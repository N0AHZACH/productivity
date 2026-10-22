"""Deterministic review checks and reflective repair-loop tests."""

from typing import Any

import pytest

from app.agent.checks import MISSING_SECTION_PREFIX, run_checks
from app.agent.reflector import CriticReview, reflect
from app.core.config import Settings
from app.llm.fake import FakeLLM
from app.models.common import DocType, TaskStatus
from app.models.document import (
    DocumentSpec,
    Paragraph,
    Section,
    SectionDraft,
    Table,
)
from app.models.plan import Task, TaskList
from app.models.state import AgentState, Analysis, Issue
from app.tools.templates import template_titles


def _analysis() -> Analysis:
    return Analysis(
        doc_type=DocType.PROJECT_PLAN,
        objective="Launch a support pilot.",
        audience="Executive team",
        known_facts=["Twelve weeks"],
        assumptions=["Budget timing is assumed."],
    )


def _budget_blocks(total: str) -> list[Any]:
    return [
        Paragraph(text="The budget contains the approved delivery estimate."),
        Table(
            columns=["Item", "Amount"],
            rows=[["Delivery", "60,000.00"], ["Total", total]],
        ),
    ]


def _budget_draft() -> SectionDraft:
    return SectionDraft(
        heading="Budget and Resourcing",
        blocks=_budget_blocks("100,000.00"),
    )


def _make_state(
    *,
    budget_total: str = "100000.00",
    table_total: str = "100,000.00",
    placeholder_title: str | None = None,
) -> AgentState:
    titles = template_titles(DocType.PROJECT_PLAN)
    tasks = [
        Task(
            id="t1",
            title="Estimate budget",
            description="Calculate the budget.",
            kind="tool",
            tool="estimate_budget",
            acceptance="A total is returned.",
            status=TaskStatus.DONE,
            result={"total": budget_total},
        )
    ]
    sections: list[Section] = []
    for index, title in enumerate(titles, start=2):
        if title == "Budget and Resourcing":
            blocks = _budget_blocks(table_total)
        else:
            blocks = [
                Paragraph(text=f"{title} has substantive analysis."),
                Paragraph(text=f"{title} includes a concrete next consideration."),
            ]
        if title == placeholder_title:
            blocks[0] = Paragraph(text="TODO replace this placeholder.")
        task_id = f"t{index}"
        draft = SectionDraft(heading=title, blocks=blocks)
        tasks.append(
            Task(
                id=task_id,
                title=title,
                description=f"Write {title}.",
                kind="write_section",
                section_title=title,
                acceptance="The section is complete.",
                status=TaskStatus.DONE,
                result=draft.model_dump(mode="json"),
            )
        )
        sections.append(Section(id=f"s_{task_id}", heading=title, blocks=blocks))
    analysis = _analysis()
    plan = TaskList(
        objective=analysis.objective,
        doc_type=analysis.doc_type,
        tasks=tasks,
    )
    spec = DocumentSpec(
        title="Support Pilot Plan",
        subtitle="Twelve-week delivery",
        doc_type=analysis.doc_type,
        assumptions=["Budget timing is assumed."],
        sections=sections,
    )
    return AgentState(
        run_id="review1",
        request="Write a project plan.",
        analysis=analysis,
        plan=plan,
        spec=spec,
    )


def _settings(*, mode: str = "auto", loops: int = 2) -> Settings:
    return Settings(reflection_llm_mode=mode, max_reflection_loops=loops)


@pytest.mark.asyncio
async def test_clean_document_passes_all_checks() -> None:
    state = _make_state()
    assert state.spec is not None and state.plan is not None and state.analysis is not None
    assert run_checks(state.spec, state.plan, state.analysis) == []


def test_missing_required_section_is_detected() -> None:
    state = _make_state()
    assert state.spec is not None and state.plan is not None and state.analysis is not None
    state.spec.sections = [
        section
        for section in state.spec.sections
        if section.heading != "Risks and Mitigations"
    ]
    issues = run_checks(state.spec, state.plan, state.analysis)
    assert any(
        issue.description == f"{MISSING_SECTION_PREFIX} 'Risks and Mitigations'"
        for issue in issues
    )


