"""Operational and agent HTTP routes."""

from fastapi import APIRouter

from app.core.config import get_settings

router = APIRouter()


@router.get("/health", tags=["operations"])
async def health() -> dict[str, str]:
    """Liveness probe that does not depend on configuration or providers."""
    return {"status": "ok"}


@router.get("/ready", tags=["operations"])
async def ready() -> dict[str, str]:
    """Readiness probe confirming settings are loadable."""
    settings = get_settings()
    return {"status": "ready", "env": settings.env}
