"""Daily REST helpers — provision a short-lived room + meeting tokens on OUR Daily domain.

Used by the `local` agent-host path in app/cekura.py: when we run the agent-under-test ourselves
(instead of on Pipecat Cloud), we own the room and mint BOTH join tokens — the agent's and Cekura's
simulated caller's — so there are no cross-domain token problems. The room is created with an `exp`
and `eject_at_room_exp`, so a swarm run cleans itself up even if teardown is missed.

Contracts (https://docs.daily.co/reference/rest-api):
  POST   /v1/rooms           {privacy, properties:{exp, eject_at_room_exp, …}} -> {url, name, …}
  POST   /v1/meeting-tokens  {properties:{room_name, is_owner, user_name, exp}} -> {token}
  DELETE /v1/rooms/{name}
Auth: Authorization: Bearer <DAILY_API_KEY>.
"""

from __future__ import annotations

import time

import httpx

from . import config

_BASE = "https://api.daily.co/v1"


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=_BASE,
        headers={"Authorization": f"Bearer {config.DAILY_API_KEY}", "Content-Type": "application/json"},
        timeout=20.0,
    )


async def create_room(prefix: str = "otto", exp_s: int | None = None) -> dict:
    """Create a public, auto-expiring room for one swarm run. Returns the room dict ({'url','name',…})."""
    exp = int(time.time()) + (exp_s or config.CEKURA_ROOM_EXP_S)
    async with _client() as c:
        r = await c.post("/rooms", json={
            # public so a tokenless join still works; tokens minted below add ownership/identity.
            "privacy": "public",
            "properties": {"exp": exp, "eject_at_room_exp": True,
                           "enable_prejoin_ui": False, "start_video_off": True},
        })
        r.raise_for_status()
        return r.json()


async def mint_token(room_name: str, *, is_owner: bool, user_name: str, exp_s: int | None = None) -> str:
    """Mint a meeting token scoped to `room_name` (same Daily domain as create_room)."""
    exp = int(time.time()) + (exp_s or config.CEKURA_ROOM_EXP_S)
    async with _client() as c:
        r = await c.post("/meeting-tokens", json={"properties": {
            "room_name": room_name, "is_owner": is_owner, "user_name": user_name, "exp": exp}})
        r.raise_for_status()
        return r.json()["token"]


async def delete_room(room_name: str) -> None:
    """Best-effort teardown — the room's `exp` is the real backstop, so a failure here is harmless."""
    try:
        async with _client() as c:
            await c.delete(f"/rooms/{room_name}")
    except Exception:
        pass
