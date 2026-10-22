"""FastAPI application factory."""

import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from app.api.routes import router
from app.core.config import Settings, get_settings
from app.core.errors import install_exception_handlers
from app.core.logging import bind_request, clear_context, configure_logging, get_logger

logger = get_logger(__name__)
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_logging(settings.log_level, json_logs=settings.log_json)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    logger.info("application_started", env=settings.env)
    yield
    logger.info("application_stopped", env=settings.env)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the ASGI application."""
    resolved_settings = settings or get_settings()
    app = FastAPI(
        title="DocPilot",
        version="0.1.0",
        description="Autonomous document agent with validated plans and deterministic Word output.",
        lifespan=_lifespan,
    )
    app.state.settings = resolved_settings

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next: object) -> Response:
        incoming = request.headers.get("X-Request-ID", "")
        request_id = incoming if _REQUEST_ID_PATTERN.fullmatch(incoming) else uuid.uuid4().hex
        bind_request(request_id)
        request.state.request_id = request_id
        try:
            response: Response = await call_next(request)  # type: ignore[operator]
        finally:
            clear_context()
        response.headers["X-Request-ID"] = request_id
        return response

    install_exception_handlers(app)
    app.include_router(router)
    return app


app = create_app()
