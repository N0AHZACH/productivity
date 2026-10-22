"""Fast deterministic document checks that never hallucinate."""

import json
import re
from decimal import Decimal, InvalidOperation

from app.agent.text_utils import titles_match
from app.models.common import TaskStatus
from app.models.document import (
    BulletList,
    Callout,
    DocumentSpec,
    NumberedList,
    Paragraph,
    Section,
    Table,
)
from app.models.plan import TaskList
from app.models.state import Analysis, Issue
from app.tools.templates import template_titles

MISSING_SECTION_PREFIX = "Missing required section:"
_PLACEHOLDER_PATTERNS = (
    re.compile(r"\bTBD\b", re.IGNORECASE),
    re.compile(r"\bTODO\b", re.IGNORECASE),
    re.compile(r"\blorem ipsum\b", re.IGNORECASE),
    re.compile(r"\[insert\b", re.IGNORECASE),
    re.compile(r"\bXX\b"),
    re.compile(r"\?\?\?"),
)
_NUMBER_RE = re.compile(r"(?:[A-Z]{3}\s*)?[$€£]?\s*(\d[\d,]*(?:\.\d+)?)")
_BUDGET_AMOUNT_RE = re.compile(
    r"\bbudget(?:ary)?\b[^.;!?]{0,60}?\b(?:of|is|was|were|at|:)\s*"
    r"(?:(?:USD|EUR|GBP)\s*|[$€£]\s*)?"
    r"(?P<amount>\d[\d,]*(?:\.\d+)?)"
    r"(?:\s*(?:USD|EUR|GBP)\b)?",
    re.IGNORECASE,
)
_MONEY_RE = re.compile(
    r"(?:(?:USD|EUR|GBP)\s*|[$€£]\s*)(?P<prefix>\d[\d,]*(?:\.\d+)?)"
    r"|(?P<suffix>\d[\d,]*(?:\.\d+)?)\s*(?:USD|EUR|GBP)\b",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"[a-z0-9]+")
_CONFLICT_STOPWORDS = {
    "also",
    "and",
    "are",
    "but",
    "cannot",
    "fully",
    "have",
    "must",
    "not",
    "the",
    "this",
    "together",
    "with",
}


def section_text(section: Section) -> str:
    """Flatten one section's visible text for checks and critic context."""
    parts = [section.heading]
    for block in section.blocks:
        if isinstance(block, Paragraph):
            parts.append(block.text)
        elif isinstance(block, (BulletList, NumberedList)):
            parts.extend(block.items)
        elif isinstance(block, Table):
            if block.caption:
                parts.append(block.caption)
            parts.extend(block.columns)
            parts.extend(cell for row in block.rows for cell in row)
        elif isinstance(block, Callout):
            parts.append(block.text)
    return " ".join(part for part in parts if part)


def _numbers(text: str) -> list[Decimal]:
    values: list[Decimal] = []
    for match in _NUMBER_RE.finditer(text):
        try:
            values.append(Decimal(match.group(1).replace(",", "")))
        except InvalidOperation:
            continue
    return values


def _budget_amounts(text: str) -> list[Decimal]:
    amounts: list[Decimal] = []
    for match in _BUDGET_AMOUNT_RE.finditer(text):
        try:
            amounts.append(Decimal(match.group("amount").replace(",", "")))
        except InvalidOperation:
            continue
    return amounts


def _budget_consistency_issues(spec: DocumentSpec) -> list[Issue]:
    expected = {
        amount
        for assumption in spec.assumptions
        if "budget" in assumption.lower()
        for amount in _budget_amounts(assumption)
    }
    if not expected:
        return []

    issues: list[Issue] = []
    for section in spec.sections:
        actual = set(_budget_amounts(section_text(section)))
        conflicts = sorted(actual - expected)
        if not conflicts:
            continue
        expected_text = ", ".join(f"{amount:,.2f}" for amount in sorted(expected))
        conflict_text = ", ".join(f"{amount:,.2f}" for amount in conflicts)
        issues.append(
            Issue(
                section_id=section.id,
                severity="major",
                description=(
                    f"The section uses budget amount(s) {conflict_text} but the document "
                    f"assumes {expected_text}. Use the assumed budget consistently and do "
                    "not invent contingency or derived totals."
                ),
            )
        )
    return issues