def test_placeholder_and_thin_section_are_detected() -> None:
    state = _make_state(placeholder_title="Objectives and Success Criteria")
    assert state.spec is not None and state.plan is not None and state.analysis is not None
    state.spec.sections[1].blocks = [Paragraph(text="TBD")]
    issues = run_checks(state.spec, state.plan, state.analysis)
    assert any("placeholder" in issue.description for issue in issues)
    assert any("fewer than two" in issue.description for issue in issues)


def test_budget_mismatch_is_detected() -> None:
    state = _make_state(table_total="90,000.00")
    assert state.spec is not None and state.plan is not None and state.analysis is not None
    issues = run_checks(state.spec, state.plan, state.analysis)
    assert any("reconcile" in issue.description for issue in issues)
    assert any(issue.section_id == "s_t6" for issue in issues)


def test_timeline_dates_must_appear() -> None:
    state = _make_state()
    assert state.plan is not None and state.spec is not None and state.analysis is not None
    state.plan.tasks.append(
        Task(
            id="t20",
            title="Build timeline",
            description="Build a timeline.",
            kind="tool",
            tool="build_timeline",
            acceptance="Dates are returned.",
            status=TaskStatus.DONE,
            result={"start_date": "2026-01-05", "end_date": "2026-03-30"},
        )
    )
    issues = run_checks(state.spec, state.plan, state.analysis)
    assert any("2026-01-05" in issue.description for issue in issues)
    assert any("2026-03-30" in issue.description for issue in issues)


def test_conflict_terms_and_missing_assumptions_are_checked() -> None:
    state = _make_state()
    assert state.spec is not None and state.plan is not None and state.analysis is not None
    state.analysis.conflicts = ["Strict compliance and rapid delivery cannot both be maximized."]
    state.analysis.missing_info = ["Budget approval"]
    state.spec.assumptions = []
    issues = run_checks(state.spec, state.plan, state.analysis)
    assert any("conflict" in issue.description for issue in issues)
    assert any("assumptions" in issue.description for issue in issues)


def test_failed_task_is_reported() -> None:
    state = _make_state()
    assert state.spec is not None and state.plan is not None and state.analysis is not None
    budget_task = next(task for task in state.plan.tasks if task.id == "t6")
    budget_task.status = TaskStatus.FAILED
    budget_task.error = "provider timeout"
    issues = run_checks(state.spec, state.plan, state.analysis)
    assert any("provider timeout" in issue.description for issue in issues)


@pytest.mark.asyncio
async def test_reflection_off_skips_checks_and_repairs() -> None:
    state = _make_state(table_total="90,000.00")
    writer_calls: list[str] = []

    async def writer(*args: Any, **kwargs: Any) -> SectionDraft:
        writer_calls.append(str(kwargs.get("extra_feedback")))
        return _budget_draft()

    llm = FakeLLM()
    await reflect(state, llm, _settings(mode="off"), writer, lambda *args, **kwargs: None)

    assert state.reflection == []
    assert writer_calls == []
    assert llm.calls == []


@pytest.mark.asyncio
async def test_reflection_passes_clean_standard_document_without_llm() -> None:
    state = _make_state()

    async def writer(*args: Any, **kwargs: Any) -> SectionDraft:
        raise AssertionError("clean documents should not be rewritten")

    llm = FakeLLM()
    await reflect(state, llm, _settings(), writer, lambda *args, **kwargs: None)

    assert state.reflection
    assert state.reflection[-1].passed is True
    assert state.reflection[-1].fixed_sections == []
    assert llm.calls == []


@pytest.mark.asyncio
async def test_always_mode_reviews_a_clean_standard_document() -> None:
    state = _make_state()

    async def writer(*args: Any, **kwargs: Any) -> SectionDraft:
        raise AssertionError("a passing critic should not trigger repairs")

    llm = FakeLLM.with_schemas({CriticReview: {"passed": True, "issues": []}})
    await reflect(
        state, llm, _settings(mode="always"), writer, lambda *args, **kwargs: None
    )

    assert len(llm.calls) == 1
    assert state.reflection[-1].passed is True
    assert state.reflection[-1].fixed_sections == []


