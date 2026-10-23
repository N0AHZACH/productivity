"""Weekend-aware deterministic project timelines."""

from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel, Field

from app.core.errors import ToolError
from app.tools.registry import ToolRegistry

_DESCRIPTION = (
    "Build a weekend-aware project timeline from ordered phases. Use for project plans, "
    "proposals, migration schedules, and implementation roadmaps whenever dates or "
    "milestones are needed. Required input: phases, a non-empty list of {name, weeks}. "
    "Optional input: start_date as YYYY-MM-DD (defaults to the next Monday) and "
    "milestones (default true). Returns total_weeks, overall start_date and end_date, "
    "each phase's start/end ISO dates, and a milestone at the end of each phase. "
    "Saturdays and Sundays are skipped."
)


class Phase(BaseModel):
    """A sequential project phase."""

    name: str = Field(min_length=1)
    weeks: int = Field(ge=1, le=104)


class TimelineArgs(BaseModel):
    """Inputs accepted by build_timeline."""

    start_date: str | None = None
    phases: list[Phase] = Field(min_length=1)
    milestones: bool = True


def _next_monday(today: date) -> date:
    days_ahead = (7 - today.weekday()) % 7
    return today + timedelta(days=days_ahead or 7)


def _advance_to_workday(value: date) -> date:
    while value.weekday() >= 5:
        value += timedelta(days=1)
    return value


def _add_workdays(start: date, workdays: int) -> date:
    current = start
    remaining = workdays
    while remaining > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current


def build_timeline(args: TimelineArgs) -> dict[str, Any]:
    """Build a deterministic phase schedule, skipping weekends."""
    requested_start: str | None = None
    if args.start_date:
        try:
            current = date.fromisoformat(args.start_date)
        except ValueError as exc:
            raise ToolError(
                f"start_date must be an ISO date (YYYY-MM-DD), got {args.start_date!r}"
            ) from exc
        if current < date.today():
            # Models without a reliable clock invent past years for relative briefs
            # ("before Q1"). Scheduling from the next Monday keeps the plan usable.
            requested_start = args.start_date
            current = _next_monday(date.today())
    else:
        current = _next_monday(date.today())
    current = _advance_to_workday(current)

    overall_start = current
    phases: list[dict[str, Any]] = []
    milestones: list[dict[str, str]] = []
    for phase in args.phases:
        phase_start = current
        phase_end = _add_workdays(phase_start, phase.weeks * 5 - 1)
        phases.append(
            {
                "name": phase.name,
                "weeks": phase.weeks,
                "working_days": phase.weeks * 5,
                "start_date": phase_start.isoformat(),
                "end_date": phase_end.isoformat(),
            }
        )
        if args.milestones:
            milestones.append(
                {
                    "name": f"{phase.name} complete",
                    "date": phase_end.isoformat(),
                    "phase": phase.name,
                }
            )
        current = _advance_to_workday(phase_end + timedelta(days=1))

    overall_end = date.fromisoformat(phases[-1]["end_date"])
    result: dict[str, Any] = {
        "start_date": overall_start.isoformat(),
        "end_date": overall_end.isoformat(),
        "total_weeks": sum(phase.weeks for phase in args.phases),
        "phases": phases,
        "milestones": milestones,
        "calendar_note": "Schedule uses five working days per week and skips weekends.",
    }
    if requested_start is not None:
        result["requested_start_date"] = requested_start
        result["start_date_adjusted"] = True
        result["adjustment_note"] = (
            f"Requested start {requested_start} is in the past; the schedule starts on "
            f"the next Monday ({overall_start.isoformat()})."
        )
    return result


def register(registry: ToolRegistry) -> None:
    """Register this module's tool."""

    @registry.register("build_timeline", _DESCRIPTION, TimelineArgs)
    def _build_timeline(args: TimelineArgs) -> dict[str, Any]:
        return build_timeline(args)
