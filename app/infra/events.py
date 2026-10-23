"""In-memory run event publication for live SSE progress."""

import asyncio
from collections import OrderedDict
from collections.abc import AsyncIterator
from typing import Any

Event = dict[str, Any]
_CLOSED = object()


class EventBus:
    """Keep per-run event history and fan events out to live subscribers."""

    def __init__(self, *, max_runs: int = 256, heartbeat_s: float = 15.0) -> None:
        self.max_runs = max_runs
        self.heartbeat_s = heartbeat_s
        self._history: OrderedDict[str, list[Event]] = OrderedDict()
        self._subscribers: dict[str, set[asyncio.Queue[Event | object]]] = {}
        self._closed: set[str] = set()
        self._sequences: dict[str, int] = {}

    def has_run(self, run_id: str) -> bool:
        """Return whether a run has been registered on this bus."""
        return run_id in self._history

    def open_run(self, run_id: str) -> None:
        """Register a run before its first event is published."""
        if run_id in self._history:
            return
        while len(self._history) >= self.max_runs:
            expired, _ = self._history.popitem(last=False)
            self._sequences.pop(expired, None)
            self._subscribers.pop(expired, None)
            self._closed.discard(expired)
        self._history[run_id] = []
        self._sequences[run_id] = 0
        self._subscribers.setdefault(run_id, set())
        self._closed.discard(run_id)

    def publish(self, run_id: str, event: Event) -> None:
        """Append one event and wake every current subscriber."""
        if run_id in self._closed:
            return
        if run_id not in self._history:
            self.open_run(run_id)
        sequence = self._sequences[run_id] + 1
        self._sequences[run_id] = sequence
        payload = {"seq": sequence, "run_id": run_id, **event}
        self._history[run_id].append(payload)
        for queue in tuple(self._subscribers.get(run_id, ())):
            queue.put_nowait(payload)

    def close_run(self, run_id: str) -> None:
        """Mark a run finished and end every live subscription."""
        if run_id in self._closed:
            return
        self._closed.add(run_id)
        for queue in tuple(self._subscribers.get(run_id, ())):
            queue.put_nowait(_CLOSED)

    async def subscribe(self, run_id: str) -> AsyncIterator[Event]:
        """Replay existing events, then stream new ones until the run closes."""
        if run_id not in self._history:
            return
        queue: asyncio.Queue[Event | object] = asyncio.Queue()
        self._subscribers.setdefault(run_id, set()).add(queue)
        history = list(self._history[run_id])
        try:
            for event in history:
                yield event
            if run_id in self._closed:
                return
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=self.heartbeat_s)
                except TimeoutError:
                    yield {"seq": self._sequences[run_id], "heartbeat": True}
                    continue
                if not isinstance(item, dict):
                    return
                yield item
        finally:
            self._subscribers.get(run_id, set()).discard(queue)
