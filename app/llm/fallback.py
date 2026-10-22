"""Primary-to-secondary provider fallback with a small circuit breaker."""

import time
from typing import Any, TypeVar

import structlog
from pydantic import BaseModel

from app.core.errors import LLMProviderError
from app.llm.base import LLMClient, LLMResult, Tier

logger = structlog.get_logger(__name__)
T = TypeVar("T", bound=BaseModel)


class FallbackLLM:
    """Try a primary provider, then a secondary one when the primary is unhealthy."""

    def __init__(
        self,
        primary: LLMClient,
        secondary: LLMClient | None,
        *,
        failure_threshold: int = 3,
        cooldown_s: float = 30.0,
    ) -> None:
        self.primary = primary
        self.secondary = secondary
        self.failure_threshold = failure_threshold
        self.cooldown_s = cooldown_s
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0

    def _primary_available(self) -> bool:
        if self.secondary is None:
            return True
        return time.monotonic() >= self._circuit_open_until

    def _record_primary_failure(self, exc: LLMProviderError) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.failure_threshold:
            self._circuit_open_until = time.monotonic() + self.cooldown_s
            logger.error(
                "llm_primary_circuit_open",
                failures=self._consecutive_failures,
                cooldown_s=self.cooldown_s,
                error=str(exc),
            )
        else:
            logger.warning(
                "llm_primary_failed",
                failures=self._consecutive_failures,
                error=str(exc),
            )

    def _record_primary_success(self) -> None:
        if self._consecutive_failures:
            logger.info("llm_primary_recovered")
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        tier: Tier = "fast",
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> LLMResult:
        """Run plain completion with fallback."""
        if self._primary_available():
            try:
                result = await self.primary.complete(
                    messages, tier=tier, temperature=temperature, max_tokens=max_tokens
                )
                self._record_primary_success()
                return result
            except LLMProviderError as exc:
                self._record_primary_failure(exc)
                if self.secondary is None:
                    raise
        else:
            logger.warning("llm_primary_skipped_circuit_open")

        if self.secondary is None:  # pragma: no cover - guarded above
            raise LLMProviderError("Primary LLM failed and no fallback is configured")
        logger.warning("llm_fallback_used")
        return await self.secondary.complete(
            messages, tier=tier, temperature=temperature, max_tokens=max_tokens
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
        """Run schema-constrained completion with fallback."""
        if self._primary_available():
            try:
                result = await self.primary.complete_json(
                    messages,
                    schema,
                    tier=tier,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    max_repairs=max_repairs,
                )
                self._record_primary_success()
                return result
            except LLMProviderError as exc:
                self._record_primary_failure(exc)
                if self.secondary is None:
                    raise
        else:
            logger.warning("llm_primary_skipped_circuit_open")

        if self.secondary is None:  # pragma: no cover - guarded above
            raise LLMProviderError("Primary LLM failed and no fallback is configured")
        logger.warning("llm_fallback_used", schema=schema.__name__)
        return await self.secondary.complete_json(
            messages,
            schema,
            tier=tier,
            temperature=temperature,
            max_tokens=max_tokens,
            max_repairs=max_repairs,
        )

    @property
    def state(self) -> dict[str, Any]:
        """Expose breaker state for diagnostics and tests."""
        return {
            "consecutive_failures": self._consecutive_failures,
            "circuit_open_until": self._circuit_open_until,
            "primary_available": self._primary_available(),
        }
