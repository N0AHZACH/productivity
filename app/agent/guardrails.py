"""Cheap deterministic request validation and injection flagging."""

import re
import unicodedata

from pydantic import BaseModel, Field

from app.core.config import Settings
from app.core.errors import GuardrailRejected

_INJECTION_PATTERNS = (
    r"\bignore\s+(?:all\s+)?(?:the\s+)?previous\s+instructions\b",
    r"\bdisregard\s+(?:all\s+)?(?:prior|previous|earlier)\s+instructions\b",
    r"\breveal\s+(?:your\s+)?system\s+prompt\b",
    r"\bshow\s+(?:me\s+)?(?:your\s+)?(?:hidden\s+)?system\s+prompt\b",
    r"\byou\s+are\s+now\s+(?:a|an|the)\b",
    r"\b(?:jailbreak|developer\s+mode)\b",
)


class GuardrailResult(BaseModel):
    """Sanitized request text plus non-blocking risk flags."""

    sanitized: str
    flags: list[str] = Field(default_factory=list)


def _strip_control_characters(text: str) -> str:
    return "".join(char for char in text if char in "\n\t" or unicodedata.category(char)[0] != "C")


def check_request(text: str, settings: Settings) -> GuardrailResult:
    """Validate and normalize a request, flagging likely prompt injection."""
    if not text or not text.strip():
        raise GuardrailRejected("Request must not be empty.")
    if len(text) > settings.max_request_chars:
        raise GuardrailRejected(
            f"Request is too long ({len(text)} characters); "
            f"the maximum is {settings.max_request_chars}."
        )

    cleaned = _strip_control_characters(text).replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if not cleaned:
        raise GuardrailRejected("Request must contain readable text.")

    flags: list[str] = []
    if any(re.search(pattern, cleaned, flags=re.IGNORECASE) for pattern in _INJECTION_PATTERNS):
        flags.append("prompt_injection")
    return GuardrailResult(sanitized=cleaned, flags=flags)
