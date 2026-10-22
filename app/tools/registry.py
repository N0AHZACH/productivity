"""Tool registration, schema discovery, validation, and safe invocation."""

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from app.core.errors import ToolError


class ToolSpec(BaseModel):
    """Planner-facing metadata for one deterministic tool."""

    name: str
    description: str
    parameters: dict[str, Any]


ToolFunction = Callable[[Any], dict[str, Any] | Awaitable[dict[str, Any]]]


@dataclass(slots=True)
class _RegisteredTool:
    spec: ToolSpec
    args_model: type[BaseModel]
    function: ToolFunction


def _format_validation_error(tool_name: str, exc: ValidationError) -> str:
    details = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ())) or "arguments"
        details.append(f"{location}: {error.get('msg', 'invalid value')}")
    return f"Invalid arguments for tool {tool_name!r}: " + "; ".join(details)


def _ensure_json_safe(tool_name: str, result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise ToolError(f"Tool {tool_name!r} returned {type(result).__name__}, expected an object")
    try:
        json.dumps(result, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ToolError(f"Tool {tool_name!r} returned a non-JSON value: {exc}") from exc
    return result


class ToolRegistry:
    """Registry of pure business tools callable by the executor."""

    def __init__(self) -> None:
        self._tools: dict[str, _RegisteredTool] = {}

    def register(
        self,
        name: str,
        description: str,
        args: type[BaseModel],
    ) -> Callable[[ToolFunction], ToolFunction]:
        """Register a sync or async tool under a unique name."""
        if not name.strip():
            raise ValueError("Tool name must not be empty")
        if not description.strip():
            raise ValueError("Tool description must not be empty")

        def decorator(function: ToolFunction) -> ToolFunction:
            if name in self._tools:
                raise ValueError(f"Tool {name!r} is already registered")
            spec = ToolSpec(
                name=name,
                description=description,
                parameters=args.model_json_schema(),
            )
            self._tools[name] = _RegisteredTool(spec=spec, args_model=args, function=function)
            return function

        return decorator

    def describe(self) -> list[ToolSpec]:
        """Return tool metadata in registration order."""
        return [entry.spec for entry in self._tools.values()]

    def has(self, name: str) -> bool:
        """Return whether a tool is registered."""
        return name in self._tools

    def names(self) -> list[str]:
        """Return all registered tool names."""
        return list(self._tools)

    def args_model(self, name: str) -> type[BaseModel]:
        """Expose a tool's args model for plan validation without execution."""
        entry = self._entry(name)
        return entry.args_model

    def validate_args(self, name: str, args: dict[str, Any]) -> list[str]:
        """Validate arguments without running the tool."""
        entry = self._entry(name)
        try:
            entry.args_model.model_validate(args)
        except ValidationError as exc:
            return [error["msg"] for error in exc.errors()]
        return []

    def _entry(self, name: str) -> _RegisteredTool:
        entry = self._tools.get(name)
        if entry is None:
            known = ", ".join(sorted(self._tools)) or "none"
            raise ToolError(f"Unknown tool {name!r}. Available tools: {known}")
        return entry

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Validate arguments, run the tool, and return a JSON-safe object."""
        entry = self._entry(name)
        try:
            validated = entry.args_model.model_validate(args)
        except ValidationError as exc:
            raise ToolError(_format_validation_error(name, exc)) from exc

        try:
            if inspect.iscoroutinefunction(entry.function):
                result = await entry.function(validated)
            else:
                result = await asyncio.to_thread(entry.function, validated)
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(f"Tool {name!r} failed: {exc}") from exc
        return _ensure_json_safe(name, result)


def default_registry() -> ToolRegistry:
    """Build a registry containing all production tools."""
    from app.tools import budget, compare_options, mockdata, risk, schedule, templates

    registry = ToolRegistry()
    budget.register(registry)
    schedule.register(registry)
    risk.register(registry)
    compare_options.register(registry)
    mockdata.register(registry)
    templates.register(registry)
    return registry
