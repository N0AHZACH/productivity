"""Deterministic risk-register generation from a curated library."""

import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry

_DESCRIPTION = (
    "Generate a prioritized risk register tailored to the document context. Use for "
    "project plans, proposals, technical designs, decision memos, and business reports "
    "whenever the document must cover what could go wrong and how to respond. Inputs: "
    "context_keywords (for example ['migration', 'compliance', 'vendor']) and count "
    "(default 6, maximum 25). Returns realistic risks selected from a fixed library by "
    "keyword overlap, each with category, likelihood 1-5, impact 1-5, score, rating, and "
    "a concrete mitigation. Selection is deterministic for identical inputs."
)


@dataclass(frozen=True, slots=True)
class _RiskSeed:
    risk_id: str
    title: str
    category: str
    likelihood: int
    impact: int
    mitigation: str
    keywords: str


_RISK_LIBRARY: tuple[_RiskSeed, ...] = (
    _RiskSeed(
        "r01",
        "Scope expands beyond the approved baseline",
        "schedule",
        4,
        4,
        "Freeze the baseline, require a change board for additions, and re-plan "
        "scope at each phase gate.",
        "scope creep requirements change baseline",
    ),
    _RiskSeed(
        "r02",
        "Estimates prove optimistic",
        "schedule",
        4,
        4,
        "Add a 15% schedule buffer, validate estimates with delivery leads, and "
        "track earned progress weekly.",
        "timeline schedule estimate delay",
    ),
    _RiskSeed(
        "r03",
        "A critical dependency arrives late",
        "schedule",
        3,
        4,
        "Identify dependencies early, assign owners, and define a fallback path "
        "for each external hand-off.",
        "dependency vendor integration milestone",
    ),
    _RiskSeed(
        "r04",
        "Key staff are pulled onto competing work",
        "schedule",
        3,
        3,
        "Secure named allocations, document critical knowledge, and cross-train a secondary owner.",
        "resource team staffing capacity",
    ),
    _RiskSeed(
        "r05",
        "A change freeze blocks the planned launch",
        "schedule",
        2,
        4,
        "Align launch windows with the change calendar and prepare an exception "
        "plan before the freeze.",
        "launch release change freeze season",
    ),
    _RiskSeed(
        "r06",
        "Costs exceed the approved budget",
        "budget",
        4,
        4,
        "Track committed spend weekly, hold a 10% contingency, and escalate any "
        "forecast above plan immediately.",
        "budget cost spend overrun funding",
    ),
    _RiskSeed(
        "r07",
        "Vendor pricing rises after contracting",
        "budget",
        3,
        3,
        "Negotiate price protection, cap annual increases, and model a high-cost scenario.",
        "vendor pricing contract cost",
    ),
    _RiskSeed(
        "r08",
        "Integration work reveals hidden costs",
        "budget",
        3,
        4,
        "Run a paid technical discovery, validate estimates with engineering, and "
        "retain contingency for remediation.",
        "integration technical budget cost migration",
    ),
    _RiskSeed(
        "r09",
        "Currency movement changes the business case",
        "budget",
        2,
        3,
        "Set a hedging threshold and report budget in the contracting currency.",
        "currency finance budget international",
    ),
    _RiskSeed(
        "r10",
        "Personal or customer data is mishandled",
        "compliance",
        3,
        5,
        "Complete a data protection review, minimize collected data, encrypt it, "
        "and test incident response.",
        "privacy data gdpr compliance customer",
    ),
    _RiskSeed(
        "r11",
        "Regulation changes during delivery",
        "compliance",
        2,
        4,
        "Assign regulatory monitoring, use design reviews, and document decisions "
        "that may need revision.",
        "regulation compliance legal change",
    ),
    _RiskSeed(
        "r12",
        "Audit evidence is incomplete",
        "compliance",
        3,
        4,
        "Define evidence standards up front and automate collection at each gate.",
        "audit evidence compliance controls",
    ),
    _RiskSeed(
        "r13",
        "Contractual obligations are not met",
        "compliance",
        2,
        5,
        "Map obligations to owners before kickoff and review them in every governance meeting.",
        "contract sla compliance obligation",
    ),
    _RiskSeed(
        "r14",
        "A core integration fails in production",
        "technical",
        3,
        5,
        "Build contract tests, stage realistic data, and maintain a documented manual fallback.",
        "integration api production failure",
    ),
    _RiskSeed(
        "r15",
        "Performance misses expected load",
        "technical",
        3,
        4,
        "Set performance budgets, load-test early, and add monitoring with alert thresholds.",
        "performance load latency scalability",
    ),
    _RiskSeed(
        "r16",
        "Accumulated technical debt slows delivery",
        "technical",
        3,
        3,
        "Reserve capacity for remediation and block new work that bypasses agreed "
        "engineering standards.",
        "technical debt architecture maintainability",
    ),
    _RiskSeed(
        "r17",
        "Model or content quality is inconsistent",
        "technical",
        3,
        4,
        "Create an evaluation set, review samples weekly, and keep human approval "
        "for consequential answers.",
        "ai model quality chatbot content",
    ),
    _RiskSeed(
        "r18",
        "Unplanned downtime disrupts users",
        "technical",
        2,
        5,
        "Design for graceful degradation, test failover, and publish status "
        "communication procedures.",
        "availability downtime resilience operations",
    ),
    _RiskSeed(
        "r19",
        "The organization becomes dependent on one vendor",
        "vendor",
        3,
        4,
        "Require data portability, document exit procedures, and review contract exit terms.",
        "vendor lock-in contract dependency",
    ),
    _RiskSeed(
        "r20",
        "A vendor misses contractual delivery dates",
        "vendor",
        3,
        4,
        "Set milestones with remedies, review delivery evidence monthly, and retain "
        "an alternate supplier option.",
        "vendor delivery contract delay",
    ),
    _RiskSeed(
        "r21",
        "Service levels are unclear or unenforceable",
        "vendor",
        2,
        4,
        "Define measurable SLAs, reporting cadence, and service credits in the contract.",
        "vendor sla service contract",
    ),
    _RiskSeed(
        "r22",
        "Users do not adopt the new process",
        "adoption",
        4,
        4,
        "Engage users in design, provide role-based training, and track adoption "
        "with a clear support path.",
        "adoption users training change",
    ),
    _RiskSeed(
        "r23",
        "Training does not reach frontline staff",
        "adoption",
        3,
        3,
        "Schedule training around shifts, certify team leads, and publish short job aids.",
        "training adoption staff rollout",
    ),
    _RiskSeed(
        "r24",
        "Change resistance slows rollout",
        "adoption",
        3,
        4,
        "Build a sponsor coalition, communicate the reason for change, and address "
        "feedback in each release.",
        "change management resistance culture",
    ),
    _RiskSeed(
        "r25",
        "Credentials or secrets leak",
        "security",
        2,
        5,
        "Use a secrets manager, rotate credentials, enforce least privilege, and "
        "monitor for anomalous access.",
        "security secrets credentials access",
    ),
    _RiskSeed(
        "r26",
        "A software supply-chain dependency is compromised",
        "security",
        2,
        4,
        "Pin and scan dependencies, verify provenance, and keep an inventory of "
        "third-party components.",
        "security supply chain dependency software",
    ),
    _RiskSeed(
        "r27",
        "Access controls are too broad",
        "security",
        2,
        5,
        "Apply least privilege, require periodic access reviews, and log privileged actions.",
        "security access permissions identity",
    ),
    _RiskSeed(
        "r28",
        "Data is lost during migration",
        "data-migration",
        2,
        5,
        "Take verified backups, use reversible batches, and reconcile record counts "
        "before cutover.",
        "data migration loss backup cutover",
    ),
    _RiskSeed(
        "r29",
        "Migration is incomplete at cutover",
        "data-migration",
        3,
        4,
        "Define completeness rules, run parallel reconciliation, and keep the legacy "
        "system read-only until sign-off.",
        "data migration cutover completeness legacy",
    ),
    _RiskSeed(
        "r30",
        "Source data quality is poorer than expected",
        "data-migration",
        4,
        4,
        "Profile source data early, agree cleansing rules with owners, and budget "
        "time for remediation.",
        "data quality migration validation cleansing",
    ),
)


