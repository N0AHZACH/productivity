"""Review the composed document, fix affected sections, and record warnings."""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

import structlog
from pydantic import BaseModel, Field

from app.agent.checks import (
    MISSING_SECTION_PREFIX,
    run_checks,
    section_text,
)
from app.agent.reconcile import ensure_budget_table_totals
from app.agent.text_utils import titles_match
from app.core.config import Settings
from app.core.errors import LLMOutputInvalid, LLMProviderError
from app.llm.base import LLMClient
from app.models.common import TaskStatus
from app.models.document import DocumentSpec, Section, SectionDraft
from app.models.plan import Task, TaskList
from app.models.state import AgentState, Issue, ReflectionReport
from app.prompts import load_prompt
from app.tools.templates import template_titles

logger = structlog.get_logger(__name__)
Emit = Callable[..., None]


class SectionWriter(Protocol):
    """Callable shape of the section writer used by the executor."""

    async def __call__(
        self,
        task: Task,
        state: AgentState,
        llm: LLMClient,
        settings: Settings,
        *,
        extra_feedback: str | None = None,
    ) -> SectionDraft: ...


class CriticReview(BaseModel):
    """Structured response from the optional LLM critic."""

    passed: bool = True
    issues: list[Issue] = Field(default_factory=list)


@dataclass
class _RepairTarget:
    task: Task
    section: Section | None
    issues: list[Issue] = field(default_factory=list)


def _dedupe(issues: list[Issue]) -> list[Issue]:
    seen: set[tuple[str | None, str]] = set()
    unique: list[Issue] = []
    for issue in issues:
        key = _issue_key(issue)
        if key in seen:
            continue
        seen.add(key)
        unique.append(issue)
    return unique


def _issue_key(issue: Issue) -> tuple[str | None, str]:
    return (issue.section_id, issue.description.strip())


def _critic_context(state: AgentState, spec: DocumentSpec) -> str:
    sections = [
        {
            "id": section.id,
            "heading": section.heading,
            "content": section_text(section)[:1200],
        }
        for section in spec.sections
    ]
    acceptance = []
    if state.plan is not None:
        acceptance = [
            {
                "section": task.section_title or task.title,
                "acceptance": task.acceptance,
            }
            for task in state.plan.tasks
            if task.kind == "write_section"
        ]
    return json.dumps(
        {
            "analysis": (
                state.analysis.model_dump(mode="json") if state.analysis else {}
            ),
            "acceptance_criteria": acceptance,
            "sections": sections,
        },
        indent=2,
        ensure_ascii=False,
    )


async def _critic_issues(
    state: AgentState,
    spec: DocumentSpec,
    llm: LLMClient,
    settings: Settings,
) -> list[Issue]:
    messages = [
        {"role": "system", "content": load_prompt("critic")},
        {
            "role": "user",
            "content": (
                f"DOCUMENT TO REVIEW:\n{_critic_context(state, spec)}\n\n"
                "Return one CriticReview JSON object."
            ),
        },
    ]
    review = await llm.complete_json(
        messages,
        CriticReview,
        tier=settings.reflection_tier,
        max_tokens=settings.llm_max_output_tokens,
    )
    return review.issues


def _parse_missing_title(description: str) -> str | None:
    if not description.startswith(MISSING_SECTION_PREFIX):
        return None
    return description[len(MISSING_SECTION_PREFIX) :].strip().strip("'\"")


def _find_section(spec: DocumentSpec, section_id: str) -> Section | None:
    return next((section for section in spec.sections if section.id == section_id), None)


def _find_writer_task(plan: TaskList, section_title: str) -> Task | None:
    candidates = [
        task
        for task in plan.tasks
        if task.kind == "write_section"
        and titles_match(task.section_title or task.title, section_title)
    ]
    if not candidates:
        return None
    priority = {
        TaskStatus.FAILED: 0,
        TaskStatus.SKIPPED: 1,
        TaskStatus.PENDING: 2,
        TaskStatus.RUNNING: 3,
        TaskStatus.DONE: 4,
    }
    return sorted(candidates, key=lambda task: priority.get(task.status, 99))[0]


