"""Cekura integration — the real eval engine (sponsor / co-host).

Contracts below are taken verbatim from docs.cekura.ai/api-reference (auth header
`X-CEKURA-API-KEY`). We use Cekura's **manual Pipecat** run path — we put an agent-under-test in a
Daily room and Cekura's simulated caller joins it. (The *automated* Pipecat mode, where Cekura
starts the agent itself from a Pipecat Cloud key, can't pass a per-build session id, so it can only
test the live agent — not the candidate spec under heal. We need the candidate, hence manual mode.)

`_acquire_room` (below) is what stands an agent-in-a-room up, per round, bound to THIS build's
candidate spec — static room, Pipecat Cloud, or a locally-spawned agent. See config.CEKURA_AGENT_HOST.

  POST /test_framework/v1/scenarios-external/run_scenarios_pipecat/
       body: {"scenarios": [{"scenario": <id>, "pipecat_room_url": <daily url>, "pipecat_token": <tok?>}]}
       -> {"id": <result_id>, "status": ..., "runs": [...]}
  GET  /test_framework/v1/results/{result_id}/
       -> {"status": completed|failed|..., "runs": {runId: {scenario, scenario_name,
            success: bool, expected_outcome:{score,explanation[]}, error_message, ...}}}
  POST /observability/v1/observe/   (production monitoring of live calls)

Plug-and-play: pre-create scenarios+personalities+metrics once in the Cekura dashboard and set
CEKURA_SCENARIO_MAP (persona_id -> scenario_id) and/or CEKURA_AXIS_SCENARIO_MAP (mutation axis ->
scenario_id) in .env. The production swarm-heal mutates one failure into many variations along a
few axes (accent, noise, anger, …); mapping by axis lets Cekura reuse a handful of voice scenarios
instead of one per variation. If nothing is mapped, `_resolve_scenarios` creates+caches them via the
API (needs CEKURA_AGENT_ID); if even that's missing, run_suite raises and the swarm falls back to
local sim/static — the loop keeps working.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
from typing import Awaitable, Callable

import httpx

from otto_spec import AgentSpec

from . import config
from .events import bus
from .personas import Persona

_TERMINAL = {"completed", "failed", "timeout", "cancelled"}

# Process-lifetime cache of scenarios we created on the fly, keyed by scenario-key (axis or id).
# Lets a production swarm-heal reuse the same Cekura voice scenario across all variations of an
# axis (and across repeated heals) instead of recreating one per variation each time.
_SCENARIO_CACHE: dict[str, int] = {}


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=config.CEKURA_BASE_URL,
        headers={"X-CEKURA-API-KEY": config.CEKURA_API_KEY, "Content-Type": "application/json"},
        timeout=30.0,
    )


async def run_suite(session_id: str, spec: AgentSpec, personas: list[Persona], round_no: int):
    """Run the persona suite via Cekura against our live Daily room; return CallResults.

    Publishes a {"type":"call"} event per result so the arena animates like local mode.
    Raises on any prerequisite gap so swarm.py can fall back to local eval.
    """
    from .swarm import CallResult

    if not config.cekura_available():
        raise RuntimeError("CEKURA_API_KEY not set")

    async with _client() as c:
        scenario_for = await _resolve_scenarios(c, personas)  # persona.id -> scenario id (may raise)

        # Collapse to ONE real voice call per distinct scenario — many swarm-heal variations share
        # an axis (accent, noise, anger, …) and thus a scenario, so we never run 30 real calls when
        # 10 distinct adversarial scenarios cover the space. Honest, and keeps a real run watchable.
        rep: dict[int, Persona] = {}
        for p in personas:
            sid = scenario_for.get(p.id)
            if sid is not None:
                rep.setdefault(int(sid), p)
        if not rep:
            raise RuntimeError("no Cekura scenarios resolved for personas (set CEKURA_SCENARIO_MAP / CEKURA_AXIS_SCENARIO_MAP / CEKURA_AGENT_ID)")
        if len(rep) < len(personas):
            await bus.publish(session_id, {"type": "fact", "topic": "cekura",
                                           "content": f"{len(personas)} variations → {len(rep)} reusable Cekura scenario(s): one real voice call per adversarial axis."})

        # Stand up the agent-under-test in a Daily room, serving THIS build's candidate spec. A fresh
        # agent per round is exactly what makes the before→after real: the post-heal run starts an
        # agent that loads the just-healed spec. Tear it down no matter how the run ends.
        room_url, token, teardown, host = await _acquire_room(session_id)
        await bus.publish(session_id, {"type": "fact", "topic": "cekura",
                                       "content": f"agent-under-test live in a Daily room ({host}) → Cekura calling {len(rep)} scenario(s) for real"})
        try:
            items = []
            for sid in rep:
                item = {"scenario": sid, "pipecat_room_url": room_url}
                if token:
                    item["pipecat_token"] = token
                items.append(item)

            resp = await c.post("/test_framework/v1/scenarios-external/run_scenarios_pipecat/", json={"scenarios": items})
            resp.raise_for_status()
            result_id = resp.json().get("id")
            if not result_id:  # no id => fall back to local eval immediately, don't poll /results/None/ for 4 min
                raise RuntimeError("Cekura run returned no result id")
            runs = await _poll_results(c, result_id)
        finally:
            await teardown()

    results = []
    for run in runs:
        sid = run.get("scenario")
        p = (rep.get(int(sid)) if sid is not None else None) or _match_name(personas, run.get("scenario_name", ""))
        if not p:
            continue
        res = CallResult(p.id, p.label, p.category, bool(run.get("success")),
                         str(run.get("reason", ""))[:300], [], int(run.get("duration_ms", 0)), backend="cekura")
        await bus.publish(session_id, {"type": "call", "result": dataclasses.asdict(res)})
        results.append(res)
    return results


async def _acquire_room(session_id: str) -> tuple[str, str, Callable[[], Awaitable[None]], str]:
    """Get an agent-under-test sitting in a Daily room for Cekura to call, serving THIS session's
    candidate spec. Returns (room_url, join_token, teardown, host_label).

    Precedence (config.CEKURA_AGENT_HOST pins one explicitly; 'auto' tries them in order):
      static — DAILY_ROOM_URL set; an agent is already running in it (e.g. apps/agent/daily_runner.py).
      pcc    — start the deployed Pipecat Cloud agent into a fresh room, this session id in the body.
      local  — provision our own room (+ both tokens) and spawn the agent locally against it.
    Raises if none is configured, so swarm.py falls back to the local sim — the loop never stalls.
    """
    host = config.CEKURA_AGENT_HOST

    async def _noop() -> None:
        return None

    # 1. static room — an operator manages the agent in it (manual ops / explicit override)
    if config.DAILY_ROOM_URL and host in ("auto", "static"):
        return config.DAILY_ROOM_URL, config.DAILY_ROOM_TOKEN, _noop, "static"

    # 2. Pipecat Cloud — the deployed agent, started fresh per round bound to the candidate spec
    if config.pcc_available() and host in ("auto", "pcc"):
        from . import pcc
        s = await pcc.start_session(session_id)
        await asyncio.sleep(2.0)  # let the (pre-warmed) microVM finish joining before Cekura dials in
        return s["room_url"], s.get("token", ""), _noop, "pcc"  # room `exp` auto-reclaims the session

    # 3. local — we own the room + both tokens and run the agent ourselves (best for localhost dev,
    #    where a Pipecat Cloud worker couldn't reach a localhost orchestrator for the candidate spec)
    if config.DAILY_API_KEY and host in ("auto", "local"):
        from . import daily
        room = await daily.create_room(prefix=f"otto-{session_id[:6]}")
        name = room["name"]
        agent_tok = await daily.mint_token(name, is_owner=True, user_name="otto-agent")
        cekura_tok = await daily.mint_token(name, is_owner=False, user_name="cekura-caller")
        proc = await _spawn_local_agent(session_id, room["url"], agent_tok)

        async def _td() -> None:
            await _kill(proc)
            await daily.delete_room(name)

        return room["url"], cekura_tok, _td, "local"

    raise RuntimeError(
        "no agent-in-room path for Cekura: set DAILY_ROOM_URL (+ run the agent), or "
        "PCC_API_KEY+PCC_AGENT_NAME (Pipecat Cloud), or DAILY_API_KEY (local agent)")


async def _spawn_local_agent(session_id: str, room_url: str, token: str) -> "asyncio.subprocess.Process":
    """Launch apps/agent/daily_runner.py against `room_url`, serving `session_id`'s candidate spec.
    Runs under the agent venv (config.AGENT_PYTHON) — the orchestrator venv has no pipecat installed."""
    py = config.AGENT_PYTHON
    runner = config.ROOT / "apps" / "agent" / "daily_runner.py"
    if not os.path.exists(py):
        raise RuntimeError(f"agent python not found at {py} (set AGENT_PYTHON, or use the pcc/static host)")
    env = {**os.environ,
           "OTTO_SESSION": session_id,            # → the agent loads /api/spec/<session>, the candidate
           "DAILY_ROOM_URL": room_url, "DAILY_ROOM_TOKEN": token,
           "ORCH_BASE_URL": config.ORCH_PUBLIC_URL or os.getenv("ORCH_BASE_URL", "http://localhost:8000")}
    proc = await asyncio.create_subprocess_exec(
        py, str(runner), cwd=str(runner.parent), env=env,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    await asyncio.sleep(config.LOCAL_AGENT_WARMUP_S)  # let it import pipecat + join the room before Cekura dials
    return proc


async def _kill(proc: "asyncio.subprocess.Process") -> None:
    """Best-effort stop of a spawned local agent; the room `exp` is the real backstop if this misses."""
    try:
        if proc.returncode is None:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=5.0)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _scenario_key(p: Persona) -> str:
    """Reuse one Cekura scenario across every variation that shares a mutation axis (accent, noise,
    anger, …); fall back to the persona id for un-axed personas (the pre-launch suite)."""
    return f"axis:{p.axis}" if getattr(p, "axis", "") else f"id:{p.id}"


async def _resolve_scenarios(c: httpx.AsyncClient, personas: list[Persona]) -> dict[str, int]:
    """persona.id -> Cekura scenario id. Resolution per persona, cheapest first:
      1. explicit CEKURA_SCENARIO_MAP by persona id
      2. CEKURA_AXIS_SCENARIO_MAP by mutation axis
      3. a scenario already created this process (cached by axis/id)
      4. create one now (real_world_smart) and cache it — needs CEKURA_AGENT_ID.
    Pre-launch personas (no axis) resolve by id exactly as before; swarm-heal variations resolve by
    axis, so a heal reuses a handful of scenarios instead of minting one per variation."""
    id_map = {str(k): int(v) for k, v in config.CEKURA_SCENARIO_MAP.items()}
    axis_map = {str(k): int(v) for k, v in config.CEKURA_AXIS_SCENARIO_MAP.items()}
    out: dict[str, int] = {}
    for p in personas:
        sid = id_map.get(p.id)
        if sid is None and getattr(p, "axis", ""):
            sid = axis_map.get(p.axis)
        if sid is None:
            sid = _SCENARIO_CACHE.get(_scenario_key(p))
        if sid is None:
            sid = await _create_scenario(c, p)  # caches; may raise if no CEKURA_AGENT_ID
        if sid is not None:
            out[p.id] = int(sid)
    return out


async def _create_scenario(c: httpx.AsyncClient, p: Persona) -> int:
    """Create one real_world_smart scenario for a persona (or axis), linked to CEKURA_AGENT_ID, and
    cache it by scenario-key so siblings/repeat-heals reuse it. Prefer pre-creating in the dashboard
    + CEKURA_SCENARIO_MAP / CEKURA_AXIS_SCENARIO_MAP for the demo; this is the zero-setup fallback."""
    if not config.CEKURA_AGENT_ID:
        raise RuntimeError("set CEKURA_SCENARIO_MAP / CEKURA_AXIS_SCENARIO_MAP or CEKURA_AGENT_ID so scenarios can be created")
    body: dict = {
        "name": f"otto-{p.axis or p.id}",
        "scenario_type": "real_world_smart",
        "agent": int(config.CEKURA_AGENT_ID),
        "instructions": f"Caller goal: {p.goal}\nSuccess criteria: {p.success_criteria}",
        "scenario_language": "en",
        "tags": ["otto", p.category] + ([p.axis] if getattr(p, "axis", "") else []),
    }
    if config.CEKURA_PERSONALITY_ID:
        body["personality"] = int(config.CEKURA_PERSONALITY_ID)
    if config.CEKURA_METRIC_IDS:
        body["metrics"] = config.CEKURA_METRIC_IDS
    r = await c.post("/test_framework/v1/scenarios/", json=body)
    r.raise_for_status()
    sid = int(r.json().get("id"))
    _SCENARIO_CACHE[_scenario_key(p)] = sid
    return sid


async def _poll_results(c: httpx.AsyncClient, result_id, *, timeout: float = 240.0) -> list[dict]:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        r = await c.get(f"/test_framework/v1/results/{result_id}/")
        if r.status_code == 200:
            data = r.json()
            if data.get("status") in _TERMINAL:
                out = []
                for _rid, run in (data.get("runs") or {}).items():
                    eo = run.get("expected_outcome") or {}
                    expl = eo.get("explanation")
                    reason = (" ".join(expl) if isinstance(expl, list) else str(expl or "")) or run.get("error_message", "")
                    out.append({
                        "scenario": run.get("scenario"),
                        "scenario_name": run.get("scenario_name", ""),
                        "success": run.get("success", False),
                        "reason": reason,
                        "duration_ms": run.get("duration_ms", 0),
                    })
                return out
        await asyncio.sleep(3.0)
    raise RuntimeError(f"Cekura result {result_id} did not finish within {int(timeout)}s")


def _match_name(personas: list[Persona], scenario_name: str):
    name = (scenario_name or "").lower()
    return next((p for p in personas if p.id in name or p.label.lower() in name), None)


async def observe(call_id: str, agent_id: int, transcript: list[dict], recording_url: str = "") -> None:
    """Log a completed live (Twilio) call to Cekura observability — production monitoring."""
    if not config.cekura_available():
        return
    async with _client() as c:
        await c.post("/observability/v1/observe/", json={
            "call_id": call_id,
            "agent": agent_id,
            "transcript_type": "pipecat",
            "transcript_json": transcript,
            "voice_recording_url": recording_url,
        })
