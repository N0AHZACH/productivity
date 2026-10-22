"""HTTP contracts for starting and retrieving agent runs."""

from pydantic import BaseModel, Field

from app.models.common import RunStatus
from app.models.plan import TaskList
from app.models.state import AgentState, Analysis, ReflectionReport, TraceEvent


class AgentRequest(BaseModel):
    """User input for one agent run."""

    request: str = Field(min_length=1, description="Natural-language document request")


class AgentRunResponse(BaseModel):
    """Complete, serializable result of one successful run."""

    run_id: str
    status: RunStatus
    plan_source: str | None = None
    analysis: Analysis
    plan: TaskList
    assumptions: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    reflection: list[ReflectionReport] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    trace: list[TraceEvent] = Field(default_factory=list)
    document_url: str
    filename: str

    @classmethod
    def from_state(cls, state: AgentState) -> "AgentRunResponse":
        """Build a response from a completed state."""
        if state.analysis is None or state.plan is None:
            raise ValueError("Cannot build a response from an incomplete run")
        return cls(
            run_id=state.run_id,
            status=state.status,
            plan_source=state.plan_source,
            analysis=state.analysis,
            plan=state.plan,
            assumptions=state.analysis.assumptions,
            conflicts=state.analysis.conflicts,
            reflection=state.reflection,
            warnings=state.warnings,
            trace=state.trace,
            document_url=f"/documents/{state.run_id}",
            filename=f"{state.run_id}.docx",
        )
