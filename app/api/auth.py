"""API-key authentication for agent and document endpoints."""

from secrets import compare_digest
from typing import Annotated

from fastapi import Header, Request

from app.core.errors import Unauthorized


async def require_api_key(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    """Require one configured API key when authentication is enabled."""
    settings = request.app.state.container.settings
    if not settings.auth_enabled:
        return
    supplied = x_api_key or ""
    if not any(compare_digest(supplied, expected) for expected in settings.api_key_set):
        raise Unauthorized("Missing or invalid X-API-Key.")
