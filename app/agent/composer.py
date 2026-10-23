"""Assemble completed section drafts into a validated DocumentSpec."""

import json
from datetime import date

import structlog
from pydantic import BaseModel

from app.agent.text_utils import title_tokens, titles_match
from app.core.config import Settings
from app.core.errors import LLMOutputInvalid, LLMProviderError, PlanInvalid
from app.llm.base import LLMClient
from app.models.common import TaskStatus
from app.models.document import (
    Block,
    Callout,
    DocumentSpec,
    Paragraph,
    Section,
    SectionDraft,
    Table,
)
from app.models.plan import Task
from app.models.state import AgentState
from app.prompts import load_prompt
from app.tools.templates import template_titles

logger = structlog.get_logger(__name__)


class CompositionDraft(BaseModel):
    """LLM response for document title, subtitle, and executive summary."""

    title: str
    subtitle: str | None = None
    executive_summary: str
    recommendation: str | None = None


def _successful_writer_tasks(state: AgentState) -> list[Task]:
    if state.plan is None:
        return []
    return [
        task
        for task in state.plan.tasks
        if task.kind == "write_section" and task.status is TaskStatus.DONE
    ]


def _draft_for(task: Task) -> SectionDraft | None:
    try:
        return SectionDraft.model_validate(task.result)
    except Exception:
        logger.warning("invalid_section_draft_in_result", task_id=task.id)
        return None


def _ordered_drafts(state: AgentState) -> list[tuple[Task, SectionDraft]]:
    tasks = _successful_writer_tasks(state)
    drafts: list[tuple[Task, SectionDraft]] = []
    for task in tasks:
        draft = _draft_for(task)
        if draft is not None:
            drafts.append((task, draft))

    if state.analysis is None:
        return drafts
    ordered: list[tuple[Task, SectionDraft]] = []
    used: set[str] = set()
    for template_title in template_titles(state.analysis.doc_type):
        for task, draft in drafts:
            if task.id in used:
                continue
            if titles_match(task.section_title or draft.heading, template_title):
                ordered.append((task, draft))
                used.add(task.id)
                break
    ordered.extend((task, draft) for task, draft in drafts if task.id not in used)
    return ordered


def _outline(drafts: list[tuple[Task, SectionDraft]]) -> str:
    outline = []
    for task, draft in drafts:
        preview_parts: list[str] = []
        for block in draft.blocks[:4]:
            if isinstance(block, Paragraph):
                preview_parts.append(block.text)
            elif isinstance(block, Table):
                # Tables carry rankings and totals the summary must not contradict.
                if block.caption:
                    preview_parts.append(block.caption)
                preview_parts.append(" | ".join(block.columns))
                preview_parts.extend(" | ".join(row) for row in block.rows[:6])
            elif hasattr(block, "text"):
                preview_parts.append(str(block.text))
            elif hasattr(block, "items"):
                preview_parts.append("; ".join(str(item) for item in block.items))
        outline.append(
            {
                "section": draft.heading,
                "acceptance": task.acceptance,
                "preview": " ".join(preview_parts)[:500],
            }
        )
    return json.dumps(outline, indent=2, ensure_ascii=False)


def _fallback_composition(state: AgentState) -> CompositionDraft:
    doc_label = (
        state.analysis.doc_type.value.replace("_", " ").title()
        if state.analysis
        else "Business Document"
    )
    objective = state.analysis.objective if state.analysis else "Address the request."
    return CompositionDraft(
        title=doc_label,
        subtitle=objective[:140],
        executive_summary=(
            f"This document addresses the following objective: {objective} "
            "It presents the completed analysis, options, and next steps for the audience."
        ),
        recommendation=(
            state.analysis.conflicts[0] if state.analysis and state.analysis.conflicts else None
        ),
    )


async def _generate_composition(
    state: AgentState,
    drafts: list[tuple[Task, SectionDraft]],
    llm: LLMClient,
    settings: Settings,
) -> CompositionDraft:
    analysis_json = (
        json.dumps(state.analysis.model_dump(mode="json"), indent=2) if state.analysis else "{}"
    )
    messages = [
        {"role": "system", "content": load_prompt("composer")},
        {
            "role": "user",
            "content": (
                f"ANALYSIS:\n{analysis_json}\n\nCOMPLETED SECTION OUTLINE:\n{_outline(drafts)}"
            ),
        },
    ]
    try:
        return await llm.complete_json(
            messages,
            CompositionDraft,
            tier=settings.composition_tier,
            max_tokens=2048,
        )
    except (LLMOutputInvalid, LLMProviderError) as exc:
        logger.warning("composition_fallback", error=str(exc))
        state.warnings.append(f"Executive summary used deterministic fallback: {exc}")
        return _fallback_composition(state)


def _executive_summary_blocks(composition: CompositionDraft, state: AgentState) -> list[Block]:
    blocks: list[Block] = [
        Paragraph(text=composition.executive_summary.strip() or "Summary unavailable.")
    ]
    if state.analysis and state.analysis.conflicts:
        conflicts = " ".join(state.analysis.conflicts)
        recommendation = (
            composition.recommendation.strip()
            if composition.recommendation and composition.recommendation.strip()
            else (
                "The recommendation prioritizes the constraints explicitly identified in "
                "the trade-off analysis below."
            )
        )
        blocks.append(
            Callout(
                kind="decision",
                text=f"Trade-off: {conflicts} Recommendation: {recommendation}",
            )
        )
    else:
        focus = (
            composition.recommendation.strip()
            if composition.recommendation and composition.recommendation.strip()
            else f"Primary objective: {state.analysis.objective if state.analysis else ''}"
        )
        blocks.append(Callout(kind="info", text=focus))
    return blocks


async def compose(
    state: AgentState,
    llm: LLMClient,
    settings: Settings,
) -> DocumentSpec:
    """Create a renderable DocumentSpec with an executive summary first."""
    if state.analysis is None:
        raise PlanInvalid("Cannot compose a document without analysis")
    drafts = _ordered_drafts(state)
    if not drafts:
        raise PlanInvalid("No sections were successfully written; cannot compose a document")

    composition = await _generate_composition(state, drafts, llm, settings)
    title = composition.title.strip() or _fallback_composition(state).title
    sections: list[Section] = [
        Section(
            id="s_summary",
            heading="Executive Summary",
            blocks=_executive_summary_blocks(composition, state),
        )
    ]
    for task, draft in drafts:
        sections.append(Section(id=f"s_{task.id}", heading=draft.heading, blocks=draft.blocks))

    assumption_text = state.analysis.assumptions
    spec = DocumentSpec(
        title=title,
        subtitle=(composition.subtitle or "").strip() or None,
        doc_type=state.analysis.doc_type,
        metadata={
            "date": date.today().isoformat(),
            "version": "1.0",
            "author": settings.default_author,
        },
        assumptions=list(assumption_text),
        sections=sections,
    )
    state.spec = spec
    return spec


def section_search_terms(heading: str) -> set[str]:
    """Public helper used by the reflector to match issue text to sections."""
    return title_tokens(heading)