class RiskArgs(BaseModel):
    """Inputs accepted by generate_risk_register."""

    context_keywords: list[str] = Field(default_factory=list)
    count: int = Field(default=6, ge=1, le=25)


def _rating(score: int) -> str:
    if score >= 20:
        return "critical"
    if score >= 12:
        return "high"
    if score >= 6:
        return "medium"
    return "low"


def _tokens(values: list[str]) -> set[str]:
    return {
        token
        for value in values
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if len(token) > 2
    }


def generate_risk_register(args: RiskArgs) -> dict[str, Any]:
    """Select risks deterministically by keyword overlap, then score."""
    requested = _tokens(args.context_keywords)
    ranked: list[tuple[int, int, _RiskSeed]] = []
    for seed in _RISK_LIBRARY:
        seed_tokens = _tokens([seed.title, seed.category, seed.keywords])
        overlap = len(requested & seed_tokens)
        ranked.append((overlap, seed.likelihood * seed.impact, seed))
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2].risk_id))

    risks = []
    for _, score, seed in ranked[: args.count]:
        risks.append(
            {
                "risk_id": seed.risk_id,
                "risk": seed.title,
                "category": seed.category,
                "likelihood": seed.likelihood,
                "impact": seed.impact,
                "score": score,
                "rating": _rating(score),
                "mitigation": seed.mitigation,
            }
        )
    return {
        "risks": risks,
        "count": len(risks),
        "rating_scale": {"likelihood": "1-5", "impact": "1-5", "score": "likelihood * impact"},
        "selection_note": "Selected deterministically from a built-in risk library.",
    }


def register(registry: ToolRegistry) -> None:
    """Register this module's tool."""

    @registry.register("generate_risk_register", _DESCRIPTION, RiskArgs)
    def _generate_risk_register(args: RiskArgs) -> dict[str, Any]:
        return generate_risk_register(args)
