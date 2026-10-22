"""End-to-end agent orchestration with timeouts and trace events."""

import asyncio
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
from app.agent.reflector import reflect
from app.container import Container
from app.core.errors import AgentFailed, DocPilotError, RunTimeout
from app.core.logging import bind_run
from app.models.common import RunStatus, TaskStatus
from app.models.state import AgentState, Principal, TraceEvent

logger = structlog.get_logger(__name__)


def _emitter(state: AgentState) -> Callable[..., None]:
    def emit(stage: str, message: str, **data: Any) -> None:
        duration_ms = data.pop("duration_ms", None)
        trace = TraceEvent(
            stage=stage,
            message=message,
            duration_ms=duration_ms if isinstance(duration_ms, int) else None,
            data=data,
        )
        state.trace.append(trace)
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

    async def run(
        self,
        request: str,
        principal: Principal | None = None,
    ) -> AgentState:
        """Execute one bounded run and return its complete state."""
        settings = self.container.settings
        state = AgentState(
            run_id=uuid.uuid4().hex,
            principal=principal or Principal(),
            request=request,
        )
        emit = _emitter(state)
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

                stage_started = time.perf_counter()
                state.analysis = await analyze(
                    state.request, guardrail.flags, self.container.llm
                )
                emit(
                    "analysis",
                    "Request analyzed",
                    doc_type=state.analysis.doc_type.value,
                    complexity=state.analysis.complexity,
                    conflicts=len(state.analysis.conflicts),
                    missing_info=len(state.analysis.missing_info),
                    duration_ms=int((time.perf_counter() - stage_started) * 1000),
                )

                stage_started = time.perf_counter()
                state.plan = await plan(
                    state.analysis,
                    self.container.registry,
                    self.container.llm,
                    settings,
                )
                state.plan_source = get_plan_source()
                if state.plan_source == "fallback":
                    state.warnings.append(
                        "Planning used the deterministic fallback after invalid LLM output."
                    )
                emit(
                    "plan",
                    "Task plan ready",
                    source=state.plan_source,
                    tasks=len(state.plan.tasks),
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

                stage_started = time.perf_counter()
                spec = await compose(state, self.container.llm, settings)
                emit(
                    "compose",
                    "Document composed",
                    sections=len(spec.sections),
                    duration_ms=int((time.perf_counter() - stage_started) * 1000),
                )

                stage_started = time.perf_counter()
                await reflect(
                    state,
                    self.container.llm,
                    settings,
                    write_section,
                    emit,
                )
                emit(
                    "reflection",
                    "Review complete",
                    loops=len(state.reflection),
                    duration_ms=int((time.perf_counter() - stage_started) * 1000),
                )

                stage_started = time.perf_counter()
                content = await asyncio.to_thread(self.container.renderer.render, spec)
                emit(
                    "render",
                    "Word document rendered",
                    bytes=len(content),
                    duration_ms=int((time.perf_counter() - stage_started) * 1000),
                )

                stage_started = time.perf_counter()
                path = await self.container.filestore.save(state.run_id, content)
                state.document_path = str(path)
                emit(
                    "store",
                    "Document stored",
                    filename=path.name,
                    bytes=len(content),
                    duration_ms=int((time.perf_counter() - stage_started) * 1000),
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
            )
            return state
        except TimeoutError as exc:
            state.status = RunStatus.FAILED
            message = f"Run exceeded the {settings.run_timeout_s:g}s timeout."
            state.warnings.append(message)
            emit("run", "Run timed out", status=RunStatus.FAILED.value, error=message)
            logger.warning("run_timeout", run_id=state.run_id, timeout_s=settings.run_timeout_s)
            raise RunTimeout(message) from exc
        except DocPilotError as exc:
            state.status = RunStatus.FAILED
            state.warnings.append(str(exc))
            emit("run", "Run failed", status=RunStatus.FAILED.value, error=str(exc))
            logger.warning("run_failed", run_id=state.run_id, error_code=exc.code)
            raise
        except Exception as exc:
            state.status = RunStatus.FAILED
            message = "Document generation failed due to an unexpected error."
            state.warnings.append(message)
            emit("run", "Run failed", status=RunStatus.FAILED.value, error=str(exc))
            logger.exception("run_crashed", run_id=state.run_id, error=str(exc))
            raise AgentFailed(message) from exc


async def run_agent(
    request: str,
    container: Container,
    principal: Principal | None = None,
) -> AgentState:
    """Convenience wrapper for scripts and tests."""
    return await Orchestrator(container).run(request, principal)
