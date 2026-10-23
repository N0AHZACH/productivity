"""Reconcile inferred assumptions with deterministic tool results."""

import re
from decimal import Decimal, InvalidOperation

from app.models.common import TaskStatus
from app.models.document import Table
from app.models.state import AgentState

_AMOUNT_RE = re.compile(r"(?:[A-Z]{3}\s*)?[$€£]?\s*(\d[\d,]*(?:\.\d+)?)")
_BUDGET_RE = re.compile(r"\bbudget\b", re.IGNORECASE)
_BUDGET_AMOUNT_RE = re.compile(
    r"\bbudget(?:ary)?"
    r"(?:\s+(?:assumption|ceiling|estimate|expectation|limit|target|total))?"
    r"(?:\s*(?:of|is|was|were|at|:))?"
    r"\s*(?:(?:USD|EUR|GBP)\s*|[$€£]\s*)?"
    r"(?P<amount>\d[\d,]*(?:\.\d+)?)",
    re.IGNORECASE,
)
_INFERRED_RE = re.compile(
    r"\b(?:assum\w*|not\s+(?:provided|specified|given)|working\s+budget|estimate\w*)\b",
    re.IGNORECASE,
)


def _amounts(text: str) -> set[Decimal]:
    values: set[Decimal] = set()
    for match in _AMOUNT_RE.finditer(text):
        try:
            values.add(Decimal(match.group(1).replace(",", "")))
        except InvalidOperation:
            continue
    return values


def _budget_total(state: AgentState) -> tuple[Decimal, str] | None:
    if state.plan is None:
        return None
    for task in state.plan.tasks:
        if (
            task.kind != "tool"
            or task.tool != "estimate_budget"
            or task.status is not TaskStatus.DONE
            or not isinstance(task.result, dict)
            or "total" not in task.result
        ):
            continue
        try:
            total = Decimal(str(task.result["total"]))
        except InvalidOperation:
            return None
        currency = str(task.result.get("currency", "USD")).upper()
        return total, currency
    return None


def _budget_result(state: AgentState) -> dict[str, object] | None:
    if state.plan is None:
        return None
    for task in state.plan.tasks:
        if (
            task.kind == "tool"
            and task.tool == "estimate_budget"
            and task.status is TaskStatus.DONE
            and isinstance(task.result, dict)
            and "total" in task.result
        ):
            return task.result
    return None


def _line_total_index(table: Table) -> int | None:
    for index, header in enumerate(table.columns):
        normalized = header.lower()
        if "line total" in normalized or "amount" in normalized or "cost" in normalized:
            return index
    return None


def _summary_row(row: list[str]) -> bool:
    label = " ".join(row[:2]).lower()
    return any(term in label for term in ("total", "subtotal", "contingency"))


def _cell_amount(text: str) -> Decimal | None:
    match = _AMOUNT_RE.search(text)
    if match is None:
        return None
    try:
        return Decimal(match.group(1).replace(",", ""))
    except InvalidOperation:
        return None


def ensure_budget_table_totals(state: AgentState) -> None:
    """Add subtotal, contingency, and total rows when line items omit them."""
    if state.spec is None:
        return
    result = _budget_result(state)
    if result is None:
        return
    try:
        subtotal = Decimal(str(result.get("subtotal")))
        contingency = Decimal(str(result.get("contingency")))
        total = Decimal(str(result["total"]))
    except (InvalidOperation, KeyError):
        return
    if subtotal + contingency != total:
        return
    contingency_pct = str(result.get("contingency_pct", "10.00"))

    for section in state.spec.sections:
        if "budget" not in section.heading.lower():
            continue
        for block in section.blocks:
            if not isinstance(block, Table):
                continue
            line_index = _line_total_index(block)
            if line_index is None:
                continue
            if any(_summary_row(row) for row in block.rows):
                continue
            values = [
                amount
                for row in block.rows
                if len(row) > line_index
                and (amount := _cell_amount(row[line_index])) is not None
            ]
            if not values or sum(values, Decimal("0")) != subtotal:
                continue

            new_rows: list[list[str]] = []
            for label, amount in (
                ("Subtotal", subtotal),
                (f"Contingency ({contingency_pct}%)", contingency),
                ("Total Project Budget", total),
            ):
                row = [""] * len(block.columns)
                row[0] = label
                row[line_index] = f"{amount:,.2f}"
                new_rows.append(row)
            block.rows.extend(new_rows)
            return


def reconcile_budget_assumptions(state: AgentState) -> None:
    """Make inferred budget assumptions agree with the deterministic tool total."""
    if state.analysis is None:
        return
    total_info = _budget_total(state)
    if total_info is None:
        return
    total, currency = total_info
    known_text = " ".join([state.request, *state.analysis.known_facts])
    if _BUDGET_AMOUNT_RE.search(known_text):
        return

    replacement = (
        "The specific budget amount was not provided; a calculated planning budget "
        f"of {currency} {total:,.2f} is used and must be confirmed before approval."
    )
    updated: list[str] = []
    changed = False
    has_quantified = False
    for assumption in state.analysis.assumptions:
        amounts = _amounts(assumption)
        inferred = bool(_BUDGET_RE.search(assumption) and _INFERRED_RE.search(assumption))
        if _BUDGET_RE.search(assumption) and amounts:
            has_quantified = True
        if inferred and amounts and total not in amounts:
            updated.append(replacement)
            changed = True
        else:
            updated.append(assumption)

    if not has_quantified and "budget" in known_text.lower():
        if any(
            _BUDGET_RE.search(item) and _INFERRED_RE.search(item) for item in updated
        ):
            updated = [
                replacement
                if _BUDGET_RE.search(item) and _INFERRED_RE.search(item) and not _amounts(item)
                else item
                for item in updated
            ]
        else:
            updated.append(replacement)
        changed = True

    if changed:
        state.analysis.assumptions = list(dict.fromkeys(updated))
        if state.spec is not None:
            state.spec.assumptions = list(
                dict.fromkeys(
                    replacement
                    if _BUDGET_RE.search(item)
                    and _INFERRED_RE.search(item)
                    and _amounts(item)
                    and total not in _amounts(item)
                    else item
                    for item in state.spec.assumptions
                )
            )
