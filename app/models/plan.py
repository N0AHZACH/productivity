"""Task-plan contracts produced by the planner."""

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.models.common import DocType, TaskStatus


class Task(BaseModel):
    """One step in an agent-generated document plan."""

    id: str = Field(pattern=r"^t[0-9]+$")
    title: str
    description: str
    kind: Literal["tool", "write_section", "analysis"]
    tool: str | None = None
    tool_args: dict[str, Any] = Field(default_factory=dict)
    section_title: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    acceptance: str
    status: TaskStatus = TaskStatus.PENDING
    result: Any = None
    error: str | None = None
    attempts: int = 0

    @model_validator(mode="after")
    def _tool_required(self) -> "Task":
        if self.kind == "tool" and not self.tool:
            raise ValueError("tool tasks must name a tool")
        return self


class TaskList(BaseModel):
    """A validated DAG of tasks for one document run."""

    objective: str
    doc_type: DocType
    tasks: list[Task] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_dag(self) -> "TaskList":
        ids = [task.id for task in self.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate task ids")
        known = set(ids)
        for task in self.tasks:
            missing = set(task.depends_on) - known
            if missing:
                raise ValueError(f"{task.id} depends on unknown tasks {sorted(missing)}")
        self.topological_layers()
        return self

    def topological_layers(self) -> list[list[Task]]:
        """Group tasks into layers; tasks in one layer can run concurrently."""
        remaining = {task.id: task for task in self.tasks}
        done: set[str] = set()
        layers: list[list[Task]] = []
        while remaining:
            ready = [task for task in remaining.values() if set(task.depends_on) <= done]
            if not ready:
                raise ValueError("cycle detected in task dependencies")
            layers.append(ready)
            for task in ready:
                done.add(task.id)
                del remaining[task.id]
        return layers
