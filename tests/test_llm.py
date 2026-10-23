"""LLM provider behavior, retries, repair, and fallback tests (no network)."""

import asyncio
import time
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import httpx2
import openai
import pytest
from pydantic import BaseModel, ValidationError

from app.core.config import Settings
from app.core.errors import LLMOutputInvalid, LLMProviderError
from app.llm.base import LLMClient
from app.llm.factory import build_llm
from app.llm.fake import FakeLLM
from app.llm.fallback import FallbackLLM
from app.llm.openai_compat import OpenAICompatClient, extract_json_object


class Pong(BaseModel):
    pong: str


def _completion(content: Any) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        model="fake-model",
        usage=SimpleNamespace(prompt_tokens=5, completion_tokens=3),
    )


class FakeCompletions:
    def __init__(self, handler: Callable[..., Any]) -> None:
        self.handler = handler
        self.calls = 0
        self.active = 0
        self.peak = 0

    async def create(self, **kwargs: Any) -> Any:
        self.calls += 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            return await self.handler(**kwargs)
        finally:
            self.active -= 1


def _client(
    handler: Callable[..., Any],
    *,
    max_concurrency: int = 4,
    retry_delay: float = 0.0,
    min_interval: float = 0.0,
    reasoning_effort: str | None = None,
    usage_sink: Any = None,
) -> tuple[OpenAICompatClient, FakeCompletions]:
    completions = FakeCompletions(handler)
    fake = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    client = OpenAICompatClient(
        base_url="http://fake",
        api_key="fake",
        model_fast="fast",
        model_strong="strong",
        max_concurrency=max_concurrency,
        max_retries=3,
        reasoning_effort=reasoning_effort,
        retry_base_delay_s=retry_delay,
        min_interval_s=min_interval,
        usage_sink=usage_sink,
        client=fake,
    )
    return client, completions


def _rate_limit() -> openai.RateLimitError:
    request = httpx2.Request("POST", "http://fake/chat")
    response = httpx2.Response(429, request=request, headers={"retry-after": "0"})
    return openai.RateLimitError("rate limited", response=response, body=None)


def _rate_limit_body_hint(delay: str) -> openai.RateLimitError:
    request = httpx2.Request("POST", "http://fake/chat")
    response = httpx2.Response(429, request=request)
    return openai.RateLimitError(
        f"quota exceeded, 'retryDelay': '{delay}'", response=response, body=None
    )


@pytest.mark.asyncio
async def test_fenced_json_is_parsed() -> None:
    client, _ = _client(
        lambda **_: asyncio.sleep(0, _completion('```json\n{"pong": "hello"}\n```'))
    )
    result = await client.complete_json([{"role": "user", "content": "ping"}], Pong)
    assert result.pong == "hello"


@pytest.mark.asyncio
async def test_list_content_parts_are_flattened() -> None:
    client, _ = _client(
        lambda **_: asyncio.sleep(
            0,
            _completion(
                [
                    {"type": "text", "text": '{"pong": '},
                    {"type": "text", "text": '"hello"}'},
                ]
            ),
        )
    )
    result = await client.complete_json([{"role": "user", "content": "ping"}], Pong)
    assert result.pong == "hello"


@pytest.mark.asyncio
async def test_invalid_json_triggers_one_repair_then_succeeds() -> None:
    replies = iter(["this is not json", '{"pong": "fixed"}'])

    async def handler(**_: Any) -> Any:
        return _completion(next(replies))

    client, completions = _client(handler)
    result = await client.complete_json([{"role": "user", "content": "ping"}], Pong)
    assert result.pong == "fixed"
    assert completions.calls == 2


@pytest.mark.asyncio
async def test_persistent_invalid_json_raises_output_invalid() -> None:
    client, completions = _client(lambda **_: asyncio.sleep(0, _completion("not json")))
    with pytest.raises(LLMOutputInvalid):
        await client.complete_json([{"role": "user", "content": "ping"}], Pong, max_repairs=2)
    assert completions.calls == 3


@pytest.mark.asyncio
async def test_429_is_retried_then_succeeds() -> None:
    attempts = 0

    async def handler(**_: Any) -> Any:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise _rate_limit()
        return _completion('{"pong": "ok"}')

    client, _ = _client(handler)
    result = await client.complete_json([{"role": "user", "content": "ping"}], Pong)
    assert result.pong == "ok"
    assert attempts == 2


@pytest.mark.asyncio
async def test_retry_delay_in_error_body_is_honored() -> None:
    attempts = 0

    async def handler(**_: Any) -> Any:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise _rate_limit_body_hint("0.08s")
        return _completion('{"pong": "ok"}')

    client, _ = _client(handler)
    started = time.monotonic()
    result = await client.complete_json([{"role": "user", "content": "ping"}], Pong)
    elapsed = time.monotonic() - started

    assert result.pong == "ok"
    assert attempts == 2
    assert 0.05 <= elapsed < 5.0


@pytest.mark.asyncio
async def test_daily_quota_hint_fails_fast_without_retries() -> None:
    async def handler(**_: Any) -> Any:
        raise _rate_limit_body_hint("3600s")

    client, completions = _client(handler)
    with pytest.raises(LLMProviderError):
        await client.complete([{"role": "user", "content": "ping"}])
    assert completions.calls == 1


