"""Parallel task execution with retries, correction, and graceful degradation."""

import asyncio
import json
import time
from collections.abc import Callable
from datetime import date
from typing import Any

import structlog
from pydantic import BaseModel, Field

from app.core.config import Settings
from app.core.errors import PlanInvalid, ToolError
from app.llm.base import LLMClient
from app.models.common import RunStatus, TaskStatus
from app.models.document import SectionDraft
from app.models.plan import Task, TaskList
from app.models.state import AgentState
from app.prompts import load_prompt
from app.tools.registry import ToolRegistry

logger = structlog.get_logger(__name__)
Emit = Callable[..., None]


class AnalysisNote(BaseModel):
    """Result of an analysis-kind task."""

    note: str


class CorrectedToolArgs(BaseModel):
    """LLM repair response for invalid tool arguments."""

    tool_args: dict[str, Any] = Field(default_factory=dict)


def _analysis_json(state: AgentState) -> str:
    if state.analysis is None:
        return "{}"
    return json.dumps(state.analysis.model_dump(mode="json"), indent=2, ensure_ascii=False)


def _compact(value: Any, *, text_limit: int = 3000) -> Any:
    if isinstance(value, str):
        if len(value) > text_limit:
            return value[:text_limit] + "... [truncated]"
        return value
    if isinstance(value, list):
        return [_compact(item, text_limit=text_limit) for item in value]
    if isinstance(value, dict):
        return {str(key): _compact(item, text_limit=text_limit) for key, item in value.items()}
    return value


def _dependency_context(plan: TaskList, task: Task) -> dict[str, Any]:
    by_id = {candidate.id: candidate for candidate in plan.tasks}
    context: dict[str, Any] = {}
    for dependency_id in task.depends_on:
        dependency = by_id.get(dependency_id)
        if dependency is None:
            continue
        context[dependency_id] = {
            "title": dependency.title,
            "kind": dependency.kind,
            "result": _compact(dependency.result),
        }
    serialized = json.dumps(context, ensure_ascii=False)
    if len(serialized) > 24_000:
        return {
            "_truncated": True,
            "note": "Dependency results exceeded the context limit; use the supplied summary.",
            "results": {key: {"title": value["title"]} for key, value in context.items()},
        }
    return context


async def write_section(
    task: Task,
    state: AgentState,
    llm: LLMClient,
    settings: Settings,
    *,
    extra_feedback: str | None = None,
) -> SectionDraft:
    """Write one section using dependency results and optional reviewer feedback."""
    if state.plan is None:
        raise PlanInvalid("Cannot write a section without a plan")
    context = _dependency_context(state.plan, task)
    feedback = (
        f"\n\nREVIEWER FEEDBACK (address this explicitly):\n{extra_feedback}"
        if extra_feedback
        else ""
    )
    messages = [
        {"role": "system", "content": load_prompt("writer")},
        {
            "role": "user",
            "content": (
                f"CURRENT DATE: {date.today().isoformat()}\n\n"
                f"DOCUMENT ANALYSIS:\n{_analysis_json(state)}\n\n"
                f"SECTION TITLE: {task.section_title or task.title}\n"
                f"TASK DESCRIPTION:\n{task.description}\n\n"
                f"ACCEPTANCE CRITERION:\n{task.acceptance}\n\n"
                f"DEPENDENCY RESULTS (source of truth):\n"
                f"{json.dumps(context, indent=2, ensure_ascii=False)}"
                f"{feedback}\n\n"
                "Return one SectionDraft JSON object."
            ),
        },
    ]
    draft = await llm.complete_json(
        messages,
        SectionDraft,
        tier=settings.writing_tier,
        temperature=0.25,
        max_tokens=settings.llm_max_output_tokens,
    )
    if not draft.heading.strip():
        draft.heading = task.section_title or task.title
    return draft


async def _run_tool_task(
    task: Task,
    state: AgentState,
    registry: ToolRegistry,
    llm: LLMClient,
    settings: Settings,
) -> dict[str, Any]:
    tool_name = task.tool or ""
    for attempt in range(2):
        task.attempts += 1
        try:
            return await registry.call(tool_name, task.tool_args)
        except ToolError as exc:
            if attempt == 1:
                raise
            logger.warning("tool_args_correction_requested", task_id=task.id, error=str(exc))
            args_model = registry.args_model(tool_name)
            messages = [
                {
                    "role": "system",
                    "content": (
                        "Correct invalid tool arguments. Return JSON with exactly one key, "
                        "tool_args, whose value validates against the supplied schema. "
                        "Do not change the tool."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Tool: {tool_name}\n"
                        "Arguments schema:\n"
                        f"{json.dumps(args_model.model_json_schema(), indent=2)}\n"
                        f"Original arguments:\n{json.dumps(task.tool_args, indent=2)}\n"
                        f"Validation/execution error:\n{exc}\n"
                        f"Document analysis:\n{_analysis_json(state)}"
                    ),
                },
            ]
            corrected = await llm.complete_json(
                messages,
                CorrectedToolArgs,
                tier=settings.correction_tier,
                max_tokens=2048,
            )
            task.tool_args = corrected.tool_args
    raise ToolError(f"Tool {tool_name!r} failed after correction")


