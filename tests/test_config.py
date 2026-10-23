"""Configuration validation tests."""

from app.core.config import Settings


def test_empty_optional_settings_mean_none() -> None:
    assert Settings(fallback_provider="").fallback_provider is None
    assert Settings(fallback_provider="  ").fallback_provider is None
    assert Settings(fallback_provider="groq").fallback_provider == "groq"
    assert Settings(llm_reasoning_effort="").llm_reasoning_effort is None
    assert Settings(llm_reasoning_effort="low").llm_reasoning_effort == "low"
    assert Settings(llm_reasoning_effort="none").llm_reasoning_effort == "none"


def test_default_author_is_productivity() -> None:
    assert Settings().default_author == "Productivity"


def test_model_tiers_default_to_cheap_analysis_and_strong_writing() -> None:
    settings = Settings()
    assert settings.analysis_tier == "fast"
    assert settings.planning_tier == "fast"
    assert settings.writing_tier == "strong"
    assert settings.reflection_tier == "strong"
    assert settings.composition_tier == "fast"
    assert settings.correction_tier == "fast"


def test_primary_provider_missing_key() -> None:
    assert Settings(primary_provider="groq", groq_api_key="").primary_provider_missing_key
    assert not Settings(
        primary_provider="groq", groq_api_key="configured"
    ).primary_provider_missing_key

    assert Settings(primary_provider="gemini", gemini_api_key="").primary_provider_missing_key
    assert not Settings(
        primary_provider="gemini", gemini_api_key="configured"
    ).primary_provider_missing_key

    assert Settings(
        primary_provider="openrouter", openrouter_api_key=""
    ).primary_provider_missing_key
    assert not Settings(
        primary_provider="openrouter", openrouter_api_key="configured"
    ).primary_provider_missing_key

    assert not Settings(primary_provider="ollama").primary_provider_missing_key
