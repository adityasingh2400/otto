"""Cekura integration — the real eval engine (sponsor / co-host).

Contracts below are taken verbatim from docs.cekura.ai/api-reference (auth header
`X-CEKURA-API-KEY`). Our agent exposes a Daily WebRTC room (apps/agent/daily_runner.py),
so we use the **Pipecat/Daily** run path — Cekura's simulated caller joins our room:

  POST /test_framework/v1/scenarios-external/run_scenarios_pipecat/
       body: {"scenarios": [{"scenario": <id>, "pipecat_room_url": <daily url>, "pipecat_token": <tok?>}]}
       -> {"id": <result_id>, "status": ..., "runs": [...]}
  GET  /test_framework/v1/results/{result_id}/
       -> {"status": completed|failed|..., "runs": {runId: {scenario, scenario_name,
            success: bool, expected_outcome:{score,explanation[]}, error_message, ...}}}
  POST /observability/v1/observe/   (production monitoring of live calls)

Plug-and-play: pre-create scenarios+personalities+metrics once in the Cekura dashboard
and set CEKURA_AGENT_ID + CEKURA_SCENARIO_MAP (persona_id -> scenario_id) in .env. If
that's missing, run_suite raises and the swarm falls back to local sim/static — the loop
keeps working. `_ensure_scenarios` can also create them via the API (needs CEKURA_AGENT_ID).
"""

from __future__ import annotations

import asyncio
import dataclasses

import httpx

from otto_spec import AgentSpec

from . import config
from .events import bus
from .personas import Persona

_TERMINAL = {"completed", "failed", "timeout", "cancelled"}


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
    if not config.DAILY_ROOM_URL:
        raise RuntimeError("DAILY_ROOM_URL not set (the agent must expose a Daily room for Cekura to join)")

    async with _client() as c:
        scenario_map = dict(config.CEKURA_SCENARIO_MAP)
        if not scenario_map:
            scenario_map = await _ensure_scenarios(c, personas)  # may raise

        items, by_scenario = [], {}
        for p in personas:
            sid = scenario_map.get(p.id)
            if sid is None:
                continue
            item = {"scenario": int(sid), "pipecat_room_url": config.DAILY_ROOM_URL}
            if config.DAILY_ROOM_TOKEN:
                item["pipecat_token"] = config.DAILY_ROOM_TOKEN
            items.append(item)
            by_scenario[int(sid)] = p
        if not items:
            raise RuntimeError("no Cekura scenarios mapped to personas (set CEKURA_SCENARIO_MAP)")

        resp = await c.post("/test_framework/v1/scenarios-external/run_scenarios_pipecat/", json={"scenarios": items})
        resp.raise_for_status()
        result_id = resp.json().get("id")
        if not result_id:  # no id => fall back to local eval immediately, don't poll /results/None/ for 4 min
            raise RuntimeError("Cekura run returned no result id")
        runs = await _poll_results(c, result_id)

    results = []
    for run in runs:
        p = by_scenario.get(run.get("scenario")) or _match_name(personas, run.get("scenario_name", ""))
        if not p:
            continue
        res = CallResult(p.id, p.label, p.category, bool(run.get("success")),
                         str(run.get("reason", ""))[:300], [], int(run.get("duration_ms", 0)), backend="cekura")
        await bus.publish(session_id, {"type": "call", "result": dataclasses.asdict(res)})
        results.append(res)
    return results


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


async def _ensure_scenarios(c: httpx.AsyncClient, personas: list[Persona]) -> dict[str, int]:
    """Create one real_world_smart scenario per persona, linked to CEKURA_AGENT_ID.

    Returns {persona_id: scenario_id}. Optional env: CEKURA_PERSONALITY_ID, CEKURA_METRIC_IDS
    (comma-separated). Prefer pre-creating in the dashboard + CEKURA_SCENARIO_MAP for the demo.
    """
    if not config.CEKURA_AGENT_ID:
        raise RuntimeError("set CEKURA_SCENARIO_MAP or CEKURA_AGENT_ID so scenarios can be created")
    out: dict[str, int] = {}
    for p in personas:
        body: dict = {
            "name": f"otto-{p.id}",
            "scenario_type": "real_world_smart",
            "agent": int(config.CEKURA_AGENT_ID),
            "instructions": f"Caller goal: {p.goal}\nSuccess criteria: {p.success_criteria}",
            "scenario_language": "en",
            "tags": ["otto", p.category],
        }
        if config.CEKURA_PERSONALITY_ID:
            body["personality"] = int(config.CEKURA_PERSONALITY_ID)
        if config.CEKURA_METRIC_IDS:
            body["metrics"] = config.CEKURA_METRIC_IDS
        r = await c.post("/test_framework/v1/scenarios/", json=body)
        r.raise_for_status()
        out[p.id] = int(r.json().get("id"))
    return out


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
