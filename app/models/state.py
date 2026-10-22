"""Mutable state shared across one agent run."""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.common import DocType, RunStatus
from app.models.document import DocumentSpec
from app.models.plan import TaskList


def _now() -> datetime:
    return datetime.now(UTC)


class Analysis(BaseModel):
    """Structured interpretation of the user's request."""

    doc_type: DocType
    objective: str
    audience: str
    known_facts: list[str] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    complexity: str = "standard"
    in_scope: bool = True
    refusal_reason: str | None = None


class Issue(BaseModel):
    """One actionable review finding."""

    section_id: str | None = None
    severity: str = "minor"
    description: str


class ReflectionReport(BaseModel):
    """Outcome of one deterministic-and-LLM review loop."""

    loop: int
    passed: bool
    issues: list[Issue] = Field(default_factory=list)
    fixed_sections: list[str] = Field(default_factory=list)


class TraceEvent(BaseModel):
    """Timestamped observability record for one agent stage."""

    ts: datetime = Field(default_factory=_now)
    stage: str
    message: str
    duration_ms: int | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class Principal(BaseModel):
    """Caller identity, ready for multi-tenant use."""

    tenant_id: str = "default"
    user_id: str = "anonymous"
    plan: str = "free"


class AgentState(BaseModel):
    """Everything known and produced during one run."""

    run_id: str
    principal: Principal = Field(default_factory=Principal)
    request: str
    analysis: Analysis | None = None
    plan: TaskList | None = None
    spec: DocumentSpec | None = None
    plan_source: str | None = None
    reflection: list[ReflectionReport] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    trace: list[TraceEvent] = Field(default_factory=list)
    status: RunStatus = RunStatus.QUEUED
    document_path: str | None = None
    created_at: datetime = Field(default_factory=_now)
