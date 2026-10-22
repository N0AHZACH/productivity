"""Deterministic LLM test double; no network, no sleeping."""

import json
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel

from app.core.errors import LLMOutputInvalid
from app.llm.base import LLMResult, Tier

T = TypeVar("T", bound=BaseModel)


@dataclass(slots=True)
class FakeCall:
    """A record of one invocation made against FakeLLM."""

    method: str
    messages: list[dict[str, str]]
    tier: Tier
    temperature: float
    max_tokens: int
    schema: type[BaseModel] | None = None


@dataclass(slots=True)
class FakeLLM:
    """Queue-backed LLM double supporting call-order or schema-based routing."""

    responses: list[Any] = field(default_factory=list)
    by_schema: dict[type[BaseModel], Any] = field(default_factory=dict)
    calls: list[FakeCall] = field(default_factory=list)
    _response_index: int = 0
    _schema_indices: dict[type[BaseModel], int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.by_schema = dict(self.by_schema)

    @classmethod
    def with_json(cls, *objects: BaseModel | dict[str, Any]) -> "FakeLLM":
        """Build a fake whose calls return serialized JSON objects."""
        payloads = [
            obj.model_dump_json() if isinstance(obj, BaseModel) else json.dumps(obj)
            for obj in objects
        ]
        return cls(responses=payloads)

    @classmethod
    def with_schemas(cls, mapping: dict[type[BaseModel], Any]) -> "FakeLLM":
        """Build a schema-routed fake for orchestration tests."""
        return cls(by_schema=mapping)

    def _next_generic(self) -> Any:
        if self._response_index >= len(self.responses):
            raise AssertionError("FakeLLM exhausted its queued responses")
        response = self.responses[self._response_index]
        self._response_index += 1
        return response

    def _next_for_schema(
        self, schema: type[BaseModel], messages: list[dict[str, str]]
    ) -> Any:
        if schema not in self.by_schema:
            if self.responses:
                return self._next_generic()
            raise AssertionError(f"FakeLLM has no response configured for {schema.__name__}")
        configured = self.by_schema[schema]
        if callable(configured) and not isinstance(configured, type):
            return configured(messages)
        items = configured if isinstance(configured, list) else [configured]
        if not items:
            raise AssertionError(f"FakeLLM response queue for {schema.__name__} is empty")
        index = self._schema_indices.get(schema, 0)
        value = items[min(index, len(items) - 1)]
        self._schema_indices[schema] = min(index + 1, len(items) - 1)
        return value

    @staticmethod
    def _raise_if_exception(value: Any) -> None:
        if isinstance(value, BaseException):
            raise value

    def _coerce_text(self, value: Any) -> str:
        self._raise_if_exception(value)
        if isinstance(value, BaseModel):
            return value.model_dump_json()
        if isinstance(value, dict):
            return json.dumps(value)
        if isinstance(value, str):
            return value
        raise AssertionError(f"Unsupported FakeLLM response type: {type(value).__name__}")

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        tier: Tier = "fast",
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> LLMResult:
        """Return the next queued plain response."""
        self.calls.append(
            FakeCall("complete", list(messages), tier, temperature, max_tokens)
        )
        value = self._next_generic()
        return LLMResult(text=self._coerce_text(value), model=f"fake-{tier}")

    async def complete_json(
        self,
        messages: list[dict[str, str]],
        schema: type[T],
        *,
        tier: Tier = "fast",
        temperature: float = 0.2,
        max_tokens: int = 4096,
        max_repairs: int = 2,
    ) -> T:
        """Return and validate the next schema-routed response."""
        self.calls.append(
            FakeCall(
                "complete_json",
                list(messages),
                tier,
                temperature,
                max_tokens,
                schema,
            )
        )
        value = self._next_for_schema(schema, messages)
        self._raise_if_exception(value)
        if isinstance(value, schema):
            return value
        if isinstance(value, dict):
            return schema.model_validate(value)
        if isinstance(value, str):
            try:
                return schema.model_validate_json(value)
            except Exception as exc:
                raise LLMOutputInvalid(
                    f"FakeLLM response was invalid for {schema.__name__}: {exc}"
                ) from exc
        raise LLMOutputInvalid(
            f"FakeLLM cannot convert {type(value).__name__} to {schema.__name__}"
        )
