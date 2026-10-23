"""In-memory cache behavior and stable cache-key tests."""

import pytest

from app.agent.planner import fallback_plan
from app.core.config import Settings
from app.infra.cache import (
    CachedPlan,
    InMemoryTTLCache,
    plan_cache_key,
    request_cache_key,
)
from app.models.common import DocType
from app.models.state import Analysis
from app.tools.registry import default_registry


def _analysis() -> Analysis:
    return Analysis(
        doc_type=DocType.PROJECT_PLAN,
        objective="Launch a support chatbot.",
        audience="Executive team",
    )


def test_request_key_normalizes_whitespace_and_case() -> None:
    first = request_cache_key("  Write   a plan. ", ["prompt_injection"])
    second = request_cache_key("write a plan.", ["prompt_injection"])
    changed = request_cache_key("write a plan.", [])
    assert first == second
    assert first != changed


def test_plan_key_changes_with_planner_configuration() -> None:
    analysis = _analysis()
    registry = default_registry()
    fast = plan_cache_key(analysis, registry, Settings(planning_tier="fast"))
    strong = plan_cache_key(analysis, registry, Settings(planning_tier="strong"))
    assert fast != strong


def test_cache_returns_independent_copies() -> None:
    cache = InMemoryTTLCache()
    original = _analysis()
    cache.set("analysis", original)
    cached = cache.get("analysis")
    assert isinstance(cached, Analysis)
    cached.objective = "mutated"
    assert original.objective == "Launch a support chatbot."
    assert cache.get("analysis").objective == "Launch a support chatbot."


def test_cache_expires_after_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [100.0]
    monkeypatch.setattr("app.infra.cache.time.monotonic", lambda: now[0])
    cache = InMemoryTTLCache(ttl_s=10)
    cache.set("analysis", _analysis())
    now[0] = 111.0
    assert cache.get("analysis") is None


def test_cache_evicts_least_recently_used_entry() -> None:
    cache = InMemoryTTLCache(max_entries=2)
    cache.set("a", 1)
    cache.set("b", 2)
    assert cache.get("a") == 1
    cache.set("c", 3)
    assert cache.get("b") is None
    assert cache.get("a") == 1
    assert cache.get("c") == 3


def test_cached_plan_keeps_source() -> None:
    entry = CachedPlan(fallback_plan(_analysis()), "fallback")
    assert entry.source == "fallback"
    assert entry.plan.tasks
