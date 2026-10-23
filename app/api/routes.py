"""Operational, agent, and document HTTP routes."""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import structlog
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse, StreamingResponse

from app.agent.orchestrator import Orchestrator
from app.api.auth import require_api_key
from app.api.schemas import AgentRequest, AgentRunResponse, RunStartedResponse
from app.container import Container
from app.core.errors import NotFound
from app.models.common import RunStatus

router = APIRouter()
logger = structlog.get_logger(__name__)
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
INDEX_HTML = Path(__file__).resolve().parents[1] / "static" / "index.html"


@router.get("/", include_in_schema=False)
@router.get("/demo", include_in_schema=False)
async def demo_ui() -> FileResponse:
    """Serve the interactive local demo."""
    return FileResponse(INDEX_HTML, media_type="text/html")


@router.get("/health", tags=["operations"])
async def health() -> dict[str, str]:
    """Liveness probe that does not depend on configuration or providers."""
    return {"status": "ok"}


@router.get("/ready", tags=["operations"])
async def ready(request: Request) -> dict[str, str]:
    """Readiness probe confirming settings and storage are loadable."""
    container = request.app.state.container
    await asyncio.to_thread(container.filestore.ensure)
    return {"status": "ready", "env": container.settings.env}


@router.post(
    "/agent",
    response_model=AgentRunResponse,
    tags=["agent"],
    dependencies=[Depends(require_api_key)],
)
async def run_agent_endpoint(payload: AgentRequest, request: Request) -> AgentRunResponse:
    """Run the full document agent and return its metadata and download link."""
    state = await Orchestrator(request.app.state.container).run(payload.request)
    return AgentRunResponse.from_state(state)


async def _run_in_background(
    container: Container,
    request_text: str,
    run_id: str,
) -> None:
    try:
        await Orchestrator(container).run(request_text, run_id=run_id)
    except Exception:
        logger.exception("background_run_failed", run_id=run_id)


@router.post(
    "/runs",
    response_model=RunStartedResponse,
    status_code=202,
    tags=["agent"],
    dependencies=[Depends(require_api_key)],
)
async def start_run(payload: AgentRequest, request: Request) -> RunStartedResponse:
    """Accept a run for background execution and return its SSE stream URL."""
    container = request.app.state.container
    run_id = uuid.uuid4().hex
    container.event_bus.open_run(run_id)
    task = asyncio.create_task(
        _run_in_background(container, payload.request, run_id)
    )
    tasks: set[asyncio.Task[None]] = request.app.state.background_tasks
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return RunStartedResponse(
        run_id=run_id,
        status=RunStatus.QUEUED,
        events_url=f"/runs/{run_id}/events",
    )


@router.get(
    "/runs/{run_id}/events",
    tags=["agent"],
    dependencies=[Depends(require_api_key)],
)
async def stream_run_events(run_id: str, request: Request) -> StreamingResponse:
    """Stream replayable server-sent events for one run."""
    event_bus = request.app.state.container.event_bus
    if not event_bus.has_run(run_id):
        raise NotFound("Run not found.")

    async def event_stream() -> AsyncIterator[str]:
        async for event in event_bus.subscribe(run_id):
            if event.get("heartbeat"):
                yield ": ping\n\n"
                continue
            payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
            yield f"id: {event.get('seq', 0)}\ndata: {payload}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/documents/{run_id}",
    tags=["documents"],
    dependencies=[Depends(require_api_key)],
)
async def download_document(run_id: str, request: Request) -> Response:
    """Download a generated Word document by run id."""
    filestore = request.app.state.container.filestore
    try:
        content = await filestore.read(run_id)
    except (ValueError, FileNotFoundError) as exc:
        raise NotFound("Document not found.") from exc
    return Response(
        content=content,
        media_type=DOCX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{run_id}.docx"',
            "X-Content-Type-Options": "nosniff",
        },
    )
