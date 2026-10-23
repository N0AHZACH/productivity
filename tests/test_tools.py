"""Deterministic tool happy paths, bad inputs, and exact arithmetic."""

from datetime import date

import pytest
from pydantic import BaseModel, Field, ValidationError

from app.core.errors import ToolError
from app.models.common import DocType
from app.tools.budget import BudgetArgs
from app.tools.compare_options import CompareArgs, compare_options
from app.tools.mockdata import (
    CompanyProfileArgs,
    TeamRosterArgs,
    get_company_profile,
    get_team_roster,
)
from app.tools.registry import ToolRegistry, default_registry
from app.tools.risk import RiskArgs, generate_risk_register
from app.tools.schedule import TimelineArgs, build_timeline
from app.tools.templates import TEMPLATE_SECTIONS, TemplateArgs, get_section_template


def test_default_registry_exposes_all_tools_cleanly() -> None:
    registry = default_registry()
    specs = registry.describe()
    names = [spec.name for spec in specs]
    assert len(specs) == 7
    assert set(names) == {
        "estimate_budget",
        "build_timeline",
        "generate_risk_register",
        "compare_options",
        "get_company_profile",
        "get_team_roster",
        "get_section_template",
    }
    for spec in specs:
        assert len(spec.description) > 120
        assert spec.parameters["type"] == "object"
        assert "properties" in spec.parameters


@pytest.mark.asyncio
async def test_registry_validates_runs_and_suggests_errors() -> None:
    registry = ToolRegistry()

    class Args(BaseModel):
        value: int = Field(gt=0)

    @registry.register("double", "Double a positive integer. Use for arithmetic demos.", Args)
    def double(args: Args) -> dict[str, int]:
        return {"result": args.value * 2}

    assert await registry.call("double", {"value": 3}) == {"result": 6}
    with pytest.raises(ToolError, match="value"):
        await registry.call("double", {"value": 0})
    with pytest.raises(ToolError, match="Unknown tool"):
        await registry.call("missing", {})


@pytest.mark.asyncio
async def test_registry_supports_async_tools() -> None:
    registry = ToolRegistry()

    class Args(BaseModel):
        name: str

    @registry.register("hello", "Say hello. Use when a greeting is required.", Args)
    async def hello(args: Args) -> dict[str, str]:
        return {"message": f"hello {args.name}"}

    assert await registry.call("hello", {"name": "world"}) == {"message": "hello world"}


@pytest.mark.asyncio
async def test_budget_has_exact_decimal_totals() -> None:
    registry = default_registry()
    result = await registry.call(
        "estimate_budget",
        {
            "roles": [
                {"role": "Project Manager", "headcount": 1, "day_rate": 900, "days": 12},
                {"role": "Engineer", "headcount": 2, "day_rate": 700, "days": 20},
            ],
            "extras": [{"name": "Licenses", "cost": 4500}],
            "contingency_pct": 10,
            "currency": "USD",
        },
    )
    assert result["subtotal"] == "43300.00"
    assert result["contingency"] == "4330.00"
    assert result["total"] == "47630.00"
    assert result["formatted_total"] == "USD 47,630.00"
    assert sum_decimal_line_items(result["line_items"]) == result["subtotal"]


def sum_decimal_line_items(items: list[dict[str, object]]) -> str:
    from decimal import Decimal

    total = sum((Decimal(str(item["line_total"])) for item in items), Decimal("0"))
    return f"{total:.2f}"


@pytest.mark.asyncio
async def test_budget_bad_input() -> None:
    registry = default_registry()
    with pytest.raises(ToolError, match="headcount"):
        await registry.call(
            "estimate_budget",
            {"roles": [{"role": "Dev", "headcount": 0, "day_rate": 10, "days": 1}]},
        )


@pytest.mark.asyncio
async def test_schedule_skips_weekends_and_sets_milestones() -> None:
    registry = default_registry()
    result = await registry.call(
        "build_timeline",
        {
            "start_date": "2026-10-23",
            "phases": [{"name": "Discovery", "weeks": 1}, {"name": "Build", "weeks": 2}],
        },
    )
    assert result["start_date"] == "2026-10-23"
    assert result["total_weeks"] == 3
    assert result["phases"][0]["end_date"] == "2026-10-29"
    assert result["phases"][1]["start_date"] == "2026-10-30"
    assert result["phases"][1]["end_date"] == "2026-11-12"
    assert len(result["milestones"]) == 2
    for phase in result["phases"]:
        assert date.fromisoformat(phase["start_date"]).weekday() < 5
        assert date.fromisoformat(phase["end_date"]).weekday() < 5


@pytest.mark.asyncio
async def test_schedule_bad_date() -> None:
    registry = default_registry()
    with pytest.raises(ToolError, match="ISO date"):
        await registry.call(
            "build_timeline",
            {"start_date": "next month", "phases": [{"name": "One", "weeks": 1}]},
        )


