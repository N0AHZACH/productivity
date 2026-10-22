"""Executor ordering, parallelism, correction, and degradation tests."""

import asyncio
import time
from typing import Any

import pytest
from pydantic import BaseModel, Field

from app.agent.composer import CompositionDraft, compose
from app.agent.executor import CorrectedToolArgs, execute
from app.core.config import Settings
from app.core.errors import PlanInvalid, ToolError
from app.llm.fake import FakeLLM
from app.models.common import DocType, RunStatus, TaskStatus
from app.models.document import Paragraph, SectionDraft
from app.models.plan import Task, TaskList
from app.models.state import AgentState, Analysis
from app.tools.registry import ToolRegistry


def _analysis() -> Analysis:
    return Analysis(
        doc_type=DocType.PROJECT_PLAN,
        objective="Launch a customer support chatbot.",
        audience="Executive team",
        known_facts=["Twelve weeks"],
        assumptions=["Budget not given; assuming 120,000 USD."],
    )


def _state(plan: TaskList) -> AgentState:
    return AgentState(
        run_id="run1", request="Write a project plan.", analysis=_analysis(), plan=plan
    )


def _draft(heading: str) -> SectionDraft:
    return SectionDraft(
        heading=heading,
        blocks=[Paragraph(text=f"{heading} content with substantive detail.")],
    )


@pytest.mark.asyncio
async def test_diamond_plan_runs_in_order_and_parallel() -> None:
    registry = ToolRegistry()
    starts: dict[str, float] = {}
    ends: dict[str, float] = {}

    class NapArgs(BaseModel):
        label: str

    class EmptyArgs(BaseModel):
        pass

    @registry.register("fast_tool", "Return immediately. Use as a plan seed.", EmptyArgs)
    def fast_tool(args: EmptyArgs) -> dict[str, Any]:
        return {"ready": True}

    @registry.register("nap", "Sleep briefly to expose concurrency. Use in tests.", NapArgs)
    async def nap(args: NapArgs) -> dict[str, Any]:
        starts[args.label] = time.monotonic()
        await asyncio.sleep(0.06)
        ends[args.label] = time.monotonic()
        return {"label": args.label}

    plan = TaskList(
        objective="Diamond",
        doc_type=DocType.PROJECT_PLAN,
        tasks=[
            Task(
                id="t1",
                title="Seed",
                description="Seed",
                kind="tool",
                tool="fast_tool",
                tool_args={},
                acceptance="Ready is true.",
            ),
            Task(
                id="t2",
                title="Slow A",
                description="A",
                kind="tool",
                tool="nap",
                tool_args={"label": "a"},
                depends_on=["t1"],
                acceptance="A returns.",
            ),
            Task(
                id="t3",
                title="Slow B",
                description="B",
                kind="tool",
                tool="nap",
                tool_args={"label": "b"},
                depends_on=["t1"],
                acceptance="B returns.",
            ),
            Task(
                id="t4",
                title="Synthesis",
                description="Write the result.",
                kind="write_section",
                section_title="Synthesis",
                depends_on=["t2", "t3"],
                acceptance="Synthesis exists.",
            ),
        ],
    )
    state = _state(plan)
    llm = FakeLLM.with_schemas({SectionDraft: _draft("Synthesis")})
    emitted: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def emit(*args: Any, **kwargs: Any) -> None:
        emitted.append((args, kwargs))

    await execute(state, registry, llm, Settings(max_concurrency=4), emit)

    assert all(task.status is TaskStatus.DONE for task in plan.tasks)
    assert max(starts["a"], starts["b"]) < min(ends["a"], ends["b"])
    assert plan.tasks[3].result["heading"] == "Synthesis"
    assert any(kwargs.get("event") == "started" for _, kwargs in emitted)
    assert any(kwargs.get("event") == "finished" for _, kwargs in emitted)


