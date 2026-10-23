"""Build the configured LLM client chain."""

from app.core.config import Provider, Settings
from app.llm.base import LLMClient
from app.llm.fallback import FallbackLLM
from app.llm.openai_compat import OpenAICompatClient, UsageSink

_PROVIDER_ENDPOINTS: dict[Provider, tuple[str, str]] = {
    "groq": ("groq_base_url", "groq_api_key"),
    "ollama": ("ollama_base_url", "ollama_api_key"),
    "gemini": ("gemini_base_url", "gemini_api_key"),
    "openrouter": ("openrouter_base_url", "openrouter_api_key"),
}


def _build_provider(
    settings: Settings, provider: Provider, usage_sink: UsageSink | None
) -> LLMClient:
    base_attr, key_attr = _PROVIDER_ENDPOINTS[provider]
    return OpenAICompatClient(
        base_url=str(getattr(settings, base_attr)),
        api_key=str(getattr(settings, key_attr)),
        model_fast=settings.model_fast,
        model_strong=settings.model_strong,
        timeout_s=settings.llm_timeout_s,
        max_concurrency=settings.max_concurrency,
        max_retries=settings.llm_max_retries,
        supports_json_mode=settings.llm_supports_json_mode,
        reasoning_effort=settings.llm_reasoning_effort,
        min_interval_s=settings.llm_min_interval_s,
        usage_sink=usage_sink,
        provider_name=provider,
    )


def build_llm(settings: Settings, *, usage_sink: UsageSink | None = None) -> LLMClient:
    """Build the primary client, optionally wrapped with a provider fallback."""
    primary = _build_provider(settings, settings.primary_provider, usage_sink)
    if (
        settings.fallback_provider is None
        or settings.fallback_provider == settings.primary_provider
    ):
        return primary
    secondary = _build_provider(settings, settings.fallback_provider, usage_sink)
    return FallbackLLM(primary, secondary)
