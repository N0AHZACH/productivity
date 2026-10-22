"""LLM planning with semantic validation, correction rounds, and a safe fallback."""

import json
import re
from contextvars import ContextVar
from difflib import get_close_matches
from typing import Any, Literal

import structlog

from app.agent.text_utils import titles_match
from app.core.config import Settings
from app.core.errors import LLMOutputInvalid
from app.llm.base import LLMClient
from app.models.common import DocType
from app.models.plan import Task, TaskList
from app.models.state import Analysis
from app.prompts import load_prompt
from app.tools.registry import ToolRegistry
from app.tools.templates import TemplateArgs, get_section_template, template_titles

logger = structlog.get_logger(__name__)

PlanSource = Literal["llm", "fallback"]
_PLAN_SOURCE: ContextVar[PlanSource] = ContextVar("plan_source", default="llm")

_CONFLICT_PATTERN = re.compile(r"trade[\s-]?offs?|\boptions?\b|prioriti[sz]", flags=re.IGNORECASE)
_FORBIDDEN_PATTERNS = (
    re.compile(r"\b(?:render|rendering|export|exporting|download)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:save|write|create|generate|produce)\b.{0,24}\b(?:docx|word|file)\b",
        re.IGNORECASE,
    ),
)
_OMISSION_WORDS = ("omit", "omitted", "skip", "excluded", "not included")


def get_plan_source() -> PlanSource:
    """Return the source of the most recently planned TaskList in this context."""
    return _PLAN_SOURCE.get()


def _set_plan_source(source: PlanSource) -> None:
    _PLAN_SOURCE.set(source)


def _template_covered(plan: TaskList, template_title: str) -> bool:
    writers = [task for task in plan.tasks if task.kind == "write_section"]
    if any(titles_match(task.section_title or "", template_title) for task in writers):
        return True
    topic_tokens = {
        token for token in re.findall(r"[a-z0-9]+", template_title.lower()) if len(token) > 3
    }
    for task in plan.tasks:
        description = task.description.lower()
        if any(word in description for word in _OMISSION_WORDS) and any(
            token in description for token in topic_tokens
        ):
            return True
    return False


def _forbidden_work(plan: TaskList) -> list[str]:
    problems: list[str] = []
    for task in plan.tasks:
        combined = f"{task.title} {task.description}"
        if any(pattern.search(combined) for pattern in _FORBIDDEN_PATTERNS):
            problems.append(f"{task.id} plans rendering/exporting/file creation; that is automatic")
    return problems


def validate_plan(plan: TaskList, registry: ToolRegistry, analysis: Analysis) -> list[str]:
    """Return semantic problems the planner must correct."""
    problems: list[str] = []
    if plan.doc_type != analysis.doc_type:
        problems.append(
            f"plan doc_type is {plan.doc_type.value!r} but analysis chose "
            f"{analysis.doc_type.value!r}"
        )

    tasks = plan.tasks
    writer_tasks = [task for task in tasks if task.kind == "write_section"]
    if not 4 <= len(tasks) <= 16:
        problems.append("the plan must contain between 4 and 16 tasks")
    if len(writer_tasks) < 3:
        problems.append("the plan must contain at least 3 write_section tasks")

    known_names = registry.names()
    for task in tasks:
        if task.kind == "tool":
            if not registry.has(task.tool or ""):
                suggestion = get_close_matches(task.tool or "", known_names, n=1)
                hint = f" Did you mean {suggestion[0]!r}?" if suggestion else ""
                problems.append(
                    f"{task.id} names unknown tool {task.tool!r}; available tools: "
                    f"{', '.join(known_names)}.{hint}"
                )
            else:
                arg_problems = registry.validate_args(task.tool or "", task.tool_args)
                if arg_problems:
                    problems.append(
                        f"{task.id} has invalid arguments for {task.tool!r}: "
                        + "; ".join(arg_problems)
                    )
        if task.kind == "write_section" and not (task.section_title or "").strip():
            problems.append(f"{task.id} is a write_section task without section_title")

    for template_title in template_titles(analysis.doc_type):
        if not _template_covered(plan, template_title):
            problems.append(
                f"recommended section {template_title!r} is neither covered by a "
                "write_section task nor explicitly omitted with a reason"
            )

    if analysis.conflicts and not any(
        _CONFLICT_PATTERN.search(f"{task.title} {task.description}") for task in writer_tasks
    ):
        problems.append(
            "analysis lists conflicts, but no write_section task addresses trade-offs, "
            "options, or prioritization"
        )

    problems.extend(_forbidden_work(plan))
    return problems


def _analysis_context(analysis: Analysis) -> str:
    return json.dumps(analysis.model_dump(mode="json"), indent=2, ensure_ascii=False)


