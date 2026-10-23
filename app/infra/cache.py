"""Small in-memory cache for normalized analysis and plan results."""

import copy
import hashlib
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from app.core.config import Settings
from app.models.plan import TaskList
from app.models.state import AgentState, Analysis
from app.tools.registry import ToolRegistry

CACHE_VERSION = "m10-1"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def request_cache_key(request: str, flags: list[str]) -> str:
    """Return a stable key for a normalized request and its guardrail flags."""
    normalized = " ".join(request.split()).casefold()
    flag_part = ",".join(sorted(flags))
    return f"analysis:{_digest(f'{CACHE_VERSION}|{normalized}|{flag_part}')}"


def plan_cache_key(
    analysis: Analysis,
    registry: ToolRegistry,
    settings: Settings,
) -> str:
    """Return a stable key for an analysis and the planner configuration."""
    parts = [
        CACHE_VERSION,
        analysis.model_dump_json(),
        ",".join(sorted(registry.names())),
        settings.model_fast,
        settings.model_strong,
        settings.planning_tier,
        str(settings.llm_max_output_tokens),
    ]
    return f"plan:{_digest('|'.join(parts))}"


def hot_run_cache_key(request: str) -> str:
    """Return a stable key for a completed demo run."""
    normalized = " ".join(request.split()).casefold()
    return f"hot:{_digest(f'{CACHE_VERSION}|{normalized}')}"


@dataclass(frozen=True)
class CachedPlan:
    """A validated plan plus the source that produced it."""

    plan: TaskList
    source: str


@dataclass(frozen=True)
class HotRunSnapshot:
    """A completed state and rendered file that can be replayed instantly."""

    state: AgentState
    document: bytes


class InMemoryTTLCache:
    """Bounded LRU cache with TTLs and defensive copies."""

    def __init__(self, *, max_entries: int = 256, ttl_s: float = 3600.0) -> None:
        self.max_entries = max(1, max_entries)
        self.ttl_s = max(0.0, ttl_s)
        self._entries: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def get(self, key: str) -> Any | None:
        """Return a copy of a live value, or None when absent or expired."""
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at <= time.monotonic():
            self._entries.pop(key, None)
            return None
        self._entries.move_to_end(key)
        return copy.deepcopy(value)

    def set(self, key: str, value: Any) -> None:
        """Store a copy of one value and evict the least-recently used entry."""
        self._entries[key] = (time.monotonic() + self.ttl_s, copy.deepcopy(value))
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        """Drop every cached entry."""
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)
