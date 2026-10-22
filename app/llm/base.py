"""Provider-neutral LLM contracts."""

from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)
Tier = Literal["fast", "strong"]


class LLMResult(BaseModel):
    """Text returned by a completion plus usage metadata."""

    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMClient(Protocol):
    """The only LLM interface application code may depend on."""

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        tier: Tier = "fast",
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> LLMResult: ...

    async def complete_json(
        self,
        messages: list[dict[str, str]],
        schema: type[T],
        *,
        tier: Tier = "fast",
        temperature: float = 0.2,
        max_tokens: int = 4096,
        max_repairs: int = 2,
    ) -> T: ...