def _money_amounts(text: str) -> list[Decimal]:
    amounts: list[Decimal] = []
    for match in _MONEY_RE.finditer(text):
        raw = match.group("prefix") or match.group("suffix")
        try:
            amounts.append(Decimal(raw.replace(",", "")))
        except InvalidOperation:
            continue
    return amounts


def _source_amounts(spec: DocumentSpec, plan: TaskList, analysis: Analysis) -> set[Decimal]:
    parts = [analysis.model_dump_json(), *spec.assumptions]
    for task in plan.tasks:
        if task.result is not None:
            parts.append(json.dumps(task.result, default=str))
        if task.tool_args:
            parts.append(json.dumps(task.tool_args, default=str))
    return set(_numbers(" ".join(parts)))


def _unsupported_money_issues(
    spec: DocumentSpec,
    plan: TaskList,
    analysis: Analysis,
) -> list[Issue]:
    source = _source_amounts(spec, plan, analysis)
    issues: list[Issue] = []
    for section in spec.sections:
        unsupported = sorted(set(_money_amounts(section_text(section))) - source)
        if not unsupported:
            continue
        amounts = ", ".join(f"{amount:,.2f}" for amount in unsupported)
        issues.append(
            Issue(
                section_id=section.id,
                severity="major",
                description=(
                    f"The section contains monetary amount(s) {amounts} that are not "
                    "supported by the analysis, assumptions, or tool results. Remove "
                    "them or use supplied figures exactly."
                ),
            )
        )
    return issues


def _task_result(plan: TaskList, tool_name: str) -> dict[str, object] | None:
    for task in plan.tasks:
        if (
            task.kind == "tool"
            and task.tool == tool_name
            and task.status is TaskStatus.DONE
            and isinstance(task.result, dict)
        ):
            return task.result
    return None


def _budget_total_issues(spec: DocumentSpec, plan: TaskList) -> list[Issue]:
    result = _task_result(plan, "estimate_budget")
    if result is None or "total" not in result:
        return []
    try:
        expected = Decimal(str(result["total"]))
    except InvalidOperation:
        return []

    full_text = " ".join(section_text(section) for section in spec.sections)
    budget_sections = [
        section for section in spec.sections if "budget" in section.heading.lower()
    ]
    if expected not in _numbers(full_text):
        issue = Issue(
            section_id=budget_sections[0].id if budget_sections else None,
            severity="major",
            description=(
                f"The budget total {expected:,.2f} from estimate_budget does not appear "
                "anywhere in the document."
            ),
        )
    else:
        issue = None

    table_problem: Issue | None = None
    for section in budget_sections:
        for block in section.blocks:
            if not isinstance(block, Table):
                continue
            if _table_matches_budget(block, expected):
                continue
            table_problem = Issue(
                section_id=section.id,
                severity="major",
                description=(
                    f"The table in {section.heading!r} does not reconcile to the "
                    f"estimate_budget total of {expected:,.2f}. Use the tool total exactly."
                ),
            )
            break
        if table_problem:
            break
    return [found for found in (issue, table_problem) if found is not None]


def _table_matches_budget(table: Table, expected: Decimal) -> bool:
    if not table.rows:
        return False
    total_rows = [row for row in table.rows if row and "total" in row[0].lower()]
    if total_rows:
        return any(expected in _numbers(" ".join(row)) for row in total_rows)
    last_column = [row[-1] for row in table.rows if row]
    if expected in _numbers(" ".join(last_column)):
        return True
    values = _numbers(" ".join(row[-1] for row in table.rows if row))
    return bool(values) and sum(values, Decimal("0")) == expected


def _timeline_issues(spec: DocumentSpec, plan: TaskList) -> list[Issue]:
    result = _task_result(plan, "build_timeline")
    if result is None:
        return []
    text = " ".join(section_text(section) for section in spec.sections)
    timeline_section = next(
        (
            section
            for section in spec.sections
            if any(
                word in section.heading.lower()
                for word in ("timeline", "schedule", "milestone")
            )
        ),
        None,
    )
    issues = []
    for key in ("start_date", "end_date"):
        value = result.get(key)
        if not isinstance(value, str) or not value:
            continue
        if value not in text:
            issues.append(
                Issue(
                    section_id=timeline_section.id if timeline_section else None,
                    severity="major",
                    description=(
                        f"The timeline {key.replace('_', ' ')} {value} from build_timeline "
                        "does not appear in the document."
                    ),
                )
            )
    return issues


