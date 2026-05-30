"""Daily WebRTC runner — the room Cekura's simulated callers join (the swarm path).

Cekura (manual Pipecat integration) is given this room's URL + token and joins as a
caller; the agent runs the SAME pipeline as the phone path. Run one of these per
scenario, or keep a pool warm.

Run:  uv run --python 3.12 python daily_runner.py
Env:  DAILY_ROOM_URL, DAILY_API_KEY, LINEFORGE_SESSION (or falls back to cached piccino)

D3 TODO(team): wire this to app/cekura.py run_suite (it passes DAILY_ROOM_URL as the
agent websocket). Confirm DailyTransport params against the pinned Pipecat version.
"""

from __future__ import annotations

import asyncio
import os
import pathlib

import httpx
from lineforge_spec import AgentSpec

import bot


async def main() -> None:
    spec = await _load_spec()
    from pipecat.transports.services.daily import DailyParams, DailyTransport

    transport = DailyTransport(
        room_url=os.environ["DAILY_ROOM_URL"],
        token=os.getenv("DAILY_TOKEN"),
        bot_name=f"{spec.business.name} agent",
        params=DailyParams(audio_in_enabled=True, audio_out_enabled=True),
    )
    await bot.run_bot(transport, spec)


async def _load_spec() -> AgentSpec:
    session = os.getenv("LINEFORGE_SESSION", "")
    orch = os.getenv("ORCH_BASE_URL", "http://localhost:8000")
    if session:
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                r = await c.get(f"{orch}/api/spec/{session}")
                if r.status_code == 200:
                    return AgentSpec.model_validate(r.json())
        except Exception:
            pass
    cached = pathlib.Path(__file__).resolve().parents[2] / "packages" / "spec" / "piccino.json"
    return AgentSpec.model_validate_json(cached.read_text())


if __name__ == "__main__":
    asyncio.run(main())
