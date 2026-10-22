"""Configuration validation tests."""

from app.core.config import Settings


def test_empty_optional_settings_mean_none() -> None:
    assert Settings(fallback_provider="").fallback_provider is None
    assert Settings(fallback_provider="  ").fallback_provider is None
    assert Settings(fallback_provider="groq").fallback_provider == "groq"
    assert Settings(llm_reasoning_effort="").llm_reasoning_effort is None
    assert Settings(llm_reasoning_effort="low").llm_reasoning_effort == "low"


def test_primary_provider_missing_key() -> None:
    assert Settings(primary_provider="groq", groq_api_key="").primary_provider_missing_key
    assert not Settings(
        primary_provider="groq", groq_api_key="configured"
    ).primary_provider_missing_key

    assert Settings(primary_provider="gemini", gemini_api_key="").primary_provider_missing_key
    assert not Settings(
        primary_provider="gemini", gemini_api_key="configured"
    ).primary_provider_missing_key

    assert not Settings(primary_provider="ollama").primary_provider_missing_key
