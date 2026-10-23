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
from app.models.document import Paragraph, SectionDraft, Table
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
    section_calls = [call for call in llm.calls if call.schema is SectionDraft]
    assert section_calls
    assert all(call.tier == "strong" for call in section_calls)


@pytest.mark.asyncio
async def test_dependent_task_starts_before_unrelated_slow_sibling_finishes() -> None:
    registry = ToolRegistry()
    starts: dict[str, float] = {}
    ends: dict[str, float] = {}

    class DelayArgs(BaseModel):
        label: str
        delay_s: float

    class EmptyArgs(BaseModel):
        pass

    @registry.register("fast", "Return immediately. Use to unlock a dependent.", EmptyArgs)
    def fast(args: EmptyArgs) -> dict[str, Any]:
        return {"ready": True}

    @registry.register("delay", "Sleep for the requested time. Use in DAG tests.", DelayArgs)
    async def delay(args: DelayArgs) -> dict[str, Any]:
        starts[args.label] = time.monotonic()
        await asyncio.sleep(args.delay_s)
        ends[args.label] = time.monotonic()
        return {"label": args.label}

    plan = TaskList(
        objective="Dynamic DAG",
        doc_type=DocType.PROJECT_PLAN,
        tasks=[
            Task(
                id="t1",
                title="Fast",
                description="Fast",
                kind="tool",
                tool="fast",
                acceptance="Ready.",
            ),
            Task(
                id="t2",
                title="Slow",
                description="Slow",
                kind="tool",
                tool="delay",
                tool_args={"label": "slow", "delay_s": 0.08},
                acceptance="Runs.",
            ),
            Task(
                id="t3",
                title="Dependent",
                description="Needs only t1",
                kind="tool",
                tool="delay",
                tool_args={"label": "dependent", "delay_s": 0.01},
                depends_on=["t1"],
                acceptance="Runs.",
            ),
        ],
    )
    state = _state(plan)

    await execute(
        state,
        registry,
        FakeLLM(),
        Settings(max_concurrency=4),
        lambda *args, **kwargs: None,
    )

    assert all(task.status is TaskStatus.DONE for task in plan.tasks)
    assert starts["dependent"] < ends["slow"]


@pytest.mark.asyncio
async def test_executor_respects_max_concurrency() -> None:
    registry = ToolRegistry()
    active = 0
    peak = 0

    class EmptyArgs(BaseModel):
        pass

    @registry.register(
        "active_tool",
        "Track concurrent calls. Use in concurrency tests.",
        EmptyArgs,
    )
    async def active_tool(args: EmptyArgs) -> dict[str, bool]:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return {"ok": True}

    plan = TaskList(
        objective="Bounded",
        doc_type=DocType.PROJECT_PLAN,
        tasks=[
            Task(
                id=f"t{index}",
                title=f"Task {index}",
                description="Run bounded.",
                kind="tool",
                tool="active_tool",
                acceptance="Runs.",
            )
            for index in range(1, 7)
        ],
    )
    state = _state(plan)

    await execute(
        state,
        registry,
        FakeLLM(),
        Settings(max_concurrency=2),
        lambda *args, **kwargs: None,
    )

    assert all(task.status is TaskStatus.DONE for task in plan.tasks)
    assert peak == 2


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
async def test_composer_prompt_includes_table_rankings() -> None:
    draft = SectionDraft(
        heading="Options Considered",
        blocks=[
            Paragraph(text="Three options were scored against the criteria."),
            Table(
                caption="Weighted option scores",
                columns=["Option", "Weighted Score", "Rank"],
                rows=[
                    ["SaaS billing platform", "6.45", "1"],
                    ["Hybrid legacy upgrade", "6.20", "2"],
                ],
            ),
        ],
    )
    analysis = Analysis(
        doc_type=DocType.DECISION_MEMO,
        objective="Recommend a billing platform.",
        audience="Board",
    )
    plan = TaskList(
        objective=analysis.objective,
        doc_type=analysis.doc_type,
        tasks=[
            Task(
                id="t1",
                title="Options",
                description="Compare the options.",
                kind="write_section",
                section_title="Options Considered",
                acceptance="Options are ranked.",
                status=TaskStatus.DONE,
                result=draft.model_dump(mode="json"),
            )
        ],
    )
    state = AgentState(
        run_id="run3",
        request="Recommend a platform.",
        analysis=analysis,
        plan=plan,
        status=RunStatus.RUNNING,
    )
    llm = FakeLLM.with_schemas(
        {CompositionDraft: {"title": "Billing Decision", "executive_summary": "Summary."}}
    )

    await compose(state, llm, Settings())

    prompt = llm.calls[0].messages[-1]["content"]
    assert "Weighted option scores" in prompt
    assert "SaaS billing platform | 6.45 | 1" in prompt
    assert "Hybrid legacy upgrade | 6.20 | 2" in prompt


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
