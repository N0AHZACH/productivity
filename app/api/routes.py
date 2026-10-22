"""Operational, agent, and document HTTP routes."""

import asyncio

from fastapi import APIRouter, Depends, Request, Response

from app.agent.orchestrator import Orchestrator
from app.api.auth import require_api_key
from app.api.schemas import AgentRequest, AgentRunResponse
from app.core.errors import NotFound

router = APIRouter()
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


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