async def _run_analysis_task(
    task: Task,
    state: AgentState,
    llm: LLMClient,
    settings: Settings,
) -> dict[str, Any]:
    if state.plan is None:
        raise PlanInvalid("Cannot run analysis without a plan")
    context = _dependency_context(state.plan, task)
    messages = [
        {
            "role": "system",
            "content": (
                "You are performing a short reasoning step for a document plan. Return "
                "JSON with one key, note: a compact, factual reasoning note for later "
                "section writers. Do not draft prose for the document."
            ),
        },
        {
            "role": "user",
            "content": (
                f"ANALYSIS:\n{_analysis_json(state)}\n\n"
                f"TASK: {task.title}\n{task.description}\n\n"
                f"DEPENDENCY RESULTS:\n{json.dumps(context, indent=2, ensure_ascii=False)}"
            ),
        },
    ]
    last_error: Exception | None = None
    for _ in range(2):
        task.attempts += 1
        try:
            result = await llm.complete_json(
                messages,
                AnalysisNote,
                tier=settings.analysis_tier,
                max_tokens=2048,
            )
            return {"note": result.note}
        except Exception as exc:
            last_error = exc
    raise last_error or PlanInvalid(f"Analysis task {task.id} produced no note")


async def _run_task(
    task: Task,
    state: AgentState,
    registry: ToolRegistry,
    llm: LLMClient,
    settings: Settings,
    emit: Emit,
) -> None:
    if state.plan is None:
        raise PlanInvalid("Cannot execute without a plan")
    by_id = {candidate.id: candidate for candidate in state.plan.tasks}
    blocked = [
        dependency_id
        for dependency_id in task.depends_on
        if by_id.get(dependency_id) is None or by_id[dependency_id].status is not TaskStatus.DONE
    ]
    if blocked:
        task.status = TaskStatus.SKIPPED
        task.error = f"Skipped because dependencies did not complete: {', '.join(blocked)}"
        state.warnings.append(f"{task.id} ({task.title}) skipped: {task.error}")
        emit(
            "task",
            f"Skipped {task.id}: {task.title}",
            event="skipped",
            task_id=task.id,
            title=task.title,
            kind=task.kind,
            dependencies=blocked,
        )
        return

    task.status = TaskStatus.RUNNING
    task.error = None
    started = time.perf_counter()
    emit(
        "task",
        f"Started {task.id}: {task.title}",
        event="started",
        task_id=task.id,
        title=task.title,
        kind=task.kind,
    )
    try:
        if task.kind == "tool":
            task.result = await _run_tool_task(task, state, registry, llm, settings)
        elif task.kind == "analysis":
            task.result = await _run_analysis_task(task, state, llm, settings)
        else:
            last_error: Exception | None = None
            draft: SectionDraft | None = None
            for attempt in range(2):
                task.attempts += 1
                feedback = (
                    f"The previous attempt failed validation: {last_error}. "
                    "Return a corrected SectionDraft."
                    if last_error and attempt > 0
                    else None
                )
                try:
                    draft = await write_section(task, state, llm, settings, extra_feedback=feedback)
                    break
                except Exception as exc:
                    last_error = exc
                    logger.warning(
                        "section_attempt_failed",
                        task_id=task.id,
                        attempt=task.attempts,
                        error=str(exc),
                    )
            if draft is None:
                raise last_error or PlanInvalid(f"Task {task.id} produced no section")
            task.result = draft.model_dump(mode="json")
        task.status = TaskStatus.DONE
        duration_ms = int((time.perf_counter() - started) * 1000)
        emit(
            "task",
            f"Finished {task.id}: {task.title}",
            event="finished",
            task_id=task.id,
            title=task.title,
            kind=task.kind,
            status=task.status.value,
            duration_ms=duration_ms,
        )
    except Exception as exc:
        task.status = TaskStatus.FAILED
        task.error = str(exc)
        state.warnings.append(f"{task.id} ({task.title}) failed: {exc}")
        duration_ms = int((time.perf_counter() - started) * 1000)
        emit(
            "task",
            f"Failed {task.id}: {task.title}",
            event="failed",
            task_id=task.id,
            title=task.title,
            kind=task.kind,
            status=task.status.value,
            duration_ms=duration_ms,
            error=str(exc),
        )
        logger.exception("task_failed", task_id=task.id, attempts=task.attempts)


async def execute(
    state: AgentState,
    registry: ToolRegistry,
    llm: LLMClient,
    settings: Settings,
    emit: Emit,
) -> None:
    """Run the task DAG as soon as each task's dependencies are terminal."""
    if state.plan is None:
        raise PlanInvalid("Cannot execute without a plan")
    semaphore = asyncio.Semaphore(settings.max_concurrency)
    state.status = RunStatus.RUNNING
    pending = {task.id: task for task in state.plan.tasks}
    terminal: set[str] = set()
    running: dict[asyncio.Task[None], Task] = {}

    async def guarded(task: Task) -> None:
        async with semaphore:
            await _run_task(task, state, registry, llm, settings, emit)

    try:
        while pending or running:
            ready = [
                task
                for task in pending.values()
                if all(dependency in terminal for dependency in task.depends_on)
            ]
            for task in ready:
                pending.pop(task.id)
                running[asyncio.create_task(guarded(task))] = task

            if not running:
                raise PlanInvalid("Task dependencies could not be scheduled")

            done, _ = await asyncio.wait(
                running, return_when=asyncio.FIRST_COMPLETED
            )
            for future in done:
                task = running.pop(future)
                terminal.add(task.id)
                future.result()
    finally:
        for future in running:
            future.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)
