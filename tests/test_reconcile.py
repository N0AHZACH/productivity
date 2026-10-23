"""Budget assumption reconciliation tests."""

from app.agent.reconcile import (
    ensure_budget_table_totals,
    reconcile_budget_assumptions,
)
from app.models.common import DocType, TaskStatus
from app.models.document import DocumentSpec, Section, Table
from app.models.plan import Task, TaskList
from app.models.state import AgentState, Analysis


def _state(
    *,
    assumptions: list[str],
    known_facts: list[str] | None = None,
) -> AgentState:
    analysis = Analysis(
        doc_type=DocType.PROJECT_PLAN,
        objective="Launch a support chatbot.",
        audience="Executive sponsor",
        known_facts=known_facts or [],
        assumptions=assumptions,
    )
    task = Task(
        id="t1",
        title="Estimate budget",
        description="Calculate the budget.",
        kind="tool",
        tool="estimate_budget",
        acceptance="A total is returned.",
        status=TaskStatus.DONE,
        result={"total": "165000.00", "currency": "USD"},
    )
    plan = TaskList(
        objective=analysis.objective,
        doc_type=analysis.doc_type,
        tasks=[task],
    )
    return AgentState(
        run_id="reconcile1",
        request="Write a project plan with a fixed operating budget.",
        analysis=analysis,
        plan=plan,
    )


def test_inferred_budget_is_reconciled_to_tool_total() -> None:
    state = _state(
        assumptions=[
            "Specific budget amount was not provided; assuming a working budget of "
            "120,000 USD, to be confirmed before approval."
        ]
    )

    reconcile_budget_assumptions(state)

    assert state.analysis is not None
    assert len(state.analysis.assumptions) == 1
    assert "165,000.00" in state.analysis.assumptions[0]
    assert "120,000" not in state.analysis.assumptions[0]
    assert "calculated planning budget" in state.analysis.assumptions[0]


def test_user_supplied_budget_is_not_overwritten() -> None:
    state = _state(
        assumptions=["Budget not provided; assuming 120,000 USD."],
        known_facts=["Approved budget of 120,000 USD"],
    )

    reconcile_budget_assumptions(state)

    assert state.analysis is not None
    assert state.analysis.assumptions == ["Budget not provided; assuming 120,000 USD."]


def test_missing_budget_assumption_is_added_from_tool_total() -> None:
    state = _state(assumptions=["Audience inferred as the executive sponsor."])

    reconcile_budget_assumptions(state)

    assert state.analysis is not None
    assert any("165,000.00" in item for item in state.analysis.assumptions)
    assert any("specific budget amount" in item.lower() for item in state.analysis.assumptions)


def test_missing_budget_table_totals_are_added() -> None:
    state = _state(assumptions=["Budget timing is assumed."])
    assert state.plan is not None
    state.plan.tasks[0].result = {
        "total": "198000.00",
        "subtotal": "180000.00",
        "contingency": "18000.00",
        "contingency_pct": "10.00",
        "currency": "USD",
    }
    state.spec = DocumentSpec(
        title="Support Pilot Plan",
        doc_type=DocType.PROJECT_PLAN,
        sections=[
            Section(
                id="s_budget",
                heading="Budget and Resourcing",
                blocks=[
                    Table(
                        columns=[
                            "Role / Item",
                            "Description",
                            "Headcount",
                            "Day Rate (USD)",
                            "Days",
                            "Line Total (USD)",
                        ],
                        rows=[
                            ["Project Manager", "Management", "1", "600.00", "60", "36000.00"],
                            ["Lead Developer", "Development", "1", "700.00", "60", "42000.00"],
                            ["Software Engineer", "Development", "2", "500.00", "60", "60000.00"],
                            ["QA Specialist", "Quality", "1", "450.00", "60", "27000.00"],
                            ["Vendor License", "Licensing", "-", "-", "-", "15000.00"],
                        ],
                    )
                ],
            )
        ],
    )

    ensure_budget_table_totals(state)

    assert isinstance(state.spec.sections[0].blocks[0], Table)
    table = state.spec.sections[0].blocks[0]
    assert table.rows[-3][0] == "Subtotal"
    assert table.rows[-3][-1] == "180,000.00"
    assert table.rows[-2][0] == "Contingency (10.00%)"
    assert table.rows[-2][-1] == "18,000.00"
    assert table.rows[-1][0] == "Total Project Budget"
    assert table.rows[-1][-1] == "198,000.00"

    ensure_budget_table_totals(state)
    assert len(table.rows) == 8


def test_failed_budget_tool_leaves_assumptions_unchanged() -> None:
    state = _state(
        assumptions=[
            "Specific budget amount was not provided; assuming a working budget of "
            "120,000 USD, to be confirmed before approval."
        ]
    )
    assert state.plan is not None
    state.plan.tasks[0].status = TaskStatus.FAILED

    reconcile_budget_assumptions(state)

    assert state.analysis is not None
    assert "120,000 USD" in state.analysis.assumptions[0]
