"""Enumerations shared across the agent."""

from enum import StrEnum


class DocType(StrEnum):
    """Document categories the agent can produce."""

    PROPOSAL = "proposal"
    MEETING_MINUTES = "meeting_minutes"
    PROJECT_PLAN = "project_plan"
    BUSINESS_REPORT = "business_report"
    TECHNICAL_DESIGN = "technical_design"
    SOP = "sop"
    PRODUCT_SPEC = "product_spec"
    DECISION_MEMO = "decision_memo"


class TaskStatus(StrEnum):
    """Lifecycle of one planned task."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class RunStatus(StrEnum):
    """Lifecycle and terminal quality of an agent run."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED = "failed"