def _build_messages(
    analysis: Analysis, registry: ToolRegistry, template: list[dict[str, str]]
) -> list[dict[str, str]]:
    tools = [spec.model_dump(mode="json") for spec in registry.describe()]
    user = (
        "ANALYSIS:\n"
        f"{_analysis_context(analysis)}\n\n"
        "TOOL CATALOG (name, planner-facing description, JSON Schema):\n"
        f"{json.dumps(tools, indent=2, ensure_ascii=False)}\n\n"
        "RECOMMENDED SECTION TEMPLATE:\n"
        f"{json.dumps(template, indent=2, ensure_ascii=False)}\n\n"
        "Return a TaskList JSON object that fully covers this plan. Every tool "
        "argument must validate against its schema."
    )
    return [
        {"role": "system", "content": load_prompt("planner")},
        {"role": "user", "content": user},
    ]


async def plan(
    analysis: Analysis,
    registry: ToolRegistry,
    llm: LLMClient,
    settings: Settings,
) -> TaskList:
    """Plan tasks, correcting invalid output at most twice before falling back."""
    template = get_section_template(TemplateArgs(doc_type=analysis.doc_type))["sections"]
    messages = _build_messages(analysis, registry, template)
    last_problems: list[str] = []
    candidate: TaskList | None = None

    for round_index in range(3):
        try:
            candidate = await llm.complete_json(messages, TaskList, tier="strong", max_repairs=0)
        except LLMOutputInvalid as exc:
            last_problems = [f"your previous output was not a valid TaskList: {exc}"]
            candidate = None
        else:
            if candidate is not None:
                last_problems = validate_plan(candidate, registry, analysis)
                if not last_problems:
                    _set_plan_source("llm")
                    logger.info(
                        "plan_valid",
                        tasks=len(candidate.tasks),
                        corrections=round_index,
                        doc_type=analysis.doc_type.value,
                    )
                    return candidate

        logger.warning(
            "plan_invalid",
            round=round_index + 1,
            problems=last_problems,
        )
        if round_index == 2:
            break

        correction = (
            "The plan failed validation. Fix every problem below and return a complete "
            "TaskList JSON object only.\n\nProblems:\n- " + "\n- ".join(last_problems)
        )
        if candidate is not None:
            messages = [
                *messages,
                {"role": "assistant", "content": candidate.model_dump_json()},
                {"role": "user", "content": correction},
            ]
        else:
            messages = [*messages, {"role": "user", "content": correction}]

    fallback = fallback_plan(analysis)
    fallback_problems = validate_plan(fallback, registry, analysis)
    if fallback_problems:  # pragma: no cover - guards against future template drift
        logger.error("fallback_plan_invalid", problems=fallback_problems)
    _set_plan_source("fallback")
    logger.warning("plan_fallback_used", last_problems=last_problems)
    return fallback


def _budget_tool_args() -> dict[str, Any]:
    return {
        "roles": [
            {"role": "Project Manager", "headcount": 1, "day_rate": 900, "days": 20},
            {"role": "Solution Architect", "headcount": 1, "day_rate": 1100, "days": 15},
            {"role": "Implementation Engineer", "headcount": 2, "day_rate": 750, "days": 30},
        ],
        "extras": [{"name": "Platform and tooling", "cost": 12000}],
        "contingency_pct": 10,
        "currency": "USD",
    }


def _timeline_tool_args() -> dict[str, Any]:
    return {
        "start_date": None,
        "phases": [
            {"name": "Discovery and design", "weeks": 2},
            {"name": "Build and integrate", "weeks": 6},
            {"name": "Pilot and rollout", "weeks": 4},
        ],
        "milestones": True,
    }


def _risk_keywords(analysis: Analysis) -> list[str]:
    text = " ".join(
        [
            analysis.doc_type.value.replace("_", " "),
            analysis.objective,
            analysis.audience,
            *analysis.known_facts,
            *analysis.conflicts,
        ]
    )
    tokens = [token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) > 3]
    seen: list[str] = []
    for token in tokens:
        if token not in seen:
            seen.append(token)
    return seen[:15]


def _compare_tool_args() -> dict[str, Any]:
    return {
        "options": ["Vendor SaaS replacement", "Incremental modernization", "In-house build"],
        "criteria": [
            {"name": "Cost", "weight": 0.3},
            {"name": "Time to deliver", "weight": 0.25},
            {"name": "Compliance", "weight": 0.25},
            {"name": "Delivery risk", "weight": 0.2},
        ],
        "scores": {
            "Vendor SaaS replacement": {
                "Cost": 2,
                "Time to deliver": 4,
                "Compliance": 4,
                "Delivery risk": 3,
            },
            "Incremental modernization": {
                "Cost": 4,
                "Time to deliver": 2,
                "Compliance": 3,
                "Delivery risk": 3,
            },
            "In-house build": {
                "Cost": 2,
                "Time to deliver": 1,
                "Compliance": 3,
                "Delivery risk": 2,
            },
        },
    }


def _tool_task(task_id: str, title: str, tool: str, args: dict[str, Any], acceptance: str) -> Task:
    return Task(
        id=task_id,
        title=title,
        description=f"Run {tool} with the literal arguments supplied in the plan.",
        kind="tool",
        tool=tool,
        tool_args=args,
        depends_on=[],
        acceptance=acceptance,
    )


