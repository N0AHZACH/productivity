"""Human-readable labels for the configured language-model client."""

from typing import Any

from app.llm.base import Tier


def llm_label(client: Any, tier: Tier = "fast") -> str:
    """Return a concise provider/model label for traces, documents, and the API."""
    provider = getattr(client, "provider_name", None)
    model = getattr(client, "model_strong" if tier == "strong" else "model_fast", None)

    if provider is None and hasattr(client, "primary"):
        primary = client.primary
        secondary = getattr(client, "secondary", None)
        primary_label = llm_label(primary, tier)
        if secondary is not None:
            return f"{primary_label}; fallback {llm_label(secondary, tier)}"
        return primary_label

    provider_text = str(provider or client.__class__.__name__)
    model_text = str(model or "schema-routed")
    return f"{provider_text} / {model_text} ({tier})"
