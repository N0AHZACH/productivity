"""Structured logging with request and run correlation IDs."""

import logging
import sys
from typing import Any

import structlog


def _configure_utf8_stream(stream: Any) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if not callable(reconfigure):
        return
    try:
        reconfigure(encoding="utf-8", errors="backslashreplace")
    except (OSError, ValueError):
        return


def configure_logging(level: str = "INFO", *, json_logs: bool = True) -> None:
    """Configure stdlib and structlog once for the application."""
    _configure_utf8_stream(sys.stdout)
    _configure_utf8_stream(sys.stderr)
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
        force=True,
    )
    for noisy_logger in ("httpx", "httpcore", "openai"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if json_logs:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=False))
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "productivity") -> Any:
    """Return a bound structured logger."""
    return structlog.get_logger(name)


def bind_request(request_id: str, run_id: str | None = None) -> None:
    """Bind request correlation fields for the current async context."""
    values = {"request_id": request_id}
    if run_id:
        values["run_id"] = run_id
    structlog.contextvars.bind_contextvars(**values)


def bind_run(run_id: str) -> None:
    """Bind a run ID for the current async context."""
    structlog.contextvars.bind_contextvars(run_id=run_id)


def clear_context() -> None:
    """Clear all context-local logging fields."""
    structlog.contextvars.clear_contextvars()


def current_request_id() -> str:
    """Return the active request ID, if present."""
    return str(structlog.contextvars.get_contextvars().get("request_id", ""))
