"""Configuration validation tests."""

from app.core.config import Settings


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
