"""OpenAI-compatible async client used for Groq, Ollama, and Gemini."""

import asyncio
import inspect
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import openai
import structlog
from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError
from tenacity import AsyncRetrying, RetryCallState, retry_if_exception, stop_after_attempt
from tenacity.wait import wait_exponential_jitter

from app.core.errors import LLMOutputInvalid, LLMProviderError
from app.llm.base import LLMResult, Tier

logger = structlog.get_logger(__name__)
UsageSink = Callable[[int, int, str], Awaitable[None] | None]
T = TypeVar("T", bound=BaseModel)


def _status_code(exc: BaseException) -> int | None:
    status = getattr(exc, "status_code", None)
    return status if isinstance(status, int) else None


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(
        exc,
        (openai.APITimeoutError, openai.APIConnectionError, openai.RateLimitError, TimeoutError),
    ):
        return True
    status = _status_code(exc)
    return status == 429 or (status is not None and 500 <= status < 600)


def _retry_after_seconds(exc: BaseException) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    raw = headers.get("retry-after") or headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return max(0.0, min(float(raw), 60.0))
    except (TypeError, ValueError):
        return None


class _RetryAfterExponential:
    """Honor Retry-After when provided, otherwise exponential backoff with jitter."""

    def __init__(self, base_delay_s: float) -> None:
        self._fallback = wait_exponential_jitter(
            initial=max(base_delay_s, 0.0),
            max=20.0,
            jitter=max(base_delay_s, 0.0),
        )

    def __call__(self, retry_state: RetryCallState) -> float:
        if retry_state.outcome is not None:
            exc = retry_state.outcome.exception()
            if exc is not None:
                retry_after = _retry_after_seconds(exc)
                if retry_after is not None:
                    return retry_after
        return float(self._fallback(retry_state))


def extract_json_object(text: str) -> str:
    """Extract the first balanced JSON object from model output."""
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    start = candidate.find("{")
    if start < 0:
        return candidate.strip()
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(candidate)):
        char = candidate[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return candidate[start : index + 1]
    return candidate[start:].strip()


class OpenAICompatClient:
    """One shared async client with tiering, retries, and JSON repair."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model_fast: str,
        model_strong: str,
        timeout_s: float = 60.0,
        max_concurrency: int = 4,
        max_retries: int = 3,
        supports_json_mode: bool = True,
        reasoning_effort: str | None = None,
        retry_base_delay_s: float = 1.0,
        usage_sink: UsageSink | None = None,
        client: Any | None = None,
    ) -> None:
        self._client: Any = client or AsyncOpenAI(
            base_url=base_url,
            api_key=api_key or "not-required",
            timeout=timeout_s,
            max_retries=0,
        )
        self.model_fast = model_fast
        self.model_strong = model_strong
        self.max_concurrency = max_concurrency
        self.max_retries = max(1, max_retries)
        self.supports_json_mode = supports_json_mode
        self.reasoning_effort = reasoning_effort
        self.retry_base_delay_s = retry_base_delay_s
        self.usage_sink = usage_sink
        self._semaphore = asyncio.Semaphore(max_concurrency)

    def _model_for(self, tier: Tier) -> str:
        return self.model_strong if tier == "strong" else self.model_fast

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        tier: Tier = "fast",
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> LLMResult:
        """Generate plain text with bounded concurrency and retries."""
        return await self._complete(
            messages,
            tier=tier,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=False,
        )

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
        """Generate schema-valid JSON, repairing invalid output up to a cap."""
        schema_text = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        instruction = (
            "Return ONLY a JSON object that validates against this JSON Schema. "
            "Do not wrap it in prose. Do not add markdown fences.\n\n"
            f"JSON Schema:\n{schema_text}"
        )
        base_messages = [*messages, {"role": "system", "content": instruction}]
        current_messages = base_messages
        result = await self._complete(
            current_messages,
            tier=tier,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=True,
        )
        raw = result.text

        for repair in range(max_repairs + 1):
            try:
                validated: T = schema.model_validate_json(extract_json_object(raw))
                return validated
            except (ValidationError, ValueError, json.JSONDecodeError) as exc:
                if repair >= max_repairs:
                    raise LLMOutputInvalid(
                        f"The model's response for {schema.__name__} was invalid after "
                        f"{max_repairs} repair attempt(s): {exc}"
                    ) from exc
                logger.warning(
                    "llm_json_repair",
                    schema=schema.__name__,
                    repair=repair + 1,
                    error=str(exc),
                    model=result.model,
                )
                current_messages = [
                    *base_messages,
                    {"role": "assistant", "content": raw},
                    {
                        "role": "user",
                        "content": (
                            "That response was not valid for the required JSON Schema. "
                            f"Validation error:\n{exc}\n\n"
                            "Return the corrected JSON object only."
                        ),
                    },
                ]
                result = await self._complete(
                    current_messages,
                    tier=tier,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    json_mode=True,
                )
                raw = result.text

        raise LLMOutputInvalid(f"Could not parse {schema.__name__}")  # pragma: no cover

    async def _complete(
        self,
        messages: list[dict[str, str]],
        *,
        tier: Tier,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> LLMResult:
        model = self._model_for(tier)
        request: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode and self.supports_json_mode:
            request["response_format"] = {"type": "json_object"}
        if self.reasoning_effort is not None:
            request["reasoning_effort"] = self.reasoning_effort

        async def log_retry(retry_state: RetryCallState) -> None:
            exc = retry_state.outcome.exception() if retry_state.outcome else None
            logger.warning(
                "llm_retry",
                attempt=retry_state.attempt_number,
                model=model,
                error=str(exc) if exc else "unknown",
            )

        retrying = AsyncRetrying(
            retry=retry_if_exception(_is_retryable),
            wait=_RetryAfterExponential(self.retry_base_delay_s),
            stop=stop_after_attempt(self.max_retries),
            before_sleep=log_retry,
            reraise=True,
        )
        try:
            async with self._semaphore:
                async for attempt in retrying:
                    with attempt:
                        response = await self._client.chat.completions.create(**request)
                        result = self._to_result(response, model)
                        await self._record_usage(result)
                        return result
        except LLMOutputInvalid:
            raise
        except Exception as exc:
            raise LLMProviderError(f"LLM provider call failed for model {model}: {exc}") from exc
        raise LLMProviderError("LLM provider call produced no result")  # pragma: no cover

    def _to_result(self, response: Any, requested_model: str) -> LLMResult:
        choices = getattr(response, "choices", None) or []
        if not choices:
            raise ValueError("provider returned no choices")
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        if not isinstance(content, str):
            raise ValueError("provider returned non-text content")
        usage = getattr(response, "usage", None)
        return LLMResult(
            text=content,
            model=str(getattr(response, "model", requested_model) or requested_model),
            prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        )

    async def _record_usage(self, result: LLMResult) -> None:
        if self.usage_sink is None:
            return
        try:
            maybe_awaitable = self.usage_sink(
                result.prompt_tokens, result.completion_tokens, result.model
            )
            if inspect.isawaitable(maybe_awaitable):
                await maybe_awaitable
        except Exception:
            logger.exception("usage_sink_failed")
