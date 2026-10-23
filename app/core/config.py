"""Application settings loaded from environment variables and `.env`."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Provider = Literal["groq", "ollama", "gemini", "openrouter"]
ModelTier = Literal["fast", "strong"]


class Settings(BaseSettings):
    """Runtime configuration. All values can be overridden by environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    env: str = "local"
    log_level: str = "INFO"
    log_json: bool = True

    primary_provider: Provider = "groq"
    fallback_provider: Provider | None = None
    groq_api_key: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_api_key: str = "ollama"
    gemini_api_key: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    model_fast: str = "openai/gpt-oss-20b"
    model_strong: str = "openai/gpt-oss-120b"
    analysis_tier: ModelTier = "fast"
    planning_tier: ModelTier = "fast"
    writing_tier: ModelTier = "strong"
    reflection_tier: ModelTier = "strong"
    composition_tier: ModelTier = "fast"
    correction_tier: ModelTier = "fast"
    llm_supports_json_mode: bool = True
    llm_timeout_s: float = Field(default=60.0, gt=0)
    llm_max_retries: int = Field(default=3, ge=1, le=10)
    llm_max_output_tokens: int = Field(default=4096, ge=512, le=32000)
    llm_reasoning_effort: Literal["minimal", "low", "medium", "high", "none"] | None = None
    llm_min_interval_s: float = Field(default=0.0, ge=0, le=60)

    max_request_chars: int = Field(default=4000, gt=0)
    max_concurrency: int = Field(default=4, ge=1, le=32)
    max_reflection_loops: int = Field(default=2, ge=0, le=5)
    reflection_llm_mode: Literal["off", "auto", "always"] = "auto"
    reflection_timeout_s: float = Field(default=180.0, gt=0)
    run_timeout_s: float = Field(default=120.0, gt=0)

    data_dir: Path = Path("./data")
    default_author: str = "Productivity"
    api_keys: str = ""
    cache_enabled: bool = True
    cache_ttl_s: float = Field(default=3600.0, gt=0)
    cache_max_entries: int = Field(default=256, ge=1, le=10000)
    hot_cache_enabled: bool = True
    hot_cache_ttl_s: float = Field(default=86400.0, gt=0)
    hot_cache_max_entries: int = Field(default=8, ge=1, le=64)

    @field_validator("fallback_provider", "llm_reasoning_effort", mode="before")
    @classmethod
    def _empty_optional_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def api_key_set(self) -> set[str]:
        """Return configured API keys, ignoring empty entries."""
        return {key.strip() for key in self.api_keys.split(",") if key.strip()}

    @property
    def auth_enabled(self) -> bool:
        """Authentication is enabled whenever at least one API key is configured."""
        return bool(self.api_key_set)

    @property
    def primary_provider_missing_key(self) -> bool:
        """Return whether the primary provider requires an unset API key."""
        if self.primary_provider == "groq":
            return not self.groq_api_key.strip()
        if self.primary_provider == "gemini":
            return not self.gemini_api_key.strip()
        if self.primary_provider == "openrouter":
            return not self.openrouter_api_key.strip()
        return False


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
