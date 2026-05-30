"""Cekura integration — the real eval engine (sponsor / co-host).

Endpoints confirmed from docs.cekura.ai/api-reference (auth: X-CEKURA-API-KEY):
  test_framework/v1/scenarios/run_scenarios_with_websockets/   run a scenario suite
  test_framework  get-evaluator / get-metric / get-test-profile / get-agent  (create/read)
  test_framework  list-runs-with-ids / get-result                            (poll results)
  observability/v1/observe/                                                   log live calls

D3 task: this client is scaffolded against the documented routes. The exact request
field names must be confirmed against the live API reference (marked TODO(team)) and a
running agent reachable over a Daily WebRTC room (apps/agent/daily_runner.py) is
required for `run_suite` to actually execute. Until then `run_suite` raises, and the
swarm falls back to local sim/static automatically — the loop keeps working.
"""

from __future__ import annotations

import asyncio
import dataclasses

import httpx

from lineforge_spec import AgentSpec

from . import config
from .events import bus
from .personas import Persona

_HEADERS = {"X-CEKURA-API-KEY": config.CEKURA_API_KEY, "Content-Type": "application/json"}


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=config.CEKURA_BASE_URL, headers=_HEADERS, timeout=30.0)


async def run_suite(session_id: str, spec: AgentSpec, personas: list[Persona], round_no: int):
    """Run the persona suite via Cekura against the live agent and return CallResults.

    Publishes a {"type":"call"} event per result so the arena animates the same way as
    local mode. Imports CallResult lazily to avoid a circular import with swarm.
    """
    from .swarm import CallResult

    if not config.cekura_available():
        raise RuntimeError("CEKURA_API_KEY not set")
    agent_ws = _agent_websocket_url()
    if not agent_ws:
        raise RuntimeError("no agent websocket/Daily room configured for Cekura (set DAILY_ROOM_URL)")

    async with _client() as c:
        agent_id = await _ensure_agent(c, spec, agent_ws)
        scenario_ids = await _ensure_scenarios(c, agent_id, personas)
        run_ids = await _trigger_run(c, agent_id, scenario_ids, agent_ws)
        raw = await _poll_results(c, run_ids)

    by_persona = {r["persona_id"]: r for r in raw}
    results: list[CallResult] = []
    for p in personas:
        r = by_persona.get(p.id)
        if not r:
            continue
        res = CallResult(p.id, p.label, p.category, bool(r["passed"]),
                         str(r.get("reason", ""))[:300], r.get("transcript", []),
                         int(r.get("latency_ms", 0)), backend="cekura")
        await bus.publish(session_id, {"type": "call", "result": dataclasses.asdict(res)})
        results.append(res)
    return results


def _agent_websocket_url() -> str:
    # The Cekura simulated caller joins the agent here (Daily WebRTC room, manual mode).
    return config.DAILY_ROOM_URL or ""


async def _ensure_agent(c: httpx.AsyncClient, spec: AgentSpec, agent_ws: str) -> str:
    if config.CEKURA_AGENT_ID:
        return config.CEKURA_AGENT_ID
    # TODO(team): confirm payload shape against api-reference/test_framework/get-agent
    resp = await c.post("/test_framework/v1/agents/", json={
        "name": f"lineforge-{spec.business.name}",
        "type": "voice",
        "connection": {"method": "manual", "websocket_url": agent_ws},
    })
    resp.raise_for_status()
    return str(resp.json().get("id"))


async def _ensure_scenarios(c: httpx.AsyncClient, agent_id: str, personas: list[Persona]) -> list[str]:
    # TODO(team): confirm evaluator/scenario payload. Map our persona to Cekura's:
    #   personality -> Cekura personality (tone/interruption/accent),
    #   success_criteria -> an LLM-judge metric/rubric.
    ids: list[str] = []
    for p in personas:
        resp = await c.post("/test_framework/v1/evaluators/", json={
            "agent": agent_id,
            "name": p.label,
            "scenario": p.goal,
            "personality": {"description": p.personality},
            "metrics": [{"type": "llm_judge", "name": p.id, "criteria": p.success_criteria}],
        })
        resp.raise_for_status()
        ids.append(str(resp.json().get("id")))
    return ids


async def _trigger_run(c: httpx.AsyncClient, agent_id: str, scenario_ids: list[str], agent_ws: str) -> list[str]:
    resp = await c.post("/test_framework/v1/scenarios/run_scenarios_with_websockets/", json={
        "scenarios": [
            {"scenario_id": sid, "agent_id": agent_id, "websocket_url": agent_ws, "frequency": 1}
            for sid in scenario_ids
        ],
    })
    resp.raise_for_status()
    body = resp.json()
    runs = body.get("runs", body if isinstance(body, list) else [])
    return [str(r.get("run_id", r.get("id"))) for r in runs]


async def _poll_results(c: httpx.AsyncClient, run_ids: list[str], *, timeout: float = 180.0) -> list[dict]:
    # TODO(team): confirm get-result shape; this maps to {persona_id,passed,reason,transcript}.
    deadline = asyncio.get_event_loop().time() + timeout
    out: list[dict] = []
    pending = set(run_ids)
    while pending and asyncio.get_event_loop().time() < deadline:
        for rid in list(pending):
            resp = await c.get(f"/test_framework/v1/results/{rid}/")
            if resp.status_code != 200:
                continue
            data = resp.json()
            if data.get("status") in ("completed", "done", "finished"):
                out.append({
                    "persona_id": data.get("scenario_name") or data.get("evaluator_name"),
                    "passed": data.get("passed", data.get("success", False)),
                    "reason": data.get("summary") or data.get("reason", ""),
                    "transcript": data.get("transcript", []),
                    "latency_ms": data.get("latency_ms", 0),
                })
                pending.discard(rid)
        if pending:
            await asyncio.sleep(3.0)
    return out


async def observe(call_id: str, agent_id: int, transcript: list[dict], recording_url: str = "") -> None:
    """Log a live (Twilio) call to Cekura observability — production monitoring."""
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