def _resolve_target(state: AgentState, issue: Issue) -> _RepairTarget | None:
    if state.plan is None or state.spec is None:
        return None
    plan = state.plan
    spec = state.spec
    task_by_id = {task.id: task for task in plan.tasks}

    if issue.section_id:
        if issue.section_id == "s_summary":
            return None
        section = _find_section(spec, issue.section_id)
        task = task_by_id.get(issue.section_id.removeprefix("s_"))
        if task is not None and task.kind == "write_section":
            return _RepairTarget(task=task, section=section)
        if section is None:
            return None

    missing_title = _parse_missing_title(issue.description)
    if missing_title:
        section = next(
            (
                candidate
                for candidate in spec.sections
                if titles_match(candidate.heading, missing_title)
            ),
            None,
        )
        task = _find_writer_task(plan, missing_title)
        if task is not None:
            return _RepairTarget(task=task, section=section)
        if section is None:
            return None

    if issue.section_id is None:
        return None

    section = _find_section(spec, issue.section_id)
    if section is None:
        return None
    for task in plan.tasks:
        if task.kind != "write_section":
            continue
        if titles_match(task.section_title or task.title, section.heading):
            return _RepairTarget(task=task, section=section)
    return None


def _insert_section(state: AgentState, section: Section) -> None:
    if state.spec is None or state.analysis is None:
        return
    spec = state.spec
    titles = template_titles(state.analysis.doc_type)
    target_index: int | None = None
    for index, title in enumerate(titles):
        if titles_match(section.heading, title):
            target_index = index
            break
    if target_index is None:
        spec.sections.append(section)
        return

    insert_at = len(spec.sections)
    for position, existing in enumerate(spec.sections[1:], start=1):
        existing_index: int | None = None
        for index, title in enumerate(titles):
            if titles_match(existing.heading, title):
                existing_index = index
                break
        if existing_index is not None and existing_index > target_index:
            insert_at = position
            break
    spec.sections.insert(insert_at, section)


def _apply_draft(
    state: AgentState,
    target: _RepairTarget,
    draft: SectionDraft,
) -> str:
    if state.spec is None:
        raise ValueError("Cannot apply a draft without a document")
    heading = draft.heading.strip() or target.task.section_title or target.task.title
    if target.section is not None:
        target.section.heading = heading
        target.section.blocks = draft.blocks
        return target.section.id

    section = Section(id=f"s_{target.task.id}", heading=heading, blocks=draft.blocks)
    _insert_section(state, section)
    return section.id


async def _repair_target(
    target: _RepairTarget,
    state: AgentState,
    llm: LLMClient,
    settings: Settings,
    writer: SectionWriter,
) -> str | None:
    feedback = "\n".join(
        f"- {issue.description}" for issue in _dedupe(target.issues)
    )
    draft = await writer(
        target.task,
        state,
        llm,
        settings,
        extra_feedback=(
            "A reviewer found these problems in this section. Rewrite the section so "
            f"each one is resolved without changing correct facts:\n{feedback}"
        ),
    )
    target.task.result = draft.model_dump(mode="json")
    target.task.status = TaskStatus.DONE
    target.task.error = None
    target.task.attempts += 1
    failure_prefix = f"{target.task.id} ({target.task.title}) failed:"
    state.warnings = [
        warning for warning in state.warnings if not warning.startswith(failure_prefix)
    ]
    return _apply_draft(state, target, draft)


