"""End-to-end agent orchestration with timeouts and trace events."""

import asyncio
import re
import time
import uuid
from collections.abc import Callable
from typing import Any

import structlog

from app.agent.analyzer import analyze
from app.agent.composer import compose
from app.agent.executor import execute, write_section
from app.agent.guardrails import check_request
from app.agent.planner import get_plan_source, plan
from app.agent.reconcile import (
    ensure_budget_table_totals,
    reconcile_budget_assumptions,
)
from app.agent.reflector import reflect
from app.container import Container
from app.core.errors import AgentFailed, DocPilotError, RunTimeout
from app.core.logging import bind_run
from app.infra.cache import (
    CachedPlan,
    HotRunSnapshot,
    hot_run_cache_key,
    plan_cache_key,
    request_cache_key,
)
from app.infra.events import EventBus
from app.llm.metadata import llm_label
from app.models.common import RunStatus, TaskStatus
from app.models.plan import TaskList
from app.models.state import AgentState, Analysis, Principal, TraceEvent

logger = structlog.get_logger(__name__)


def _document_filename(title: str, run_id: str) -> str:
    """Create a readable, collision-resistant download filename."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")
    slug = slug[:72].strip("-") or "business-document"
    return f"{slug}-{run_id[:8]}.docx"


def _task_items(plan: TaskList) -> list[dict[str, Any]]:
    return [
        {
            "id": task.id,
            "title": task.title,
            "kind": task.kind,
            "section_title": task.section_title,
            "depends_on": task.depends_on,
            "acceptance": task.acceptance,
            "status": task.status.value,
        }
        for task in plan.tasks
    ]


def _emitter(state: AgentState, event_bus: EventBus) -> Callable[..., None]:
    def emit(stage: str, message: str, **data: Any) -> None:
        duration_ms = data.pop("duration_ms", None)
        trace = TraceEvent(
            stage=stage,
            message=message,
            duration_ms=duration_ms if isinstance(duration_ms, int) else None,
            data=data,
        )
        state.trace.append(trace)
        event_bus.publish(state.run_id, trace.model_dump(mode="json"))
        log_data = dict(data)
        trace_kind = log_data.pop("event", None)
        logger.info(
            "agent_event",
            stage=stage,
            message=message,
            trace_kind=trace_kind,
            **log_data,
        )

    return emit


class Orchestrator:
    """Run the document agent from accepted request to stored .docx."""

    def __init__(self, container: Container) -> None:
        self.container = container

    async def _serve_hot_snapshot(
        self,
        state: AgentState,
        snapshot: HotRunSnapshot,
        emit: Callable[..., None],
        settings: Any,
    ) -> AgentState:
        """Replay a completed demo run without making provider calls."""
        cached = snapshot.state
        state.analysis = cached.analysis.model_copy(deep=True) if cached.analysis else None
        state.plan = cached.plan.model_copy(deep=True) if cached.plan else None
        state.spec = cached.spec.model_copy(deep=True) if cached.spec else None
        state.plan_source = cached.plan_source
        state.plan_source_detail = cached.plan_source_detail
        state.reflection = [report.model_copy(deep=True) for report in cached.reflection]
        state.warnings = list(cached.warnings)

        if state.analysis is not None:
            analysis = state.analysis
            emit(
                "analysis",
                "Request analyzed from hot cache",
                doc_type=analysis.doc_type.value,
                complexity=analysis.complexity,
                objective=analysis.objective,
                audience=analysis.audience,
                conflicts=len(analysis.conflicts),
                missing_info=len(analysis.missing_info),
                conflict_items=analysis.conflicts,
                assumption_items=analysis.assumptions,
                missing_items=analysis.missing_info,
                cached=True,
                hot_cache=True,
                duration_ms=0,
            )
        if state.plan is not None:
            emit(
                "plan",
                "Task plan replayed from hot cache",
                source=state.plan_source,
                source_detail=state.plan_source_detail,
                tasks=len(state.plan.tasks),
                task_items=_task_items(state.plan),
                cached=True,
                hot_cache=True,
                duration_ms=0,
            )
        emit("execute", "Task results replayed from hot cache", cached=True, duration_ms=0)
        if state.spec is not None:
            emit(
                "compose",
                "Document draft replayed from hot cache",
                sections=len(state.spec.sections),
                cached=True,
                duration_ms=0,
            )
        emit(
            "reflection",
            "Self-check replayed from hot cache",
            loops=len(state.reflection),
            cached=True,
            duration_ms=0,
        )
        if state.spec is None:
            raise AgentFailed("Hot cache snapshot did not contain a document specification")

        state.document_filename = _document_filename(state.spec.title, state.run_id)
        path = await self.container.filestore.save_named(
            state.run_id, snapshot.document, state.document_filename
        )
        state.document_path = str(path)
        emit(
            "render",
            "Word document replayed from hot cache",
            bytes=len(snapshot.document),
            duration_ms=0,
        )
        emit(
            "store",
            "Document stored",
            filename=state.document_filename,
            bytes=len(snapshot.document),
            duration_ms=0,
        )
        state.status = (
            RunStatus.COMPLETED_WITH_WARNINGS if state.warnings else RunStatus.COMPLETED
        )
        emit(
            "run",
            "Run completed",
            status=state.status.value,
            warnings=len(state.warnings),
            warning_items=state.warnings,
            plan_source=state.plan_source,
            plan_source_detail=state.plan_source_detail,
            reflection_passed=all(report.passed for report in state.reflection)
            if state.reflection
            else None,
            document_url=f"/documents/{state.run_id}",
            filename=state.document_filename,
            hot_cache=True,
        )
        return state

    async def run(
        self,
        request: str,
        principal: Principal | None = None,
        *,
        run_id: str | None = None,
    ) -> AgentState:
        """Execute one bounded run and return its complete state."""
        settings = self.container.settings
        state = AgentState(
            run_id=run_id or uuid.uuid4().hex,
            principal=principal or Principal(),
            request=request,
        )
        event_bus = self.container.event_bus
        event_bus.open_run(state.run_id)
        cache = self.container.cache if settings.cache_enabled else None
        emit = _emitter(state, event_bus)
        bind_run(state.run_id)
        state.status = RunStatus.RUNNING
        emit("run", "Run started", request_chars=len(request))

        try:
            async with asyncio.timeout(settings.run_timeout_s):
                stage_started = time.perf_counter()
                guardrail = check_request(request, settings)
                state.request = guardrail.sanitized
                if guardrail.flags:
                    state.warnings.append(
                        "Guardrail flags: " + ", ".join(guardrail.flags)
                    )
                emit(
                    "guardrails",
                    "Request validated",
                    flags=guardrail.flags,
                    duration_ms=int((time.perf_counter() - stage_started) * 1000),
                )

                hot_cache = self.container.hot_cache if settings.hot_cache_enabled else None
                hot_snapshot = (
                    hot_cache.get(hot_run_cache_key(state.request))
                    if hot_cache is not None
                    else None
                )
                if isinstance(hot_snapshot, HotRunSnapshot):
                    return await self._serve_hot_snapshot(state, hot_snapshot, emit, settings)

                stage_started = time.perf_counter()
                analysis_key = request_cache_key(state.request, guardrail.flags)
                cached_analysis = cache.get(analysis_key) if cache is not None else None
                analysis_cached = isinstance(cached_analysis, Analysis)
                if isinstance(cached_analysis, Analysis):
                    analysis = cached_analysis
                else:
                    analysis = await analyze(
                        state.request,
                        guardrail.flags,
                        self.container.llm,
                        settings,
                    )
                    if cache is not None:
                        cache.set(analysis_key, analysis)
                state.analysis = analysis
                emit(
                    "analysis",
                    "Request analyzed from cache" if analysis_cached else "Request analyzed",
                    doc_type=analysis.doc_type.value,
                    complexity=analysis.complexity,
                    objective=analysis.objective,
                    audience=analysis.audience,
                    conflicts=len(analysis.conflicts),
                    missing_info=len(analysis.missing_info),
                    conflict_items=analysis.conflicts,
                    assumption_items=analysis.assumptions,
                    missing_items=analysis.missing_info,
                    cached=analysis_cached,
                    duration_ms=int((time.perf_counter() - stage_started) * 1000),
                )

                stage_started = time.perf_counter()
                plan_key = plan_cache_key(analysis, self.container.registry, settings)
                cached_plan = cache.get(plan_key) if cache is not None else None
                plan_cached = isinstance(cached_plan, CachedPlan)
                if isinstance(cached_plan, CachedPlan):
                    plan_model = cached_plan.plan
                    plan_source = cached_plan.source
                else:
                    plan_model = await plan(
                        analysis,
                        self.container.registry,
                        self.container.llm,
                        settings,
                    )
                    plan_source = get_plan_source()
                    # Never cache the degraded fallback plan: a later run should retry
                    # the LLM once the provider is healthy again.
                    if cache is not None and plan_source == "llm":
                        cache.set(plan_key, CachedPlan(plan_model, plan_source))
                state.plan = plan_model
                state.plan_source = plan_source
                state.plan_source_detail = (
                    llm_label(self.container.llm, settings.planning_tier)
                    if plan_source == "llm"
                    else "deterministic fallback plan (LLM validation failed)"
                )
                if plan_source == "fallback":
                    state.warnings.append(
                        "Planning used the deterministic fallback after invalid LLM output."
                    )
                emit(
                    "plan",
                    "Task plan ready",
                    source=plan_source,
                    source_detail=state.plan_source_detail,
                    tasks=len(plan_model.tasks),
                    task_items=_task_items(plan_model),
                    cached=plan_cached,
                    duration_ms=int((time.perf_counter() - stage_started) * 1000),
                )

                stage_started = time.perf_counter()
                await execute(
                    state,
                    self.container.registry,
                    self.container.llm,
                    settings,
                    emit,
                )
                completed = sum(
                    1 for task in state.plan.tasks if task.status is TaskStatus.DONE
                )
                emit(
                    "execute",
                    "Task execution finished",
                    completed=completed,
                    total=len(state.plan.tasks),
                    duration_ms=int((time.perf_counter() - stage_started) * 1000),
                )

                reconcile_budget_assumptions(state)

                stage_started = time.perf_counter()
                spec = await compose(state, self.container.llm, settings)
                ensure_budget_table_totals(state)
                if state.analysis is not None:
                    analysis = state.analysis
                    emit(
                        "reconcile",
                        "Assumptions reconciled with tool results",
                        doc_type=analysis.doc_type.value,
                        complexity=analysis.complexity,
                        objective=analysis.objective,
                        audience=analysis.audience,
                        conflicts=len(analysis.conflicts),
                        missing_info=len(analysis.missing_info),
                        conflict_items=analysis.conflicts,
                        assumption_items=analysis.assumptions,
                        missing_items=analysis.missing_info,
                        cached=analysis_cached,
                    )
                emit(
                    "compose",
                    "Document composed",
                    sections=len(spec.sections),
                    duration_ms=int((time.perf_counter() - stage_started) * 1000),
                )

                stage_started = time.perf_counter()
                reflection_timed_out = False
                try:
                    async with asyncio.timeout(settings.reflection_timeout_s):
                        await reflect(
                            state,
                            self.container.llm,
                            settings,
                            write_section,
                            emit,
                        )
                except TimeoutError:
                    reflection_timed_out = True
                    message = (
                        "Reflection exceeded its time budget; rendered the current draft "
                        "with unresolved review items."
                    )
                    state.warnings.append(message)
                    emit("reflection", "Review timed out", status="timed_out")
                if not reflection_timed_out:
                    emit(
                        "reflection",
                        "Review complete",
                        loops=len(state.reflection),
                        duration_ms=int((time.perf_counter() - stage_started) * 1000),
                    )

                # A timed-out or partially applied review can still leave a rewritten
                # budget table behind; the last write before rendering must reconcile.
                ensure_budget_table_totals(state)

                stage_started = time.perf_counter()
                content = await asyncio.to_thread(self.container.renderer.render, spec)
                emit(
                    "render",
                    "Word document rendered",
                    bytes=len(content),
                    duration_ms=int((time.perf_counter() - stage_started) * 1000),
                )

                stage_started = time.perf_counter()
                state.document_filename = _document_filename(spec.title, state.run_id)
                path = await self.container.filestore.save_named(
                    state.run_id, content, state.document_filename
                )
                state.document_path = str(path)
                emit(
                    "store",
                    "Document stored",
                    filename=state.document_filename,
                    bytes=len(content),
                    duration_ms=int((time.perf_counter() - stage_started) * 1000),
                )
                if hot_cache is not None:
                    hot_cache.set(
                        hot_run_cache_key(state.request),
                        HotRunSnapshot(state.model_copy(deep=True), content),
                    )

            state.status = (
                RunStatus.COMPLETED_WITH_WARNINGS
                if state.warnings
                else RunStatus.COMPLETED
            )
            emit(
                "run",
                "Run completed",
                status=state.status.value,
                warnings=len(state.warnings),
                warning_items=state.warnings,
                plan_source=state.plan_source,
                plan_source_detail=state.plan_source_detail,
                reflection_passed=(
                    all(report.passed for report in state.reflection)
                    if state.reflection
                    else None
                ),
                document_url=f"/documents/{state.run_id}",
                filename=state.document_filename or f"{state.run_id}.docx",
            )
            return state
        except TimeoutError as exc:
            state.status = RunStatus.FAILED
            message = f"Run exceeded the {settings.run_timeout_s:g}s timeout."
            state.warnings.append(message)
            emit(
                "run",
                "Run timed out",
                status=RunStatus.FAILED.value,
                error=message,
                warning_items=state.warnings,
            )
            logger.warning("run_timeout", run_id=state.run_id, timeout_s=settings.run_timeout_s)
            raise RunTimeout(message) from exc
        except DocPilotError as exc:
            state.status = RunStatus.FAILED
            state.warnings.append(str(exc))
            emit(
                "run",
                "Run failed",
                status=RunStatus.FAILED.value,
                error=str(exc),
                warning_items=state.warnings,
            )
            logger.warning("run_failed", run_id=state.run_id, error_code=exc.code)
            raise
        except Exception as exc:
            state.status = RunStatus.FAILED
            message = "Document generation failed due to an unexpected error."
            state.warnings.append(message)
            emit(
                "run",
                "Run failed",
                status=RunStatus.FAILED.value,
                error=str(exc),
                warning_items=state.warnings,
            )
            logger.exception("run_crashed", run_id=state.run_id, error=str(exc))
            raise AgentFailed(message) from exc
        finally:
            event_bus.close_run(state.run_id)


async def run_agent(
    request: str,
    container: Container,
    principal: Principal | None = None,
) -> AgentState:
    """Convenience wrapper for scripts and tests."""
    return await Orchestrator(container).run(request, principal)
