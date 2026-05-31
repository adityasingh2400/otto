"""Pipecat Cloud REST — start a session of the deployed agent-under-test in a Daily room.

The swarm starts ONE session per round. Crucially it passes this build's session id in the start
`body`, which arrives at the agent as `runner_args.body` (see apps/agent/bot.py): the agent then
loads /api/spec/<session> — the CANDIDATE spec for this build — instead of the last-activated one.
That's what makes Cekura's before→after real across a heal: a fresh start per round picks up the
just-healed spec. Pipecat Cloud creates the room (createDailyRoom) and returns its url + an owner
token, both of which we hand to Cekura's simulated caller.

Contract (https://docs.pipecat.ai/deployment/pipecat-cloud — REST: start an agent session):
  POST https://api.pipecat.daily.co/v1/public/{agentName}/start
       Authorization: Bearer <PCC_API_KEY>
       {createDailyRoom, dailyRoomProperties, dailyMeetingTokenProperties, body}
    -> {dailyRoom, dailyToken, sessionId, iceConfig}

Teardown is handled by the room's `exp` (set in dailyRoomProperties) + Cekura ending the call, so
there's no separate stop call to get wrong — the microVM is reclaimed when the room closes.
"""

from __future__ import annotations

import time

import httpx

from . import config


async def start_session(session_id: str, *, exp_s: int | None = None) -> dict:
    """Start the deployed agent into a fresh room bound to `session_id`'s candidate spec.

    Returns {'room_url', 'token', 'pcc_session_id'}. Raises on any failure so cekura._acquire_room
    can fall through to the next host (and ultimately the loop falls back to the local sim).
    """
    if not config.pcc_available():
        raise RuntimeError("PCC_API_KEY / PCC_AGENT_NAME not set")
    exp = int(time.time()) + (exp_s or config.CEKURA_ROOM_EXP_S)

    body: dict = {"session": session_id}
    if config.ORCH_PUBLIC_URL:  # how the PCC worker reaches THIS orchestrator to fetch the candidate spec
        body["orch_base_url"] = config.ORCH_PUBLIC_URL
    payload = {
        "createDailyRoom": True,
        # passed through to Daily room creation → the room (and thus the session) auto-expires
        "dailyRoomProperties": {"exp": exp, "eject_at_room_exp": True, "enable_prejoin_ui": False},
        "dailyMeetingTokenProperties": {"is_owner": True},
        "body": body,
    }
    url = f"{config.PCC_BASE_URL}/v1/public/{config.PCC_AGENT_NAME}/start"
    async with httpx.AsyncClient(timeout=90.0) as c:  # a cold microVM can take a while to boot + join
        r = await c.post(url, headers={"Authorization": f"Bearer {config.PCC_API_KEY}",
                                       "Content-Type": "application/json"}, json=payload)
        r.raise_for_status()
        data = r.json()

    room = data.get("dailyRoom") or data.get("daily_room")
    if not room:
        raise RuntimeError(f"PCC start returned no dailyRoom: {str(data)[:200]}")
    return {
        "room_url": room,
        "token": data.get("dailyToken") or data.get("daily_token") or "",
        "pcc_session_id": data.get("sessionId") or data.get("session_id") or "",
    }
