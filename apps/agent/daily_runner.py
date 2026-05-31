"""Daily WebRTC runner — the room Cekura's simulated callers join (the swarm path).

Cekura (manual Pipecat integration) is given this room's URL + token and joins as a
caller; the agent runs the SAME pipeline as the phone path. Run one of these per
scenario, or keep a pool warm.

Run:  uv run --python 3.12 python daily_runner.py
Env:  DAILY_ROOM_URL, DAILY_ROOM_TOKEN, OTTO_SESSION, ORCH_BASE_URL.

This is the agent for the orchestrator's `local` Cekura host path: cekura._spawn_local_agent
launches it with OTTO_SESSION set, so it loads /api/spec/<session> — the build's CANDIDATE spec —
and joins the room we provisioned for Cekura's caller. (Pipecat Cloud uses bot.py directly instead.)
"""

from __future__ import annotations

import asyncio
import os
import pathlib

import httpx
from otto_spec import AgentSpec

import bot


async def main() -> None:
    spec = await bot.load_spec()
    from pipecat.transports.daily.transport import DailyParams, DailyTransport  # pipecat 1.x path (matches bot.py)

    transport = DailyTransport(
        room_url=os.environ["DAILY_ROOM_URL"],
        token=os.getenv("DAILY_ROOM_TOKEN"),  # matches config.py + .env.example (not DAILY_TOKEN)
        bot_name=f"{spec.business.name} agent",
        params=DailyParams(audio_in_enabled=True, audio_out_enabled=True),
    )
    await bot.run_bot(transport, spec)


if __name__ == "__main__":
    asyncio.run(main())