def _fallback_tools(analysis: Analysis) -> list[Task]:
    doc_type = analysis.doc_type
    tasks: list[Task] = []
    if doc_type in {DocType.PROPOSAL, DocType.PROJECT_PLAN}:
        tasks.append(
            _tool_task(
                "t1",
                "Estimate delivery budget",
                "estimate_budget",
                _budget_tool_args(),
                "Budget result includes line items and a total equal to subtotal plus contingency.",
            )
        )
        tasks.append(
            _tool_task(
                "t2",
                "Build delivery timeline",
                "build_timeline",
                _timeline_tool_args(),
                "Timeline includes start and end dates, every phase, and milestones.",
            )
        )
        tasks.append(
            _tool_task(
                "t3",
                "Generate risk register",
                "generate_risk_register",
                {"context_keywords": _risk_keywords(analysis), "count": 6},
                "Risk register contains six scored risks with concrete mitigations.",
            )
        )
    elif doc_type == DocType.DECISION_MEMO:
        tasks.append(
            _tool_task(
                "t1",
                "Compare replacement options",
                "compare_options",
                _compare_tool_args(),
                "Option comparison includes normalized weights, ranking, and no missing scores.",
            )
        )
        tasks.append(
            _tool_task(
                "t2",
                "Estimate replacement budget",
                "estimate_budget",
                _budget_tool_args(),
                "Budget result includes line items and one consistent total.",
            )
        )
        tasks.append(
            _tool_task(
                "t3",
                "Generate risk register",
                "generate_risk_register",
                {"context_keywords": _risk_keywords(analysis), "count": 6},
                "Risk register contains six scored risks with concrete mitigations.",
            )
        )
    elif doc_type == DocType.BUSINESS_REPORT:
        tasks.append(
            _tool_task(
                "t1",
                "Load company profile",
                "get_company_profile",
                {},
                "Company profile is returned and labelled as mock data.",
            )
        )
        tasks.append(
            _tool_task(
                "t2",
                "Generate risk register",
                "generate_risk_register",
                {"context_keywords": _risk_keywords(analysis), "count": 6},
                "Risk register contains six scored risks with concrete mitigations.",
            )
        )
    elif doc_type in {DocType.TECHNICAL_DESIGN, DocType.PRODUCT_SPEC}:
        tasks.append(
            _tool_task(
                "t1",
                "Build delivery timeline",
                "build_timeline",
                _timeline_tool_args(),
                "Timeline includes start and end dates, every phase, and milestones.",
            )
        )
        tasks.append(
            _tool_task(
                "t2",
                "Generate risk register",
                "generate_risk_register",
                {"context_keywords": _risk_keywords(analysis), "count": 6},
                "Risk register contains six scored risks with concrete mitigations.",
            )
        )
    elif doc_type == DocType.SOP:
        tasks.append(
            _tool_task(
                "t1",
                "Generate control risks",
                "generate_risk_register",
                {"context_keywords": ["sop", *_risk_keywords(analysis)], "count": 5},
                "Risk register contains five scored risks relevant to operating controls.",
            )
        )
    return tasks


def fallback_plan(analysis: Analysis) -> TaskList:
    """Build a deterministic plan guaranteed to satisfy validate_plan."""
    tool_tasks = _fallback_tools(analysis)
    tool_ids = [task.id for task in tool_tasks]
    next_id = len(tool_tasks) + 1
    template = template_titles(analysis.doc_type)
    assumption_note = (
        " Use these analysis assumptions where relevant: " + " ".join(analysis.assumptions[:4])
        if analysis.assumptions
        else ""
    )
    conflict_note = (
        " Address the listed conflicts directly using trade-offs, options, or prioritization."
        if analysis.conflicts
        else ""
    )

    writer_tasks: list[Task] = []
    conflict_covered = False
    for title in template:
        description = (
            f"Write the {title!r} section using dependency results as the source of truth."
            f"{assumption_note}{conflict_note}"
        )
        if analysis.conflicts and _CONFLICT_PATTERN.search(title):
            conflict_covered = True
        writer_tasks.append(
            Task(
                id=f"t{next_id}",
                title=title,
                description=description,
                kind="write_section",
                section_title=title,
                depends_on=tool_ids,
                acceptance=(
                    f"The {title!r} section contains at least two substantive blocks, "
                    "meets its purpose, and copies tool figures exactly."
                ),
            )
        )
        next_id += 1

    if analysis.conflicts and not conflict_covered:
        writer_tasks.append(
            Task(
                id=f"t{next_id}",
                title="Conflicts and Trade-offs",
                description=(
                    "Compare the conflicting constraints, explain the trade-offs, and "
                    "recommend a prioritized path. Use tool results where available."
                    f"{assumption_note}"
                ),
                kind="write_section",
                section_title="Conflicts and Trade-offs",
                depends_on=tool_ids,
                acceptance=(
                    "Every listed conflict is named, at least two options are compared, "
                    "and a clear prioritization is recommended."
                ),
            )
        )

    return TaskList(
        objective=analysis.objective,
        doc_type=analysis.doc_type,
        tasks=[*tool_tasks, *writer_tasks],
    )