async def _run_repairs(
    major_issues: list[Issue],
    state: AgentState,
    llm: LLMClient,
    settings: Settings,
    writer: SectionWriter,
    emit: Emit,
) -> tuple[list[str], list[Issue]]:
    targets: dict[str, _RepairTarget] = {}
    unresolved: list[Issue] = []
    for issue in major_issues:
        target = _resolve_target(state, issue)
        if target is None:
            unresolved.append(issue)
            continue
        key = (
            f"section:{target.section.id}"
            if target.section is not None
            else f"task:{target.task.id}"
        )
        existing = targets.get(key)
        if existing is None:
            targets[key] = _RepairTarget(
                task=target.task, section=target.section, issues=[issue]
            )
        else:
            existing.issues.append(issue)

    fixed: list[str] = []
    for target in targets.values():
        try:
            section_id = await _repair_target(target, state, llm, settings, writer)
        except Exception as exc:
            logger.warning(
                "reflection_repair_failed", task_id=target.task.id, error=str(exc)
            )
            unresolved.extend(target.issues)
            continue
        if section_id is not None:
            fixed.append(section_id)
            emit(
                "review",
                f"Repaired {section_id}",
                event="section_repaired",
                section_id=section_id,
                task_id=target.task.id,
            )
    return fixed, unresolved


def _add_warning(state: AgentState, issue: Issue) -> None:
    warning = f"Unresolved review issue: {issue.description}"
    if warning not in state.warnings:
        state.warnings.append(warning)


async def reflect(
    state: AgentState,
    llm: LLMClient,
    settings: Settings,
    writer: SectionWriter,
    emit: Emit,
) -> None:
    """Review, target repairs, and repeat within the configured loop budget."""
    if settings.reflection_llm_mode == "off":
        return
    if state.spec is None or state.analysis is None or state.plan is None:
        return
    if settings.max_reflection_loops == 0:
        return

    major_after: list[Issue] = []
    unresolved_after: list[Issue] = []
    carried: list[Issue] = []
    for loop in range(1, settings.max_reflection_loops + 1):
        issues = _dedupe(
            [*run_checks(state.spec, state.plan, state.analysis), *carried]
        )
        should_review = settings.reflection_llm_mode == "always" or (
            settings.reflection_llm_mode == "auto"
            and (
                bool(issues)
                or (state.analysis.complexity or "").strip().lower() == "complex"
            )
        )
        critic_issues: list[Issue] = []
        if should_review:
            try:
                critic_issues = await _critic_issues(state, state.spec, llm, settings)
                issues.extend(critic_issues)
            except (LLMOutputInvalid, LLMProviderError) as exc:
                logger.warning("critic_unavailable", error=str(exc))
                state.warnings.append(f"LLM critic was unavailable: {exc}")
        issues = _dedupe(issues)
        major = [issue for issue in issues if issue.severity.lower() == "major"]

        if not major:
            report = ReflectionReport(loop=loop, passed=True, issues=issues)
            state.reflection.append(report)
            emit(
                "review",
                f"Review loop {loop} passed",
                event="passed",
                loop=loop,
                issues=len(issues),
            )
            return

        fixed, unresolved = await _run_repairs(
            major, state, llm, settings, writer, emit
        )
        # Repairs rewrite whole sections from model output, so a repaired budget table can
        # lose the deterministic summary rows. Re-apply reconciliation before re-checking.
        ensure_budget_table_totals(state)
        major_after = [
            issue
            for issue in run_checks(state.spec, state.plan, state.analysis)
            if issue.severity.lower() == "major"
        ]
        deterministic_keys = {_issue_key(issue) for issue in major_after}
        critic_keys = {_issue_key(issue) for issue in critic_issues}
        unresolved = [
            issue
            for issue in unresolved
            if _issue_key(issue) in deterministic_keys or _issue_key(issue) in critic_keys
        ]
        unresolved_after = unresolved
        carried = _dedupe([*major_after, *unresolved])
        passed = not carried
        report = ReflectionReport(
            loop=loop,
            passed=passed,
            issues=issues,
            fixed_sections=fixed,
        )
        state.reflection.append(report)
        emit(
            "review",
            f"Review loop {loop} completed",
            event="completed",
            loop=loop,
            passed=passed,
            issues=len(issues),
            fixed_sections=fixed,
        )
        if passed:
            return

    for issue in _dedupe(major_after + unresolved_after):
        _add_warning(state, issue)
