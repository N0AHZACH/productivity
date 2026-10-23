"""Fast deterministic document checks that never hallucinate."""

import json
import re
from decimal import Decimal, InvalidOperation

from app.agent.text_utils import normalize_title, titles_match
from app.models.common import TaskStatus
from app.models.document import (
    Block,
    BulletList,
    Callout,
    DocumentSpec,
    Heading,
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
    r"\bbudget(?:ary)?"
    r"(?:\s+(?:assumption|ceiling|estimate|expectation|limit|target|total))?"
    r"\s*(?:of|is|was|were|at|:)\s*"
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
_FIXED_BUDGET_RE = re.compile(
    r"\b(?:(?:fixed|approved|allocated)(?:\s+[\w-]+){0,2}\s+budget|"
    r"budget\s+(?:is|was|has been)\s+(?:fixed|approved|allocated))\b",
    re.IGNORECASE,
)
_CRITERIA_HEADING_RE = re.compile(
    r"\b(?:success\s+criteria|success\s+measures?|key\s+performance\s+indicators?|kpis?)\b",
    re.IGNORECASE,
)
_VAGUE_TARGET_RE = re.compile(
    r"\b(?:minimum|maximum|target|positive|improve(?:s|d|ment)?|increase(?:s|d)?|"
    r"reduce(?:s|d)?|higher|lower|faster|better|timely|efficient(?:ly)?|"
    r"significant(?:ly)?|substantial(?:ly)?|high|low|robust|seamless|optimal|"
    r"successful(?:ly)?|effective(?:ly)?|adher(?:e|ence|ent)?|resol(?:ve|ution|ved)?)\b",
    re.IGNORECASE,
)
_TOTAL_ROW_LABELS = {"total", "subtotal", "grand total"}
_GENERIC_ROLE_LABELS = {
    "role",
    "category",
    "description",
    "item",
    "type",
    "kind",
    "extra",
    "summary",
}
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


def _budget_basis_issues(spec: DocumentSpec) -> list[Issue]:
    references = [
        section
        for section in spec.sections
        if _FIXED_BUDGET_RE.search(section_text(section))
    ]
    if not references:
        return []
    if any(
        "budget" in assumption.lower() and _money_amounts(assumption)
        for assumption in spec.assumptions
    ):
        return []
    for section in spec.sections:
        for sentence in re.split(r"(?<=[.!?])\s+", section_text(section)):
            if _FIXED_BUDGET_RE.search(sentence) and _money_amounts(sentence):
                return []
    section = next(
        (candidate for candidate in references if "budget" in candidate.heading.lower()),
        references[0],
    )
    return [
        Issue(
            section_id=section.id,
            severity="major",
            description=(
                "The document refers to a fixed/approved budget but never states the "
                "amount. State the approved amount, or say the amount was not provided "
                "and label the estimated budget as a planning assumption or ceiling."
            ),
        )
    ]


def _block_items(block: Block) -> list[str]:
    if isinstance(block, Paragraph):
        return [block.text]
    if isinstance(block, (BulletList, NumberedList)):
        return block.items
    if isinstance(block, Table):
        return [" | ".join(row) for row in block.rows]
    if isinstance(block, Callout):
        return [block.text]
    return []


def _criteria_blocks(section: Section) -> list[Block]:
    for index, block in enumerate(section.blocks):
        if isinstance(block, Heading) and _CRITERIA_HEADING_RE.search(block.text):
            nested: list[Block] = []
            for candidate in section.blocks[index + 1 :]:
                if isinstance(candidate, Heading):
                    break
                nested.append(candidate)
            return nested
    if _CRITERIA_HEADING_RE.search(section.heading):
        return [block for block in section.blocks if not isinstance(block, Heading)]
    return []


def _measurement_issues(spec: DocumentSpec) -> list[Issue]:
    issues: list[Issue] = []
    for section in spec.sections:
        vague: list[str] = []
        for block in _criteria_blocks(section):
            if isinstance(block, Paragraph) and block.text.strip().endswith(":"):
                continue
            for item in _block_items(block):
                if _VAGUE_TARGET_RE.search(item) and not re.search(r"\d", item):
                    vague.append(item.strip())
        if not vague:
            continue
        examples = "; ".join(repr(item) for item in vague[:2])
        remaining = f" (+{len(vague) - 2} more)" if len(vague) > 2 else ""
        issues.append(
            Issue(
                section_id=section.id,
                severity="major",
                description=(
                    f"The success criteria contain unquantified target(s): {examples}"
                    f"{remaining}. Add a numeric threshold, unit, and timeframe, or state "
                    "a labelled assumption."
                ),
            )
        )
    return issues


def _role_columns(table: Table, *, budget: bool) -> list[int]:
    headers = [normalize_title(column) for column in table.columns]
    has_headcount = any("headcount" in header for header in headers)
    if budget:
        if not has_headcount:
            return []
        return [
            index
            for index, header in enumerate(headers)
            if any(term in header for term in ("role", "category", "description", "item"))
        ]
    if has_headcount:
        return []
    if not any(
        term in header
        for header in headers
        for term in ("team", "member", "workstream", "owner", "staff", "name")
    ):
        return []
    return [
        index
        for index, header in enumerate(headers)
        if any(term in header for term in ("role", "owner", "lead", "assigned"))
    ]


def _role_names(table: Table, role_index: int, *, budget: bool) -> list[str]:
    headers = [normalize_title(column) for column in table.columns]
    headcount_index = next(
        (index for index, header in enumerate(headers) if "headcount" in header),
        None,
    )
    header = headers[role_index] if role_index < len(headers) else ""
    extract_parenthetical = not budget and "role" not in header
    names: list[str] = []
    seen: set[str] = set()
    for row in table.rows:
        if len(row) <= role_index:
            continue
        name = row[role_index].strip()
        if extract_parenthetical:
            matches = re.findall(r"\(([^()]*)\)", name)
            if not matches:
                continue
            name = matches[-1].strip()
        key = normalize_title(name)
        if (
            not key
            or key in _TOTAL_ROW_LABELS
            or key.startswith(("total ", "subtotal ", "grand total "))
            or key in _GENERIC_ROLE_LABELS
        ):
            continue
        if (
            budget
            and headcount_index is not None
            and (len(row) <= headcount_index or not re.search(r"\d", row[headcount_index]))
        ):
            continue
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def _role_alignment_issues(spec: DocumentSpec) -> list[Issue]:
    team_tables: list[tuple[Section, list[list[str]]]] = []
    budget_tables: list[tuple[Section, list[str]]] = []
    for section in spec.sections:
        for block in section.blocks:
            if not isinstance(block, Table):
                continue
            budget_columns = _role_columns(block, budget=True)
            if budget_columns:
                roles: list[str] = []
                for index in budget_columns:
                    for role in _role_names(block, index, budget=True):
                        if not any(titles_match(role, found) for found in roles):
                            roles.append(role)
                if len(roles) >= 2:
                    budget_tables.append((section, roles))
                continue
            team_columns = _role_columns(block, budget=False)
            role_sets: list[list[str]] = []
            for index in team_columns:
                roles = _role_names(block, index, budget=False)
                if len(roles) >= 2:
                    role_sets.append(roles)
            if role_sets:
                team_tables.append((section, role_sets))
    if not team_tables or not budget_tables:
        return []

    primary_team_tables = [
        (section, role_sets)
        for section, role_sets in team_tables
        if any(
            term in normalize_title(section.heading)
            for term in ("workstream", "governance", "team", "role")
        )
    ]
    if primary_team_tables:
        team_tables = primary_team_tables

    budget_roles: list[str] = []
    seen: set[str] = set()
    for _, roles in budget_tables:
        for role in roles:
            key = normalize_title(role)
            if key not in seen:
                seen.add(key)
                budget_roles.append(role)

    issues: list[Issue] = []
    for section, role_sets in team_tables:
        best: tuple[int, list[str], list[str]] | None = None
        for roles in role_sets:
            team_only = [
                role
                for role in roles
                if not any(
                    titles_match(role, budget_role) for budget_role in budget_roles
                )
            ]
            budget_only = [
                role
                for role in budget_roles
                if not any(titles_match(role, team_role) for team_role in roles)
            ]
            score = len(team_only) + len(budget_only)
            if best is None or score < best[0]:
                best = (score, team_only, budget_only)
        if best is None or best[0] == 0:
            continue
        _, team_only, budget_only = best
        details = []
        if team_only:
            details.append("team-only: " + ", ".join(team_only))
        if budget_only:
            details.append("budget-only: " + ", ".join(budget_only))
        issues.append(
            Issue(
                section_id=section.id,
                severity="major",
                description=(
                    "The team/workstream roles do not align with the budget roles ("
                    + "; ".join(details)
                    + "). Use one role taxonomy in both tables or add an "
                    "operational/budget-role mapping column to the team table using "
                    "these exact budget labels: "
                    + ", ".join(budget_roles)
                    + "; do not merely state that the roles match."
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


def _is_budget_table(table: Table) -> bool:
    headers = [normalize_title(column) for column in table.columns]
    if any(
        any(term in header for term in ("line total", "amount", "cost", "budget"))
        for header in headers
    ):
        return True
    return any(
        any(normalize_title(cell) in _TOTAL_ROW_LABELS for cell in row[:2])
        for row in table.rows
    )


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
            if not isinstance(block, Table) or not _is_budget_table(block):
                continue
            if _table_matches_budget(block, expected):
                continue
            table_problem = Issue(
                section_id=section.id,
                severity="major",
                description=(
                    f"The table in {section.heading!r} does not reconcile to the "
                    f"estimate_budget total of {expected:,.2f}. Use the tool total exactly "
                    "and include the subtotal, contingency, and total rows needed to add up."
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
    # A timeline tool can support a section that speaks in phases or weeks (for example
    # a memo's next steps). Only enforce the tool dates where the document itself
    # presents a schedule.
    if timeline_section is None:
        return []
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
    issues.extend(_budget_basis_issues(spec))
    issues.extend(_unsupported_money_issues(spec, plan, analysis))
    issues.extend(_budget_total_issues(spec, plan))
    issues.extend(_measurement_issues(spec))
    issues.extend(_role_alignment_issues(spec))
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
