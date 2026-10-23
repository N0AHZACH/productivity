"""Deterministic review checks and reflective repair-loop tests."""

from typing import Any

import pytest

from app.agent.checks import MISSING_SECTION_PREFIX, run_checks
from app.agent.reflector import CriticReview, reflect
from app.core.config import Settings
from app.llm.fake import FakeLLM
from app.models.common import DocType, TaskStatus
from app.models.document import (
    BulletList,
    DocumentSpec,
    Heading,
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


@pytest.mark.parametrize(
    ("amount", "has_conflict"),
    [("151,800.00", True), ("120,000.00", False)],
)
def test_budget_assumption_consistency(amount: str, has_conflict: bool) -> None:
    state = _make_state()
    assert state.spec is not None and state.plan is not None and state.analysis is not None
    state.spec.assumptions = [
        "Available budget was not provided; assuming a working budget of 120,000 USD."
    ]
    risks = next(
        section for section in state.spec.sections if section.heading == "Risks and Mitigations"
    )
    risks.blocks.append(
        Paragraph(text=f"The working budget expectation of USD {amount} includes contingency.")
    )

    issues = run_checks(state.spec, state.plan, state.analysis)
    budget_issues = [issue for issue in issues if "assumed budget" in issue.description]
    unsupported = [issue for issue in issues if "not supported" in issue.description]

    assert bool(budget_issues) is has_conflict
    assert bool(unsupported) is has_conflict
    if has_conflict:
        assert budget_issues[0].section_id == risks.id
        assert "151,800.00" in budget_issues[0].description
        assert unsupported[0].section_id == risks.id
        assert "151,800.00" in unsupported[0].description


def test_allocated_budget_line_is_not_treated_as_total_budget() -> None:
    state = _make_state()
    assert state.spec is not None and state.plan is not None and state.analysis is not None
    state.spec.assumptions = ["Available budget is fixed at 150,000 USD."]
    state.analysis.known_facts.append("Marketing allocation of $15,000.")
    risks = next(
        section for section in state.spec.sections if section.heading == "Risks and Mitigations"
    )
    risks.blocks.append(Paragraph(text="Marketing budget allocation: $15,000."))

    issues = run_checks(state.spec, state.plan, state.analysis)

    assert not any("assumed budget" in issue.description for issue in issues)
    assert not any("not supported" in issue.description for issue in issues)


def test_fixed_budget_without_amount_is_detected() -> None:
    state = _make_state()
    assert state.spec is not None and state.plan is not None and state.analysis is not None
    budget = next(
        section
        for section in state.spec.sections
        if section.heading == "Budget and Resourcing"
    )
    budget.blocks.append(
        Paragraph(text="Delivery will remain within the approved fixed operating budget.")
    )

    issues = run_checks(state.spec, state.plan, state.analysis)
    matches = [issue for issue in issues if "fixed/approved budget" in issue.description]

    assert matches
    assert matches[0].section_id == budget.id


def test_fixed_budget_with_quantified_assumption_is_clear() -> None:
    state = _make_state()
    assert state.spec is not None and state.plan is not None and state.analysis is not None
    state.spec.assumptions = ["The fixed operating budget is USD 100,000."]
    budget = next(
        section
        for section in state.spec.sections
        if section.heading == "Budget and Resourcing"
    )
    budget.blocks.append(
        Paragraph(text="Delivery will remain within the approved fixed operating budget.")
    )

    issues = run_checks(state.spec, state.plan, state.analysis)

    assert not any("fixed/approved budget" in issue.description for issue in issues)


def test_fixed_budget_with_quantified_statement_is_clear() -> None:
    state = _make_state()
    assert state.spec is not None and state.plan is not None and state.analysis is not None
    budget = next(
        section
        for section in state.spec.sections
        if section.heading == "Budget and Resourcing"
    )
    budget.blocks.append(
        Paragraph(
            text=(
                "The approved fixed operating budget is USD 100,000, "
                "to be confirmed by the sponsor."
            )
        )
    )

    issues = run_checks(state.spec, state.plan, state.analysis)

    assert not any("fixed/approved budget" in issue.description for issue in issues)


def test_budget_fixed_at_amount_is_clear() -> None:
    state = _make_state()
    assert state.spec is not None and state.plan is not None and state.analysis is not None
    budget = next(
        section
        for section in state.spec.sections
        if section.heading == "Budget and Resourcing"
    )
    budget.blocks.append(
        Paragraph(text="The operating budget is fixed at USD 100,000.")
    )

    issues = run_checks(state.spec, state.plan, state.analysis)

    assert not any("fixed/approved budget" in issue.description for issue in issues)


def test_budget_total_check_ignores_non_budget_tables() -> None:
    state = _make_state()
    assert state.spec is not None and state.plan is not None and state.analysis is not None
    budget = next(
        section
        for section in state.spec.sections
        if section.heading == "Budget and Resourcing"
    )
    budget.blocks.insert(
        0,
        Table(
            columns=["Name", "Role", "Day Rate (USD)", "Allocation (%)"],
            rows=[["Alex", "Project Manager", "950", "100"]],
        ),
    )

    issues = run_checks(state.spec, state.plan, state.analysis)

    assert not any("reconcile" in issue.description for issue in issues)


def test_unquantified_success_criteria_are_detected() -> None:
    state = _make_state()
    assert state.spec is not None and state.plan is not None and state.analysis is not None
    objectives = next(
        section
        for section in state.spec.sections
        if section.heading == "Objectives and Success Criteria"
    )
    objectives.blocks = [
        Paragraph(text="The plan aims to improve support."),
        Heading(level=3, text="Measurable Success Criteria"),
        BulletList(
            items=[
                "Increase customer satisfaction.",
                "Reduce response time by 30%.",
            ]
        ),
    ]

    issues = run_checks(state.spec, state.plan, state.analysis)
    criteria = [issue for issue in issues if "unquantified target" in issue.description]

    assert len(criteria) == 1
    assert criteria[0].section_id == objectives.id
    assert "Increase customer satisfaction." in criteria[0].description
    assert "Reduce response time by 30%." not in criteria[0].description


def test_success_criteria_intro_is_not_treated_as_criterion() -> None:
    state = _make_state()
    assert state.spec is not None and state.plan is not None and state.analysis is not None
    objectives = next(
        section
        for section in state.spec.sections
        if section.heading == "Objectives and Success Criteria"
    )
    objectives.blocks = [
        Paragraph(
            text=(
                "The primary objective is to successfully launch the chatbot, "
                "and the following measurable outcomes have been established:"
            )
        ),
        BulletList(
            items=[
                "Achieve a containment rate of at least 70% within 30 days.",
                "Maintain a CSAT of at least 80% for 30 days.",
            ]
        ),
    ]

    issues = run_checks(state.spec, state.plan, state.analysis)

    assert not any("unquantified target" in issue.description for issue in issues)


def test_qualitative_success_criteria_are_detected() -> None:
    state = _make_state()
    assert state.spec is not None and state.plan is not None and state.analysis is not None
    objectives = next(
        section
        for section in state.spec.sections
        if section.heading == "Objectives and Success Criteria"
    )
    objectives.blocks = [
        Paragraph(text="The plan aims to improve support."),
        Heading(level=3, text="Key Success Criteria"),
        BulletList(
            items=[
                "Adherence to all financial constraints within the fixed operating budget.",
                "Effective utilization of the five-person team.",
                "Resolution of standard retail support use cases.",
            ]
        ),
    ]

    issues = run_checks(state.spec, state.plan, state.analysis)
    criteria = [issue for issue in issues if "unquantified target" in issue.description]

    assert len(criteria) == 1
    assert criteria[0].section_id == objectives.id
    assert "Adherence" in criteria[0].description
    assert "Effective utilization" in criteria[0].description


def test_quantified_success_criteria_pass() -> None:
    state = _make_state()
    assert state.spec is not None and state.plan is not None and state.analysis is not None
    objectives = next(
        section
        for section in state.spec.sections
        if section.heading == "Objectives and Success Criteria"
    )
    objectives.blocks = [
        Paragraph(text="The plan targets measurable support improvements."),
        Heading(level=3, text="Measurable Success Criteria"),
        BulletList(
            items=[
                "Achieve a CSAT of at least 4.2/5 within 30 days.",
                "Reduce average response time by 30%.",
            ]
        ),
    ]

    issues = run_checks(state.spec, state.plan, state.analysis)

    assert not any("unquantified target" in issue.description for issue in issues)


def test_team_and_budget_role_mismatch_is_detected() -> None:
    state = _make_state()
    assert state.spec is not None and state.plan is not None and state.analysis is not None
    workstream = next(
        section
        for section in state.spec.sections
        if section.heading == "Workstreams and Governance"
    )
    workstream.blocks = [
        Paragraph(text="The team owns delivery."),
        Table(
            columns=["Team Member", "Role", "Workstream"],
            rows=[
                ["Alex", "Project Manager", "Governance"],
                ["Sam", "Data Engineer", "Data"],
            ],
        ),
    ]
    budget = next(
        section
        for section in state.spec.sections
        if section.heading == "Budget and Resourcing"
    )
    budget.blocks = [
        Paragraph(text="The budget reconciles to the tool total."),
        Table(
            columns=["Role", "Headcount"],
            rows=[
                ["Project Manager", "1"],
                ["QA Specialist", "1"],
                ["Total", "100,000"],
            ],
        ),
    ]

    issues = run_checks(state.spec, state.plan, state.analysis)
    role_issues = [issue for issue in issues if "do not align" in issue.description]

    assert role_issues
    assert role_issues[0].section_id == workstream.id
    assert "Data Engineer" in role_issues[0].description
    assert "QA Specialist" in role_issues[0].description


def test_owner_role_in_parentheses_is_checked() -> None:
    state = _make_state()
    assert state.spec is not None and state.plan is not None and state.analysis is not None
    workstream = next(
        section
        for section in state.spec.sections
        if section.heading == "Workstreams and Governance"
    )
    workstream.blocks = [
        Table(
            columns=["Workstream", "Lead Owner"],
            rows=[
                ["Requirements", "Jordan Brown (Business Analyst)"],
                ["Architecture", "Taylor Patel (Solution Architect)"],
            ],
        )
    ]
    budget = next(
        section
        for section in state.spec.sections
        if section.heading == "Budget and Resourcing"
    )
    budget.blocks = [
        Table(
            columns=["Role", "Headcount"],
            rows=[
                ["Project Manager", "1"],
                ["QA Specialist", "1"],
                ["Total", "100,000"],
            ],
        )
    ]

    issues = run_checks(state.spec, state.plan, state.analysis)
    role_issues = [issue for issue in issues if "do not align" in issue.description]

    assert role_issues
    assert "Business Analyst" in role_issues[0].description
    assert "QA Specialist" in role_issues[0].description


def test_team_and_budget_roles_align() -> None:
    state = _make_state()
    assert state.spec is not None and state.plan is not None and state.analysis is not None
    workstream = next(
        section
        for section in state.spec.sections
        if section.heading == "Workstreams and Governance"
    )
    workstream.blocks = [
        Paragraph(text="The team owns delivery."),
        Table(
            columns=["Team Member", "Role", "Workstream"],
            rows=[
                ["Alex", "Project Manager", "Governance"],
                ["Sam", "QA Specialist", "Quality"],
            ],
        ),
    ]
    budget = next(
        section
        for section in state.spec.sections
        if section.heading == "Budget and Resourcing"
    )
    budget.blocks = [
        Paragraph(text="The budget reconciles to the tool total."),
        Table(
            columns=["Role", "Headcount"],
            rows=[
                ["Project Manager", "1"],
                ["QA Specialist", "1"],
                ["Total", "100,000"],
            ],
        ),
    ]

    issues = run_checks(state.spec, state.plan, state.analysis)

    assert not any("do not align" in issue.description for issue in issues)


def test_team_role_mapping_satisfies_budget_alignment() -> None:
    state = _make_state()
    assert state.spec is not None and state.plan is not None and state.analysis is not None
    workstream = next(
        section
        for section in state.spec.sections
        if section.heading == "Workstreams and Governance"
    )
    workstream.blocks = [
        Paragraph(text="The table maps roster roles to operational roles."),
        Table(
            columns=["Team Member", "Roster Role", "Operational Role"],
            rows=[
                ["Alex", "Project Manager", "Project Manager"],
                ["Sam", "Data Engineer", "QA Specialist"],
            ],
        ),
    ]
    budget = next(
        section
        for section in state.spec.sections
        if section.heading == "Budget and Resourcing"
    )
    budget.blocks = [
        Paragraph(text="The budget reconciles to the tool total."),
        Table(
            columns=["Category", "Description", "Headcount"],
            rows=[
                ["Role", "Project Manager", "1"],
                ["Role", "QA Specialist", "1"],
                ["Total", "Total Budget", "100,000"],
            ],
        ),
    ]

    issues = run_checks(state.spec, state.plan, state.analysis)

    assert not any("do not align" in issue.description for issue in issues)


def test_parenthetical_budget_roles_align_with_team_roles() -> None:
    state = _make_state()
    assert state.spec is not None and state.plan is not None and state.analysis is not None
    workstream = next(
        section
        for section in state.spec.sections
        if section.heading == "Workstreams and Governance"
    )
    workstream.blocks = [
        Paragraph(text="The team owns delivery."),
        Table(
            columns=["Team Member", "Role", "Workstream"],
            rows=[
                ["Alex", "Project Manager", "Governance"],
                ["Sam", "Business Analyst / QA Specialist", "Quality"],
                ["Riley", "Solution Architect", "Architecture"],
                ["Casey", "Software Engineer", "Development"],
                ["Jordan", "Data Engineer", "Data"],
            ],
        ),
    ]
    budget = next(
        section
        for section in state.spec.sections
        if section.heading == "Budget and Resourcing"
    )
    budget.blocks = [
        Paragraph(text="The budget reconciles to the tool total."),
        Table(
            columns=["Description", "Headcount"],
            rows=[
                ["Project Manager", "1"],
                ["Lead Developer (Solution Architect)", "1"],
                ["Chatbot Engineer (Software Engineer)", "1"],
                ["QA Specialist", "1"],
                ["Operations Lead (Data Engineer)", "1"],
                ["Total Project Budget", "100,000"],
            ],
        ),
    ]

    issues = run_checks(state.spec, state.plan, state.analysis)

    assert not any("do not align" in issue.description for issue in issues)


def test_workstream_mapping_ignores_budget_roster_table() -> None:
    state = _make_state()
    assert state.spec is not None and state.plan is not None and state.analysis is not None
    workstream = next(
        section
        for section in state.spec.sections
        if section.heading == "Workstreams and Governance"
    )
    workstream.blocks = [
        Table(
            columns=["Team Member", "Role", "Budget Role"],
            rows=[
                ["Alex", "Project Manager", "Project Manager"],
                ["Sam", "Data Engineer", "QA Specialist"],
            ],
        )
    ]
    budget = next(
        section
        for section in state.spec.sections
        if section.heading == "Budget and Resourcing"
    )
    budget.blocks = [
        Table(
            columns=["Name", "Role", "Day Rate (USD)"],
            rows=[["Alex", "Project Manager", "950"], ["Sam", "Data Engineer", "900"]],
        ),
        Table(
            columns=["Role", "Headcount", "Line Total (USD)"],
            rows=[
                ["Project Manager", "1", "60,000.00"],
                ["QA Specialist", "1", "40,000.00"],
            ],
        ),
    ]

    issues = run_checks(state.spec, state.plan, state.analysis)

    assert not any("do not align" in issue.description for issue in issues)


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


def test_timeline_dates_are_not_required_without_a_schedule_section() -> None:
    state = _make_state()
    assert state.plan is not None and state.spec is not None and state.analysis is not None
    state.spec.sections = [
        section
        for section in state.spec.sections
        if "timeline" not in section.heading.lower()
    ]
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

    assert not any("2026-01-05" in issue.description for issue in issues)
    assert not any("2026-03-30" in issue.description for issue in issues)


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
async def test_repair_that_drops_budget_totals_is_reconciled_again() -> None:
    state = _make_state(budget_total="110000.00", table_total="90,000.00")
    assert state.plan is not None
    budget_tool = next(task for task in state.plan.tasks if task.tool == "estimate_budget")
    budget_tool.result = {
        "total": "110000.00",
        "subtotal": "100000.00",
        "contingency": "10000.00",
        "contingency_pct": "10.00",
        "currency": "USD",
    }

    async def writer(*args: Any, **kwargs: Any) -> SectionDraft:
        # A rewrite that keeps the line items but drops the summary rows.
        return SectionDraft(
            heading="Budget and Resourcing",
            blocks=[
                Paragraph(text="The budget reconciles to the tool total."),
                Table(
                    columns=["Item", "Amount"],
                    rows=[["Delivery", "60000.00"], ["Platform", "40000.00"]],
                ),
            ],
        )

    llm = FakeLLM.with_schemas({CriticReview: {"passed": True, "issues": []}})
    await reflect(state, llm, _settings(), writer, lambda *args, **kwargs: None)

    assert state.spec is not None
    budget = next(
        section for section in state.spec.sections if section.heading == "Budget and Resourcing"
    )
    table = next(block for block in budget.blocks if isinstance(block, Table))
    assert [row[0] for row in table.rows[-3:]] == [
        "Subtotal",
        "Contingency (10.00%)",
        "Total Project Budget",
    ]
    assert table.rows[-1][-1] == "110,000.00"
    assert state.reflection[-1].passed is True
    assert not any("Unresolved review issue" in warning for warning in state.warnings)


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
    state.warnings.append("t6 (Budget and Resourcing) failed: writer failed")
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
    assert not any("t6 (Budget and Resourcing) failed" in warning for warning in state.warnings)


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
async def test_unresolved_critic_issue_is_not_lost_on_later_clean_loop() -> None:
    state = _make_state()

    async def writer(*args: Any, **kwargs: Any) -> SectionDraft:
        raise AssertionError("a document-wide issue has no section to rewrite")

    issue = Issue(
        section_id=None,
        severity="major",
        description="The document omits the required approval signature block.",
    )
    llm = FakeLLM.with_schemas(
        {CriticReview: {"passed": False, "issues": [issue.model_dump(mode="json")]}}
    )
    await reflect(
        state, llm, _settings(mode="always", loops=2), writer, lambda *args, **kwargs: None
    )

    assert len(state.reflection) == 2
    assert all(report.passed is False for report in state.reflection)
    assert any("approval signature block" in warning for warning in state.warnings)


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
                Paragraph(text=f"{heading} now includes a measurable 95% target."),
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
