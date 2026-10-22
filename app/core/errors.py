"""Typed application errors and the API's single error envelope."""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import current_request_id, get_logger

logger = get_logger(__name__)


class DocPilotError(Exception):
    """Base class for expected failures with a stable API code and status."""

    code = "DOCPILOT_ERROR"
    http_status = 500

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        http_status: int | None = None,
    ) -> None:
        self.message = message or self.code.replace("_", " ").title()
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status
        super().__init__(self.message)

    def to_dict(self) -> dict[str, str]:
        """Return the serializable error payload."""
        return {
            "code": self.code,
            "message": self.message,
            "request_id": current_request_id(),
        }


class GuardrailRejected(DocPilotError):
    code = "GUARDRAIL_REJECTED"
    http_status = 400


class LLMProviderError(DocPilotError):
    code = "LLM_PROVIDER_ERROR"
    http_status = 502


class LLMOutputInvalid(DocPilotError):
    code = "LLM_OUTPUT_INVALID"
    http_status = 502


class PlanInvalid(DocPilotError):
    code = "PLAN_INVALID"
    http_status = 500


class ToolError(DocPilotError):
    code = "TOOL_ERROR"
    http_status = 500


class RenderError(DocPilotError):
    code = "RENDER_ERROR"
    http_status = 500


class NotFound(DocPilotError):
    code = "NOT_FOUND"
    http_status = 404


class Unauthorized(DocPilotError):
    code = "UNAUTHORIZED"
    http_status = 401


class RateLimited(DocPilotError):
    code = "RATE_LIMITED"
    http_status = 429


def _error_response(status: int, code: str, message: str, request_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "request_id": request_id}},
    )


def _validation_message(errors: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for error in errors:
        location = ".".join(str(value) for value in error.get("loc", ()) if value != "body")
        message = str(error.get("msg", "invalid value"))
        parts.append(f"{location}: {message}" if location else message)
    return "; ".join(parts) or "Request validation failed"


def install_exception_handlers(app: FastAPI) -> None:
    """Register handlers that always return the standard error envelope."""

    @app.exception_handler(DocPilotError)
    async def _handle_docpilot(_: Request, exc: DocPilotError) -> JSONResponse:
        request_id = current_request_id()
        logger.warning(
            "request_failed",
            error_code=exc.code,
            http_status=exc.http_status,
            error_message=exc.message,
        )
        return _error_response(exc.http_status, exc.code, exc.message, request_id)

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = exc.errors()
        too_long = any(error.get("type") == "string_too_long" for error in errors)
        status = 400 if too_long else 422
        code = "GUARDRAIL_REJECTED" if too_long else "VALIDATION_ERROR"
        message = (
            "Request is too long"
            if too_long
            else _validation_message([dict(error) for error in errors])
        )
        return _error_response(status, code, message, current_request_id())

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        message = str(exc.detail) if exc.detail else "Request failed"
        code = "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
        return _error_response(exc.status_code, code, message, current_request_id())

    @app.exception_handler(Exception)
    async def _handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_exception", error_type=type(exc).__name__)
        return _error_response(
            500,
            "INTERNAL_ERROR",
            "An unexpected internal error occurred",
            current_request_id(),
        )
