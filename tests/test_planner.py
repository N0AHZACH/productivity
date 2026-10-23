"""Planner validation, corrections, and deterministic fallback tests."""

import json

import pytest

from app.agent.planner import (
    fallback_plan,
    get_plan_source,
    plan,
    validate_plan,
)
from app.core.config import Settings
from app.llm.fake import FakeLLM
from app.models.common import DocType
from app.models.plan import TaskList
from app.models.state import Analysis
from app.tools.registry import default_registry


def _analysis(doc_type: DocType = DocType.PROJECT_PLAN, *, conflicts: bool = False) -> Analysis:
    return Analysis(
        doc_type=doc_type,
        objective="Deliver a controlled customer support chatbot launch.",
        audience="Executive sponsor and delivery team",
        known_facts=["Three-month timeline", "Team of five"],
        missing_info=["Budget ceiling"] if conflicts else [],
        conflicts=[
            "Cheap, fast, and fully compliant cannot all be maximized; prioritize "
            "compliance and phase the quick wins."
        ]
        if conflicts
        else [],
        assumptions=["Budget not given; assuming 120,000 USD."] if conflicts else [],
        complexity="complex" if conflicts else "standard",
    )


@pytest.mark.asyncio
async def test_valid_plan_passes_untouched() -> None:
    analysis = _analysis()
    valid = fallback_plan(analysis)
    llm = FakeLLM.with_schemas({TaskList: valid})
    settings = Settings(llm_max_output_tokens=2800)
    result = await plan(analysis, default_registry(), llm, settings)
    assert result == valid
    assert len(llm.calls) == 1
    assert llm.calls[0].max_tokens == 2800
    assert llm.calls[0].tier == "fast"
    assert get_plan_source() == "llm"


@pytest.mark.asyncio
async def test_planner_tier_is_configurable() -> None:
    analysis = _analysis()
    valid = fallback_plan(analysis)
    llm = FakeLLM.with_schemas({TaskList: valid})
    await plan(
        analysis,
        default_registry(),
        llm,
        Settings(planning_tier="strong"),
    )
    assert llm.calls[0].tier == "strong"


@pytest.mark.asyncio
async def test_invented_tool_triggers_one_correction() -> None:
    analysis = _analysis()
    valid = fallback_plan(analysis)
    invalid = valid.model_copy(deep=True)
    invalid.tasks[0].tool = "make_budget_magic"
    llm = FakeLLM.with_schemas({TaskList: [invalid, valid]})

    result = await plan(analysis, default_registry(), llm, Settings())

    assert result == valid
    assert len(llm.calls) == 2
    assert "make_budget_magic" in llm.calls[1].messages[-1]["content"]
    assert get_plan_source() == "llm"


@pytest.mark.asyncio
async def test_cyclic_plan_is_corrected() -> None:
    analysis = _analysis()
    valid = fallback_plan(analysis)
    cyclic_json = json.dumps(
        {
            "objective": "Cycle",
            "doc_type": "project_plan",
            "tasks": [
                {
                    "id": "t1",
                    "title": "One",
                    "description": "First",
                    "kind": "write_section",
                    "section_title": "One",
                    "depends_on": ["t2"],
                    "acceptance": "Exists",
                },
                {
                    "id": "t2",
                    "title": "Two",
                    "description": "Second",
                    "kind": "write_section",
                    "section_title": "Two",
                    "depends_on": ["t1"],
                    "acceptance": "Exists",
                },
            ],
        }
    )
    llm = FakeLLM.with_schemas({TaskList: [cyclic_json, valid]})

    result = await plan(analysis, default_registry(), llm, Settings())

    assert result == valid
    assert len(llm.calls) == 2
    assert "cycle" in llm.calls[1].messages[-1]["content"].lower()


@pytest.mark.asyncio
async def test_persistent_invalid_plan_returns_fallback() -> None:
    analysis = _analysis()
    llm = FakeLLM.with_schemas({TaskList: "not a plan at all"})

    result = await plan(analysis, default_registry(), llm, Settings())

    assert get_plan_source() == "fallback"
    assert validate_plan(result, default_registry(), analysis) == []
    assert len(llm.calls) == 3


def test_fallback_plan_passes_for_every_doc_type() -> None:
    registry = default_registry()
    for doc_type in DocType:
        analysis = _analysis(doc_type, conflicts=True)
        result = fallback_plan(analysis)
        problems = validate_plan(result, registry, analysis)
        assert problems == [], f"{doc_type}: {problems}"
        assert 4 <= len(result.tasks) <= 16
        assert sum(task.kind == "write_section" for task in result.tasks) >= 3


def test_validate_plan_reports_tool_args_and_acceptance_shape() -> None:
    analysis = _analysis()
    plan_model = fallback_plan(analysis)
    plan_model.tasks[0].tool_args = {"roles": []}
    problems = validate_plan(plan_model, default_registry(), analysis)
    assert any("invalid arguments" in problem for problem in problems)


def test_validate_plan_forbids_rendering_tasks() -> None:
    analysis = _analysis()
    plan_model = fallback_plan(analysis)
    plan_model.tasks[-1].description += " Render the final .docx file."
    problems = validate_plan(plan_model, default_registry(), analysis)
    assert any("rendering" in problem for problem in problems)


def test_conflict_requires_tradeoff_section() -> None:
    analysis = _analysis(conflicts=True)
    plan_model = fallback_plan(_analysis(conflicts=False))
    plan_model.doc_type = analysis.doc_type
    problems = validate_plan(plan_model, default_registry(), analysis)
    assert any("conflicts" in problem for problem in problems)
