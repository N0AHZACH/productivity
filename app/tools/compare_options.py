"""Weighted option comparison without guessing missing scores."""

from typing import Any

from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry

_DESCRIPTION = (
    "Score and rank options against weighted decision criteria. Use for decision memos, "
    "business cases, vendor selections, architecture choices, or any request that asks "
    "for a comparison, trade-off, or recommendation. Required inputs: options (at least "
    "two names), criteria [{name, weight}], and scores as an object mapping option name "
    "to criterion name to a numeric score. Returns normalized weights, a score matrix, "
    "weighted totals, a ranking, and an explicit missing list. Missing scores are never "
    "invented; incomplete options are excluded from the ranking."
)


class Criterion(BaseModel):
    """One weighted decision criterion."""

    name: str = Field(min_length=1)
    weight: float = Field(gt=0)


class CompareArgs(BaseModel):
    """Inputs accepted by compare_options."""

    options: list[str] = Field(min_length=2)
    criteria: list[Criterion] = Field(min_length=1)
    scores: dict[str, dict[str, float]] = Field(default_factory=dict)


def compare_options(args: CompareArgs) -> dict[str, Any]:
    """Normalize weights, compute totals, and rank fully scored options."""
    if len(set(args.options)) != len(args.options):
        return {"error": "Option names must be unique", "missing": []}
    if len({criterion.name for criterion in args.criteria}) != len(args.criteria):
        return {"error": "Criterion names must be unique", "missing": []}

    total_weight = sum(criterion.weight for criterion in args.criteria)
    normalized = {criterion.name: criterion.weight / total_weight for criterion in args.criteria}
    missing: list[dict[str, str]] = []
    weighted: dict[str, dict[str, Any]] = {}
    for option in args.options:
        by_criterion: dict[str, float | None] = {}
        weighted_total = 0.0
        complete = True
        for criterion in args.criteria:
            raw = args.scores.get(option, {}).get(criterion.name)
            if raw is None:
                complete = False
                missing.append({"option": option, "criterion": criterion.name})
                by_criterion[criterion.name] = None
                continue
            contribution = float(raw) * normalized[criterion.name]
            by_criterion[criterion.name] = round(contribution, 4)
            weighted_total += contribution
        weighted[option] = {
            "complete": complete,
            "by_criterion": by_criterion,
            "weighted_total": round(weighted_total, 4) if complete else None,
        }

    ranking = sorted(
        (
            (option, float(data["weighted_total"]))
            for option, data in weighted.items()
            if data["complete"]
        ),
        key=lambda item: (-item[1], item[0]),
    )
    return {
        "criteria": [
            {
                "name": criterion.name,
                "weight": criterion.weight,
                "normalized_weight": round(normalized[criterion.name], 4),
            }
            for criterion in args.criteria
        ],
        "matrix": weighted,
        "ranking": [
            {"rank": index, "option": option, "weighted_score": score}
            for index, (option, score) in enumerate(ranking, start=1)
        ],
        "incomplete_options": [option for option, data in weighted.items() if not data["complete"]],
        "missing": missing,
        "method": "Weighted sum; criterion weights normalized to 1.0.",
    }


def register(registry: ToolRegistry) -> None:
    """Register this module's tool."""

    @registry.register("compare_options", _DESCRIPTION, CompareArgs)
    def _compare_options(args: CompareArgs) -> dict[str, Any]:
        return compare_options(args)
