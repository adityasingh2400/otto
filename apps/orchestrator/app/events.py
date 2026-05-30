"""Tiny async pub/sub for streaming pipeline events to the dashboard over SSE.

Every stage of the pipeline publishes typed events (see packages/spec/types.ts
`LineForgeEvent`) onto a per-session channel; the dashboard subscribes via
GET /events/{session_id}.
"""

import asyncio
import json
from collections import defaultdict
from typing import Any, AsyncIterator


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[asyncio.Queue]] = defaultdict(list)
        self._history: dict[str, list[dict]] = defaultdict(list)

    def subscribe(self, session_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs[session_id].append(q)
        return q

    def unsubscribe(self, session_id: str, q: asyncio.Queue) -> None:
        if q in self._subs.get(session_id, []):
            self._subs[session_id].remove(q)

    async def publish(self, session_id: str, event: dict[str, Any]) -> None:
        self._history[session_id].append(event)
        for q in list(self._subs.get(session_id, [])):
            await q.put(event)

    def history(self, session_id: str) -> list[dict]:
        return list(self._history.get(session_id, []))


bus = EventBus()


async def sse_stream(session_id: str) -> AsyncIterator[bytes]:
    """SSE generator. Replays history (so late subscribers catch up) then tails live."""
    q = bus.subscribe(session_id)
    try:
        yield b": connected\n\n"
        for past in bus.history(session_id):
            yield f"data: {json.dumps(past)}\n\n".encode()
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=15.0)
                yield f"data: {json.dumps(event)}\n\n".encode()
            except asyncio.TimeoutError:
                yield b": keepalive\n\n"
    finally:
        bus.unsubscribe(session_id, q)