@pytest.mark.asyncio
async def test_tool_failure_requests_corrected_args_once() -> None:
    registry = ToolRegistry()

    class ValueArgs(BaseModel):
        value: int = Field(gt=0)

    @registry.register(
        "need_positive",
        "Return a positive value. Use to test argument correction.",
        ValueArgs,
    )
    def need_positive(args: ValueArgs) -> dict[str, int]:
        return {"value": args.value}

    plan = TaskList(
        objective="Correct args",
        doc_type=DocType.PROJECT_PLAN,
        tasks=[
            Task(
                id="t1",
                title="Use tool",
                description="Invoke the tool.",
                kind="tool",
                tool="need_positive",
                tool_args={"value": -2},
                acceptance="A positive value is returned.",
            )
        ],
    )
    llm = FakeLLM.with_schemas({CorrectedToolArgs: {"tool_args": {"value": 5}}})
    state = _state(plan)

    await execute(state, registry, llm, Settings(), lambda *args, **kwargs: None)

    assert plan.tasks[0].status is TaskStatus.DONE
    assert plan.tasks[0].attempts == 2
    assert plan.tasks[0].result == {"value": 5}
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_failure_skips_dependents_but_independent_branch_completes() -> None:
    registry = ToolRegistry()

    class EmptyArgs(BaseModel):
        pass

    @registry.register("fail", "Always fail. Use for degradation tests.", EmptyArgs)
    def fail(args: EmptyArgs) -> dict[str, Any]:
        raise ToolError("permanent failure")

    @registry.register("good", "Return success. Use for degradation tests.", EmptyArgs)
    def good(args: EmptyArgs) -> dict[str, Any]:
        return {"ok": True}

    plan = TaskList(
        objective="Degrade",
        doc_type=DocType.PROJECT_PLAN,
        tasks=[
            Task(
                id="t1",
                title="Fail",
                description="Fail",
                kind="tool",
                tool="fail",
                acceptance="Fails.",
            ),
            Task(
                id="t2",
                title="Dependent",
                description="Needs t1",
                kind="write_section",
                section_title="Dependent",
                depends_on=["t1"],
                acceptance="Exists.",
            ),
            Task(
                id="t3",
                title="Good",
                description="Independent",
                kind="tool",
                tool="good",
                acceptance="Succeeds.",
            ),
        ],
    )
    state = _state(plan)
    await execute(state, registry, FakeLLM(), Settings(), lambda *args, **kwargs: None)

    assert plan.tasks[0].status is TaskStatus.FAILED
    assert plan.tasks[1].status is TaskStatus.SKIPPED
    assert plan.tasks[2].status is TaskStatus.DONE
    assert state.warnings


@pytest.mark.asyncio
async def test_composer_orders_sections_and_adds_executive_summary() -> None:
    plan = TaskList(
        objective="Compose",
        doc_type=DocType.PROJECT_PLAN,
        tasks=[
            Task(
                id="t1",
                title="Risks",
                description="Risks",
                kind="write_section",
                section_title="Risks and Mitigations",
                acceptance="Risks",
                status=TaskStatus.DONE,
                result=_draft("Risks and Mitigations").model_dump(mode="json"),
            ),
            Task(
                id="t2",
                title="Objectives",
                description="Objectives",
                kind="write_section",
                section_title="Objectives and Success Criteria",
                acceptance="Objectives",
                status=TaskStatus.DONE,
                result=_draft("Objectives and Success Criteria").model_dump(mode="json"),
            ),
        ],
    )
    analysis = _analysis()
    analysis.conflicts = ["Speed and full compliance cannot both be maximized."]
    state = AgentState(
        run_id="run2",
        request="Write a plan.",
        analysis=analysis,
        plan=plan,
        status=RunStatus.RUNNING,
    )
    llm = FakeLLM.with_schemas(
        {
            CompositionDraft: {
                "title": "Customer Support Chatbot Project Plan",
                "subtitle": "Twelve-week launch",
                "executive_summary": "The plan launches a focused pilot before widening scope.",
                "recommendation": "Prioritize a safe pilot; defer broad automation.",
            }
        }
    )

    spec = await compose(state, llm, Settings(default_author="Test Author"))

    assert spec.sections[0].heading == "Executive Summary"
    assert spec.sections[1].heading == "Objectives and Success Criteria"
    assert spec.sections[2].heading == "Risks and Mitigations"
    assert [section.id for section in spec.sections] == ["s_summary", "s_t2", "s_t1"]
    assert len({section.id for section in spec.sections}) == 3
    summary_blocks = spec.sections[0].blocks
    assert any(getattr(block, "kind", None) == "decision" for block in summary_blocks)
    assert spec.metadata["author"] == "Test Author"
    assert spec.assumptions == analysis.assumptions
    assert state.spec == spec
    assert len(llm.calls) == 1
    assert llm.calls[0].tier == "fast"


@pytest.mark.asyncio
async def test_composer_raises_when_no_sections_succeed() -> None:
    plan = TaskList(
        objective="Empty",
        doc_type=DocType.PROJECT_PLAN,
        tasks=[
            Task(
                id="t1",
                title="Missing",
                description="Missing",
                kind="write_section",
                section_title="Missing",
                acceptance="Exists",
            )
        ],
    )
    state = AgentState(run_id="run3", request="Write.", analysis=_analysis(), plan=plan)
    with pytest.raises(PlanInvalid, match="No sections"):
        await compose(state, FakeLLM(), Settings())
