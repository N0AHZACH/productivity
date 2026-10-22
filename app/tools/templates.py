"""Recommended section templates for each supported document type."""

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from app.models.common import DocType
from app.tools.registry import ToolRegistry

_DESCRIPTION = (
    "Return the recommended section outline and one-line writing guidance for a document "
    "type. Use this before planning writing tasks to choose a complete, professional "
    "structure; the recommendations are a strong default, not a rigid rule. Input: "
    "doc_type, one of proposal, meeting_minutes, project_plan, business_report, "
    "technical_design, sop, product_spec, decision_memo. Returns doc_type and sections "
    "[{title, guidance}]."
)


@dataclass(frozen=True, slots=True)
class TemplateSection:
    """One recommended section and its writing intent."""

    title: str
    guidance: str


TEMPLATE_SECTIONS: dict[DocType, tuple[TemplateSection, ...]] = {
    DocType.PROPOSAL: (
        TemplateSection("Problem and Background", "Establish the client's problem with evidence."),
        TemplateSection("Proposed Solution", "Describe the offering and why it fits."),
        TemplateSection("Scope and Approach", "Set boundaries, method, and exclusions."),
        TemplateSection("Timeline and Milestones", "Show a realistic sequenced delivery plan."),
        TemplateSection("Investment and Budget", "Present consistent, itemized costs."),
        TemplateSection("Risks and Mitigations", "Name material risks and concrete responses."),
        TemplateSection("Why This Team", "Connect relevant capability and governance to delivery."),
        TemplateSection("Next Steps", "Give a clear decision path and owners."),
    ),
    DocType.MEETING_MINUTES: (
        TemplateSection("Meeting Details", "Record date, time, location, and chair."),
        TemplateSection("Attendees and Apologies", "List participants and absentees."),
        TemplateSection("Agenda", "State the topics considered."),
        TemplateSection("Discussion and Decisions", "Capture substantive points and decisions."),
        TemplateSection("Action Items", "Assign every action, owner, and due date."),
        TemplateSection("Open Issues", "Record unresolved matters and next venue."),
        TemplateSection("Next Meeting", "State date, time, and location."),
    ),
    DocType.PROJECT_PLAN: (
        TemplateSection("Objectives and Success Criteria", "Make outcomes measurable."),
        TemplateSection("Scope and Deliverables", "Define included and excluded work."),
        TemplateSection("Workstreams and Governance", "Assign ownership and decision rights."),
        TemplateSection("Timeline and Milestones", "Provide a feasible schedule with gates."),
        TemplateSection("Budget and Resourcing", "Explain costs and staffing assumptions."),
        TemplateSection("Risks and Mitigations", "Prioritize delivery risks and responses."),
        TemplateSection(
            "Dependencies and Next Steps",
            "Name external dependencies and immediate actions.",
        ),
    ),
    DocType.BUSINESS_REPORT: (
        TemplateSection("Context", "Explain why the report exists and its scope."),
        TemplateSection("Key Findings", "Lead with the most decision-relevant evidence."),
        TemplateSection("Analysis", "Interpret the findings and causes."),
        TemplateSection("Recommendations", "Give prioritized actions with owners."),
        TemplateSection("Risks and Considerations", "Surface trade-offs and uncertainty."),
        TemplateSection("Financial Outlook", "Summarize quantified impact and assumptions."),
        TemplateSection("Next Steps", "Set near-term decisions and follow-ups."),
    ),
    DocType.TECHNICAL_DESIGN: (
        TemplateSection("Problem Statement", "Define the technical problem and constraints."),
        TemplateSection("Goals and Non-Goals", "Bound the design explicitly."),
        TemplateSection("Proposed Architecture", "Describe components and interactions."),
        TemplateSection("Data and Interfaces", "Specify data flows, schemas, and contracts."),
        TemplateSection(
            "Migration and Rollout",
            "Explain transition, reversibility, and sequencing.",
        ),
        TemplateSection("Risks and Trade-offs", "Compare alternatives and failure modes."),
        TemplateSection("Testing and Operations", "Define validation, monitoring, and support."),
        TemplateSection("Open Questions", "List decisions still required."),
    ),
    DocType.SOP: (
        TemplateSection(
            "Purpose and Scope",
            "State why the procedure exists and where it applies.",
        ),
        TemplateSection("Roles and Responsibilities", "Assign every step to an accountable role."),
        TemplateSection("Prerequisites", "List access, tools, approvals, and inputs."),
        TemplateSection("Step-by-Step Procedure", "Write ordered, executable steps."),
        TemplateSection(
            "Controls and Compliance",
            "Specify checks, records, and policy obligations.",
        ),
        TemplateSection("Exceptions and Escalation", "Explain deviations and escalation paths."),
        TemplateSection("Revision History", "Record version, date, and approver."),
    ),
    DocType.PRODUCT_SPEC: (
        TemplateSection("Background and Problem", "Explain user pain and business context."),
        TemplateSection("Goals and Success Metrics", "Define measurable outcomes."),
        TemplateSection("Users and Use Cases", "Describe target users and key journeys."),
        TemplateSection("Functional Requirements", "Specify testable capabilities."),
        TemplateSection("User Experience", "Describe the intended interaction and constraints."),
        TemplateSection("Risks and Open Questions", "Surface uncertainty and dependencies."),
        TemplateSection("Release Plan", "Sequence delivery, pilot, and measurement."),
    ),
    DocType.DECISION_MEMO: (
        TemplateSection("Decision Request", "State the decision, owner, and deadline."),
        TemplateSection("Background", "Provide only decision-relevant context."),
        TemplateSection("Options Considered", "Describe credible alternatives fairly."),
        TemplateSection("Evaluation Criteria", "Explain the weighted criteria."),
        TemplateSection("Trade-offs", "Confront constraints honestly and rank priorities."),
        TemplateSection("Recommendation", "Make one clear recommendation with rationale."),
        TemplateSection("Risks and Mitigations", "Name the main downside and response."),
        TemplateSection("Next Steps", "List decisions, owners, and dates."),
    ),
}


class TemplateArgs(BaseModel):
    """Inputs accepted by get_section_template."""

    doc_type: DocType


def section_template(doc_type: DocType) -> tuple[TemplateSection, ...]:
    """Return the internal tuple for a document type."""
    return TEMPLATE_SECTIONS[doc_type]


def template_titles(doc_type: DocType) -> list[str]:
    """Return only the recommended section titles."""
    return [section.title for section in TEMPLATE_SECTIONS[doc_type]]


def get_section_template(args: TemplateArgs) -> dict[str, Any]:
    """Return a JSON-safe template payload."""
    sections = [
        {"title": section.title, "guidance": section.guidance}
        for section in TEMPLATE_SECTIONS[args.doc_type]
    ]
    return {
        "doc_type": args.doc_type.value,
        "sections": sections,
        "count": len(sections),
        "note": "Strong recommended structure; explain any omitted section.",
    }


def register(registry: ToolRegistry) -> None:
    """Register this module's tool."""

    @registry.register("get_section_template", _DESCRIPTION, TemplateArgs)
    def _get_section_template(args: TemplateArgs) -> dict[str, Any]:
        return get_section_template(args)
