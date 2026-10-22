"""Exact decimal budget estimation."""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry

_CENT = Decimal("0.01")
_DESCRIPTION = (
    "Calculate an itemized project or proposal budget using exact decimal arithmetic. "
    "Use whenever the document needs cost, investment, budget, or total-cost figures. "
    "Required input: roles, a non-empty list of {role, headcount, day_rate, days}. "
    "Optional inputs: extras [{name, cost}], contingency_pct (default 10), and a "
    "three-letter currency (default USD). Returns line_items with line totals, then "
    "subtotal, contingency, total, and formatted_total as strings. All totals are "
    "exactly consistent for direct use in tables."
)


class RoleLine(BaseModel):
    """Staffing line item."""

    role: str = Field(min_length=1)
    headcount: int = Field(ge=1, le=10_000)
    day_rate: Decimal = Field(ge=0)
    days: int = Field(ge=1, le=3_650)


class ExtraLine(BaseModel):
    """Non-staff cost line item."""

    name: str = Field(min_length=1)
    cost: Decimal = Field(ge=0)


class BudgetArgs(BaseModel):
    """Inputs accepted by estimate_budget."""

    roles: list[RoleLine] = Field(min_length=1)
    extras: list[ExtraLine] = Field(default_factory=list)
    contingency_pct: Decimal = Field(default=Decimal("10"), ge=0, le=100)
    currency: str = Field(default="USD", pattern=r"^[A-Za-z]{3}$")


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def estimate_budget(args: BudgetArgs) -> dict[str, Any]:
    """Compute a deterministic budget with decimal-exact totals."""
    line_items: list[dict[str, Any]] = []
    subtotal = Decimal("0")

    for role in args.roles:
        line_total = _money(role.day_rate * role.headcount * role.days)
        subtotal += line_total
        line_items.append(
            {
                "kind": "role",
                "description": role.role,
                "headcount": role.headcount,
                "day_rate": str(_money(role.day_rate)),
                "days": role.days,
                "line_total": str(line_total),
            }
        )
    for extra in args.extras:
        line_total = _money(extra.cost)
        subtotal += line_total
        line_items.append(
            {
                "kind": "extra",
                "description": extra.name,
                "headcount": None,
                "day_rate": None,
                "days": None,
                "line_total": str(line_total),
            }
        )

    subtotal = _money(subtotal)
    contingency = _money(subtotal * args.contingency_pct / Decimal("100"))
    total = _money(subtotal + contingency)
    currency = args.currency.upper()
    return {
        "currency": currency,
        "line_items": line_items,
        "subtotal": str(subtotal),
        "contingency_pct": str(_money(args.contingency_pct)),
        "contingency": str(contingency),
        "total": str(total),
        "formatted_subtotal": f"{currency} {subtotal:,.2f}",
        "formatted_total": f"{currency} {total:,.2f}",
        "arithmetic": "subtotal + contingency = total",
    }


def register(registry: ToolRegistry) -> None:
    """Register this module's tool."""

    @registry.register("estimate_budget", _DESCRIPTION, BudgetArgs)
    def _estimate_budget(args: BudgetArgs) -> dict[str, Any]:
        return estimate_budget(args)