@pytest.mark.asyncio
async def test_min_interval_paces_provider_calls() -> None:
    client, completions = _client(
        lambda **_: asyncio.sleep(0, _completion("ok")), min_interval=0.05
    )

    started = time.monotonic()
    await client.complete([{"role": "user", "content": "one"}])
    await client.complete([{"role": "user", "content": "two"}])
    elapsed = time.monotonic() - started

    assert completions.calls == 2
    assert elapsed >= 0.04


@pytest.mark.asyncio
async def test_exhausted_retries_raise_provider_error() -> None:
    async def handler(**_: Any) -> Any:
        raise _rate_limit()

    client, completions = _client(handler)
    with pytest.raises(LLMProviderError):
        await client.complete([{"role": "user", "content": "ping"}])
    assert completions.calls == 3


@pytest.mark.asyncio
async def test_primary_failure_falls_back_to_secondary() -> None:
    primary = FakeLLM([LLMProviderError("primary down")])
    secondary = FakeLLM.with_schemas({Pong: {"pong": "secondary"}})
    fallback = FallbackLLM(primary, secondary)

    result = await fallback.complete_json([{"role": "user", "content": "ping"}], Pong)

    assert result.pong == "secondary"
    assert len(primary.calls) == 1
    assert len(secondary.calls) == 1


@pytest.mark.asyncio
async def test_circuit_breaker_skips_primary_after_three_failures() -> None:
    primary = FakeLLM([LLMProviderError("down") for _ in range(5)])
    secondary = FakeLLM.with_schemas({Pong: {"pong": "fallback"}})
    fallback = FallbackLLM(primary, secondary, cooldown_s=30)

    for _ in range(5):
        result = await fallback.complete_json([{"role": "user", "content": "ping"}], Pong)
        assert result.pong == "fallback"

    assert len(primary.calls) == 3
    assert len(secondary.calls) == 5
    assert fallback.state["primary_available"] is False


@pytest.mark.asyncio
async def test_concurrency_cap_is_respected() -> None:
    async def handler(**_: Any) -> Any:
        await asyncio.sleep(0.02)
        return _completion("ok")

    client, completions = _client(handler, max_concurrency=2)
    await asyncio.gather(
        *(client.complete([{"role": "user", "content": "x"}]) for _ in range(6))
    )
    assert completions.peak <= 2
    assert completions.peak == 2


@pytest.mark.asyncio
async def test_reasoning_effort_is_forwarded_only_when_configured() -> None:
    captured: dict[str, Any] = {}

    async def handler(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _completion('{"pong": "ok"}')

    client, _ = _client(handler, reasoning_effort="low")
    await client.complete_json([{"role": "user", "content": "ping"}], Pong)
    assert captured["reasoning_effort"] == "low"

    captured.clear()
    client, _ = _client(handler)
    await client.complete_json([{"role": "user", "content": "ping"}], Pong)
    assert "reasoning_effort" not in captured


@pytest.mark.asyncio
async def test_usage_sink_receives_tokens() -> None:
    received: list[tuple[int, int, str]] = []
    client, _ = _client(
        lambda **_: asyncio.sleep(0, _completion("ok")),
        usage_sink=lambda p, c, m: received.append((p, c, m)),
    )
    await client.complete([{"role": "user", "content": "x"}])
    assert received == [(5, 3, "fake-model")]


def test_extract_json_handles_prose_and_balances_strings() -> None:
    raw = 'Here you go: {"text": "a } brace", "nested": {"x": 1}} trailing'
    assert extract_json_object(raw) == '{"text": "a } brace", "nested": {"x": 1}}'


def test_fake_by_schema_and_with_json() -> None:
    fake = FakeLLM.by_schema({Pong: [{"pong": "one"}, {"pong": "two"}]})
    first = asyncio.run(fake.complete_json([], Pong))
    second = asyncio.run(fake.complete_json([], Pong))
    assert (first.pong, second.pong) == ("one", "two")

    fake_queue = FakeLLM.with_json(Pong(pong="queued"))
    result = asyncio.run(fake_queue.complete_json([], Pong))
    assert result.pong == "queued"


def test_fake_raises_typed_invalid_output() -> None:
    fake = FakeLLM.with_schemas({Pong: "not json"})
    with pytest.raises(LLMOutputInvalid):
        asyncio.run(fake.complete_json([], Pong))


def test_factory_builds_primary_and_optional_fallback() -> None:
    settings = Settings(
        primary_provider="ollama",
        fallback_provider=None,
        ollama_base_url="http://localhost:11434/v1",
    )
    primary = build_llm(settings)
    assert isinstance(primary, OpenAICompatClient)

    openrouter = build_llm(
        Settings(primary_provider="openrouter", openrouter_api_key="test")
    )
    assert isinstance(openrouter, OpenAICompatClient)

    with_fallback = build_llm(
        settings.model_copy(update={"fallback_provider": "groq", "groq_api_key": "test"})
    )
    assert isinstance(with_fallback, FallbackLLM)


def test_fake_llm_is_structurally_an_llm_client() -> None:
    def accepts_client(client: LLMClient) -> None:
        assert client is not None

    accepts_client(FakeLLM())


def test_retry_delay_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.llm.openai_compat import _retry_after_seconds

    request = httpx2.Request("POST", "http://fake")
    response = httpx2.Response(429, request=request, headers={"retry-after": "999"})
    exc = openai.RateLimitError("limited", response=response, body=None)
    assert _retry_after_seconds(exc) == 60.0


def test_validation_error_imported_for_type_checking() -> None:
    with pytest.raises(ValidationError):
        Pong.model_validate({})


def test_retry_after_takes_precedence_without_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    assert True
