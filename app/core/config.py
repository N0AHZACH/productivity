"""Application settings loaded from environment variables and `.env`."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Provider = Literal["groq", "ollama", "gemini"]


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
    model_fast: str = "llama-3.1-8b-instant"
    model_strong: str = "llama-3.3-70b-versatile"
    llm_supports_json_mode: bool = True
    llm_timeout_s: float = Field(default=60.0, gt=0)
    llm_max_retries: int = Field(default=3, ge=1, le=10)

    max_request_chars: int = Field(default=4000, gt=0)
    max_concurrency: int = Field(default=4, ge=1, le=32)
    max_reflection_loops: int = Field(default=2, ge=0, le=5)
    reflection_llm_mode: Literal["off", "auto", "always"] = "auto"
    run_timeout_s: float = Field(default=120.0, gt=0)

    data_dir: Path = Path("./data")
    default_author: str = "DocPilot"
    api_keys: str = ""
    cache_enabled: bool = True

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
        return False


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
