"""Composition root for one application instance."""

from dataclasses import dataclass, field

from app.core.config import Settings
from app.infra.cache import InMemoryTTLCache
from app.infra.events import EventBus
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
    cache: InMemoryTTLCache | None = None
    event_bus: EventBus = field(default_factory=EventBus)

    def __post_init__(self) -> None:
        if self.cache is None and self.settings.cache_enabled:
            self.cache = InMemoryTTLCache(
                max_entries=self.settings.cache_max_entries,
                ttl_s=self.settings.cache_ttl_s,
            )

    @classmethod
    def build(
        cls,
        settings: Settings,
        *,
        llm: LLMClient | None = None,
        registry: ToolRegistry | None = None,
        renderer: DocxRenderer | None = None,
        filestore: FileStore | None = None,
        cache: InMemoryTTLCache | None = None,
        event_bus: EventBus | None = None,
        theme: Theme | None = None,
    ) -> "Container":
        """Build production dependencies, allowing test overrides."""
        return cls(
            settings=settings,
            llm=llm or build_llm(settings),
            registry=registry or default_registry(),
            renderer=renderer or DocxRenderer(theme or Theme()),
            filestore=filestore or FileStore(settings.data_dir / "documents"),
            cache=cache,
            event_bus=event_bus or EventBus(),
        )
