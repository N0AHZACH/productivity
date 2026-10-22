"""Helpers for embedding Pydantic contracts in LLM prompts."""

from typing import Any

from pydantic import BaseModel


def schema_for(model: type[BaseModel]) -> dict[str, Any]:
    """Return a JSON Schema for a model, suitable for prompt embedding."""
    schema = model.model_json_schema()
    schema.pop("title", None)
    return schema