@pytest.mark.asyncio
async def test_reflection_repairs_only_the_budget_section() -> None:
    state = _make_state(table_total="90,000.00")
    writer_calls: list[tuple[str, str | None]] = []

    async def writer(
        task: Task, *args: Any, **kwargs: Any
    ) -> SectionDraft:
        writer_calls.append((task.id, kwargs.get("extra_feedback")))
        return _budget_draft()

    llm = FakeLLM.with_schemas({CriticReview: {"passed": False, "issues": []}})
    await reflect(state, llm, _settings(), writer, lambda *args, **kwargs: None)

    assert [task_id for task_id, _ in writer_calls] == ["t6"]
    assert writer_calls[0][1] and "reconcile" in writer_calls[0][1]
    assert state.spec is not None
    assert run_checks(state.spec, state.plan, state.analysis) == []  # type: ignore[arg-type]
    assert state.reflection[0].passed is True
    assert state.reflection[0].fixed_sections == ["s_t6"]


@pytest.mark.asyncio
async def test_reflection_reruns_failed_writer_for_missing_section() -> None:
    state = _make_state()
    assert state.spec is not None and state.plan is not None
    state.spec.sections = [
        section
        for section in state.spec.sections
        if section.heading != "Budget and Resourcing"
    ]
    budget_task = next(task for task in state.plan.tasks if task.id == "t6")
    budget_task.status = TaskStatus.FAILED
    budget_task.error = "writer failed"
    writer_calls: list[str] = []

    async def writer(task: Task, *args: Any, **kwargs: Any) -> SectionDraft:
        writer_calls.append(task.id)
        return _budget_draft()

    llm = FakeLLM.with_schemas({CriticReview: {"passed": True, "issues": []}})
    await reflect(state, llm, _settings(), writer, lambda *args, **kwargs: None)

    assert writer_calls == ["t6"]
    assert any(
        section.heading == "Budget and Resourcing" for section in state.spec.sections
    )
    assert budget_task.status is TaskStatus.DONE
    assert not any("Unresolved review issue" in warning for warning in state.warnings)


@pytest.mark.asyncio
async def test_reflection_caps_loops_and_promotes_remaining_major_issues() -> None:
    state = _make_state(placeholder_title="Objectives and Success Criteria")
    writer_calls: list[str] = []

    async def writer(task: Task, *args: Any, **kwargs: Any) -> SectionDraft:
        writer_calls.append(task.id)
        assert state.spec is not None
        section = next(
            candidate for candidate in state.spec.sections if candidate.id == f"s_{task.id}"
        )
        return SectionDraft(heading=section.heading, blocks=section.blocks)

    llm = FakeLLM.with_schemas({CriticReview: {"passed": True, "issues": []}})
    await reflect(state, llm, _settings(loops=1), writer, lambda *args, **kwargs: None)

    assert len(writer_calls) == 1
    assert state.reflection[0].passed is False
    assert any("Unresolved review issue" in warning for warning in state.warnings)
    assert any("placeholder" in warning for warning in state.warnings)


@pytest.mark.asyncio
async def test_auto_reviews_complex_documents_even_when_checks_pass() -> None:
    state = _make_state()
    assert state.analysis is not None
    state.analysis.complexity = "complex"
    writer_calls: list[str] = []

    async def writer(task: Task, *args: Any, **kwargs: Any) -> SectionDraft:
        writer_calls.append(task.id)
        heading = task.section_title or task.title
        return SectionDraft(
            heading=heading,
            blocks=[
                Paragraph(text=f"{heading} now includes a measurable target."),
                Paragraph(text=f"{heading} now states an accountable owner."),
            ],
        )

    llm = FakeLLM.with_schemas(
        {
            CriticReview: CriticReview(
                passed=False,
                issues=[
                    Issue(
                        section_id="s_t2",
                        severity="major",
                        description="Add a measurable target and an accountable owner.",
                    )
                ],
            )
        }
    )
    await reflect(state, llm, _settings(), writer, lambda *args, **kwargs: None)

    assert len(llm.calls) == 1
    assert writer_calls == ["t2"]
    assert state.reflection[0].passed is True
    assert state.reflection[0].fixed_sections == ["s_t2"]
