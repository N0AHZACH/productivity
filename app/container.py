"""Composition root for one application instance."""

from dataclasses import dataclass

from app.core.config import Settings
from app.infra.filestore import FileStore
from app.llm.base import LLMClient
from app.llm.factory import build_llm
from app.render.docx_renderer import DocxRenderer
from app.render.themes import Theme
from app.tools.registry import ToolRegistry, default_registry


@dataclass(slots=True)
class Container:
    """Concrete dependencies shared by the API and the orchestrator."""

    settings: Settings
    llm: LLMClient
    registry: ToolRegistry
    renderer: DocxRenderer
    filestore: FileStore

    @classmethod
    def build(
        cls,
        settings: Settings,
        *,
        llm: LLMClient | None = None,
        registry: ToolRegistry | None = None,
        renderer: DocxRenderer | None = None,
        filestore: FileStore | None = None,
        theme: Theme | None = None,
    ) -> "Container":
        """Build production dependencies, allowing test overrides."""
        return cls(
            settings=settings,
            llm=llm or build_llm(settings),
            registry=registry or default_registry(),
            renderer=renderer or DocxRenderer(theme or Theme()),
            filestore=filestore or FileStore(settings.data_dir / "documents"),
        )
