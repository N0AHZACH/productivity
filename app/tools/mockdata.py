"""Clearly labelled fictional company and team data for demos."""

import random
from typing import Any

from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry

_COMPANY_DESCRIPTION = (
    "Return a fictional mock company profile for illustrative documents. Use when a "
    "request needs company context (industry, size, locations, technology stack, fiscal "
    "year) but no real company data was supplied. Input: none. The output is explicitly "
    "labelled MOCK DATA and must never be presented as a real client."
)
_ROSTER_DESCRIPTION = (
    "Return a deterministic fictional team roster for staffing or governance examples. "
    "Use when a document needs named roles, owners, or day rates and the user did not "
    "provide a team. Input: size (1-100, default 5). The output is explicitly labelled "
    "MOCK DATA and includes names, roles, and day rates."
)


class CompanyProfileArgs(BaseModel):
    """No arguments; an empty object is required for uniformity."""


class TeamRosterArgs(BaseModel):
    """Inputs accepted by get_team_roster."""

    size: int = Field(default=5, ge=1, le=100)


_FIRST_NAMES = (
    "Avery",
    "Jordan",
    "Morgan",
    "Riley",
    "Casey",
    "Taylor",
    "Cameron",
    "Drew",
    "Quinn",
    "Reese",
    "Skyler",
    "Parker",
    "Hayden",
    "Rowan",
    "Emerson",
    "Finley",
    "Sage",
    "Blake",
    "Dakota",
    "Elliot",
    "Harper",
    "Jamie",
    "Kendall",
    "Logan",
    "Marlow",
    "Noel",
    "Oakley",
)
_LAST_NAMES = (
    "Chen",
    "Patel",
    "Garcia",
    "Nguyen",
    "Okafor",
    "Smith",
    "Khan",
    "Wilson",
    "Martinez",
    "Brown",
    "Kim",
    "Johnson",
    "Ali",
    "Davis",
    "Rodriguez",
    "Lee",
    "Thompson",
    "Clark",
    "Walker",
    "Hall",
    "Young",
    "Allen",
    "Wright",
    "Scott",
    "Baker",
    "Adams",
    "Turner",
)
_ROLE_RATES = (
    ("Project Manager", 950),
    ("Business Analyst", 700),
    ("Solution Architect", 1100),
    ("Software Engineer", 850),
    ("Data Engineer", 900),
    ("QA Engineer", 600),
    ("Change Manager", 800),
    ("Support Lead", 550),
)


def get_company_profile() -> dict[str, Any]:
    """Return a static fictional company profile."""
    return {
        "mock_data": True,
        "disclaimer": "MOCK DATA: fictional organization for document examples only.",
        "name": "Northstar Retail Group",
        "industry": "Omnichannel retail",
        "size": "1,200 employees across corporate and store operations",
        "annual_revenue": "USD 280 million (illustrative)",
        "locations": ["Chicago, US (headquarters)", "Austin, US", "Toronto, Canada"],
        "tech_stack": [
            "Microsoft Azure",
            "Salesforce Commerce Cloud",
            "Snowflake",
            "Zendesk",
            "Okta",
            "Python services",
        ],
        "fiscal_year": "February-January",
        "strategic_priorities": [
            "Improve customer retention",
            "Reduce manual service contacts",
            "Modernize legacy billing",
        ],
    }


def get_team_roster(args: TeamRosterArgs) -> dict[str, Any]:
    """Return a deterministic fictional roster seeded by team size."""
    rng = random.Random(20261022 + args.size)
    names = [f"{first} {last}" for first in _FIRST_NAMES for last in _LAST_NAMES]
    rng.shuffle(names)
    people = []
    total_day_rate = 0
    for index in range(args.size):
        role, day_rate = _ROLE_RATES[index % len(_ROLE_RATES)]
        people.append(
            {
                "name": names[index],
                "role": role,
                "day_rate": day_rate,
                "allocation_pct": 100 if index < 2 else 50,
            }
        )
        total_day_rate += day_rate
    return {
        "mock_data": True,
        "disclaimer": "MOCK DATA: fictional people for document examples only.",
        "size": args.size,
        "team": people,
        "total_daily_rate": total_day_rate,
        "seed": 20261022 + args.size,
    }


def register(registry: ToolRegistry) -> None:
    """Register this module's tools."""

    @registry.register("get_company_profile", _COMPANY_DESCRIPTION, CompanyProfileArgs)
    def _get_company_profile(args: CompanyProfileArgs) -> dict[str, Any]:
        return get_company_profile()

    @registry.register("get_team_roster", _ROSTER_DESCRIPTION, TeamRosterArgs)
    def _get_team_roster(args: TeamRosterArgs) -> dict[str, Any]:
        return get_team_roster(args)