def test_schedule_defaults_to_next_monday() -> None:
    result = build_timeline(TimelineArgs(phases=[{"name": "One", "weeks": 1}]))
    assert date.fromisoformat(result["start_date"]).weekday() == 0
    assert "start_date_adjusted" not in result


def test_schedule_rejects_a_past_start_date() -> None:
    result = build_timeline(
        TimelineArgs(
            start_date="2024-11-04",
            phases=[{"name": "One", "weeks": 2}, {"name": "Two", "weeks": 1}],
        )
    )

    start = date.fromisoformat(result["start_date"])
    assert start >= date.today()
    assert start.weekday() == 0
    assert result["requested_start_date"] == "2024-11-04"
    assert result["start_date_adjusted"] is True
    assert "2024-11-04" in result["adjustment_note"]
    assert result["total_weeks"] == 3


def test_schedule_keeps_an_explicit_future_start_date() -> None:
    result = build_timeline(
        TimelineArgs(
            start_date="2030-01-07",
            phases=[{"name": "One", "weeks": 1}],
        )
    )

    assert result["start_date"] == "2030-01-07"
    assert "start_date_adjusted" not in result


def test_risk_register_is_deterministic_and_relevant() -> None:
    args = RiskArgs(
        context_keywords=["compliance", "migration", "data", "privacy"],
        count=6,
    )
    first = generate_risk_register(args)
    second = generate_risk_register(args)
    assert first == second
    assert len(first["risks"]) == 6
    assert any(risk["category"] == "compliance" for risk in first["risks"])
    assert all(risk["score"] == risk["likelihood"] * risk["impact"] for risk in first["risks"])
    assert all(risk["rating"] in {"low", "medium", "high", "critical"} for risk in first["risks"])


def test_risk_register_count_validation() -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        RiskArgs(count=0)


def test_compare_options_ranks_complete_options() -> None:
    result = compare_options(
        CompareArgs(
            options=["Build", "Buy"],
            criteria=[{"name": "Cost", "weight": 2}, {"name": "Speed", "weight": 1}],
            scores={
                "Build": {"Cost": 4, "Speed": 2},
                "Buy": {"Cost": 2, "Speed": 5},
            },
        )
    )
    assert result["ranking"][0]["option"] == "Build"
    assert result["criteria"][0]["normalized_weight"] == pytest.approx(0.6667)
    assert result["missing"] == []


def test_compare_options_reports_missing_scores() -> None:
    result = compare_options(
        CompareArgs(
            options=["A", "B"],
            criteria=[{"name": "Cost", "weight": 1}],
            scores={"A": {"Cost": 4}},
        )
    )
    assert result["incomplete_options"] == ["B"]
    assert result["missing"] == [{"option": "B", "criterion": "Cost"}]
    assert [item["option"] for item in result["ranking"]] == ["A"]


def test_mock_company_and_roster_are_deterministic_and_labelled() -> None:
    company = get_company_profile()
    assert company["mock_data"] is True
    assert "MOCK DATA" in company["disclaimer"]
    first = get_team_roster(TeamRosterArgs(size=7))
    second = get_team_roster(TeamRosterArgs(size=7))
    assert first == second
    assert len(first["team"]) == 7
    assert first["mock_data"] is True


def test_template_covers_every_doc_type() -> None:
    assert set(TEMPLATE_SECTIONS) == set(DocType)
    for doc_type in DocType:
        result = get_section_template(TemplateArgs(doc_type=doc_type))
        assert result["count"] == len(TEMPLATE_SECTIONS[doc_type])
        assert all(section["title"] for section in result["sections"])
        assert all(section["guidance"] for section in result["sections"])


def test_mockdata_bad_roster_size() -> None:
    with pytest.raises(ValidationError, match="less than or equal to 100"):
        TeamRosterArgs(size=101)


def test_compare_options_rejects_nonpositive_weight() -> None:
    with pytest.raises(ValidationError, match="greater than 0"):
        CompareArgs(
            options=["A", "B"],
            criteria=[{"name": "Cost", "weight": 0}],
            scores={},
        )


@pytest.mark.asyncio
async def test_compare_and_template_tools_report_bad_input() -> None:
    registry = default_registry()
    with pytest.raises(ToolError, match="weight"):
        await registry.call(
            "compare_options",
            {
                "options": ["A", "B"],
                "criteria": [{"name": "Cost", "weight": 0}],
                "scores": {},
            },
        )
    with pytest.raises(ToolError, match="doc_type"):
        await registry.call("get_section_template", {"doc_type": "not_a_type"})


def test_company_profile_requires_empty_object_through_registry() -> None:
    registry = default_registry()
    assert registry.args_model("get_company_profile") is CompanyProfileArgs
    result = generate_risk_register(RiskArgs(context_keywords=["budget"], count=2))
    assert result["count"] == 2


def test_budget_model_schema_limits_extras() -> None:
    with pytest.raises(ValidationError, match="at least 1"):
        BudgetArgs(roles=[], extras=[])