def _failed_task_issues(plan: TaskList) -> list[Issue]:
    issues: list[Issue] = []
    for task in plan.tasks:
        if task.status not in {TaskStatus.FAILED, TaskStatus.SKIPPED}:
            continue
        section_name = task.section_title or task.title
        issues.append(
            Issue(
                section_id=task.section_title and f"s_{task.id}" or None,
                severity="major",
                description=(
                    f"Task {task.id} ({task.title!r}) is {task.status.value}; the section "
                    f"{section_name!r} or its supporting data is missing. "
                    f"Cause: {task.error or 'unknown error'}"
                ),
            )
        )
    return issues


def _placeholder_issues(spec: DocumentSpec) -> list[Issue]:
    issues: list[Issue] = []
    for section in spec.sections:
        text = section_text(section)
        matches = [
            pattern.pattern for pattern in _PLACEHOLDER_PATTERNS if pattern.search(text)
        ]
        if matches:
            issues.append(
                Issue(
                    section_id=section.id,
                    severity="major",
                    description=(
                        f"The section contains placeholder text ({', '.join(matches)}). "
                        "Replace it with substantive content or a stated assumption."
                    ),
                )
            )
    document_text = " ".join(
        [spec.title, spec.subtitle or "", *spec.assumptions]
    )
    if any(pattern.search(document_text) for pattern in _PLACEHOLDER_PATTERNS):
        issues.append(
            Issue(
                severity="major",
                description=(
                    "The document title, subtitle, or assumptions contain placeholder text."
                ),
            )
        )
    return issues


def _conflict_issues(spec: DocumentSpec, analysis: Analysis) -> list[Issue]:
    if not analysis.conflicts:
        return []
    text = " ".join(section_text(section) for section in spec.sections).lower()
    issues = []
    for conflict in analysis.conflicts:
        terms = [
            token
            for token in _WORD_RE.findall(conflict.lower())
            if len(token) > 3 and token not in _CONFLICT_STOPWORDS
        ]
        if not terms:
            continue
        unique_terms = set(terms)
        matches = sum(1 for term in unique_terms if term in text)
        if matches < min(2, len(unique_terms)):
            issues.append(
                Issue(
                    severity="major",
                    description=(
                        "The document does not visibly confront this conflict with its "
                        f"key terms ({', '.join(sorted(unique_terms))}): {conflict}"
                    ),
                )
            )
    return issues


def run_checks(spec: DocumentSpec, plan: TaskList, analysis: Analysis) -> list[Issue]:
    """Run all deterministic structural and consistency checks."""
    issues: list[Issue] = []
    for template_title in template_titles(analysis.doc_type):
        if not any(
            titles_match(section.heading, template_title) for section in spec.sections
        ):
            issues.append(
                Issue(
                    severity="major",
                    description=f"{MISSING_SECTION_PREFIX} {template_title!r}",
                )
            )

    for section in spec.sections:
        callout_only = all(isinstance(block, Callout) for block in section.blocks)
        if not section.heading.strip() or (
            len(section.blocks) < 2 and not callout_only
        ):
            issues.append(
                Issue(
                    section_id=section.id,
                    severity="major",
                    description=(
                        f"Section {section.heading or section.id!r} is empty or has fewer "
                        "than two substantive blocks."
                    ),
                )
            )

    issues.extend(_placeholder_issues(spec))
    issues.extend(_budget_consistency_issues(spec))
    issues.extend(_unsupported_money_issues(spec, plan, analysis))
    issues.extend(_budget_total_issues(spec, plan))
    issues.extend(_timeline_issues(spec, plan))
    issues.extend(_failed_task_issues(plan))
    issues.extend(_conflict_issues(spec, analysis))

    if analysis.missing_info and not spec.assumptions:
        issues.append(
            Issue(
                severity="major",
                description=(
                    "The analysis identified missing information but the document has no "
                    "assumptions section."
                ),
            )
        )
    return issues
