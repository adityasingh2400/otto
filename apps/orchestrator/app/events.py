"""Tiny async pub/sub for streaming pipeline events to the dashboard over SSE.

Every stage of the pipeline publishes typed events (see packages/spec/types.ts
`OttoEvent`) onto a per-session channel; the dashboard subscribes via
GET /events/{session_id}.
"""

import asyncio
import json
from collections import defaultdict, deque
from typing import Any, AsyncIterator

# Cap per-session history so a long-lived process (many production-heal calls) can't
# grow memory without bound and slow every SSE reconnect. Generous enough that a full
# demo (pre-launch + dozens of production heals) never evicts the certification rounds
# the report reads back.
_HISTORY_MAX = 5000


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[asyncio.Queue]] = defaultdict(list)
        self._history: dict[str, deque] = defaultdict(lambda: deque(maxlen=_HISTORY_MAX))

    def subscribe_with_history(self, session_id: str) -> tuple[asyncio.Queue, list[dict]]:
        """Register a subscriber AND snapshot history in one synchronous step.

        publish() appends to history then enqueues; doing both reads here without an
        await in between means no publish can interleave (asyncio is cooperative). So an
        event is delivered via EITHER the replay OR the queue, never both — no duplicates.
        """
        snapshot = list(self._history.get(session_id, ()))
        q: asyncio.Queue = asyncio.Queue()
        self._subs[session_id].append(q)
        return q, snapshot

    def unsubscribe(self, session_id: str, q: asyncio.Queue) -> None:
        if q in self._subs.get(session_id, []):
            self._subs[session_id].remove(q)

    async def publish(self, session_id: str, event: dict[str, Any]) -> None:
        self._history[session_id].append(event)
        for q in list(self._subs.get(session_id, [])):
            await q.put(event)

    def history(self, session_id: str) -> list[dict]:
        return list(self._history.get(session_id, ()))


bus = EventBus()


async def sse_stream(session_id: str) -> AsyncIterator[bytes]:
    """SSE generator. Replays history (so late subscribers catch up) then tails live."""
    q, past = bus.subscribe_with_history(session_id)
    try:
        yield b": connected\n\n"
        for event in past:
            yield f"data: {json.dumps(event)}\n\n".encode()
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=15.0)
                yield f"data: {json.dumps(event)}\n\n".encode()
            except asyncio.TimeoutError:
                yield b": keepalive\n\n"
    finally:
        bus.unsubscribe(session_id, q)
