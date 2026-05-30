"""Otto orchestrator API.

  POST /api/run            {url?, use_cached?}  -> {session_id}; starts the pipeline
  GET  /api/events/{sid}    Server-Sent Events stream of OttoEvent (the dashboard)
  GET  /api/spec/{sid}      current AgentSpec
  POST /api/activate/{sid}  manually (re)activate the line for a session
  GET  /api/health
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import uuid

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, store
from .events import sse_stream
from .pipeline import activate, run_pipeline

app = FastAPI(title="Otto Orchestrator", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class RunReq(BaseModel):
    url: str | None = None
    use_cached: bool = False
    cached: str | None = None  # demo fixture: "piccino" | "contractor" | "clinic"
    extra_info: str | None = None  # owner-provided note folded into the agent's knowledge


class ObserveReq(BaseModel):
    transcript: str | None = None  # a completed live-call transcript to audit (voice-only)
    persona: str | None = None     # or an explicit failure signal (persona id)
    trace_id: str | None = None    # or a synthetic CallTrace fixture id (demo replay buttons)
    trace: dict | None = None      # or a full CallTrace the live agent emitted (event stream)


class SimReq(BaseModel):
    trace_id: str = "accent_struggle"  # which enriched trace to replay through the live channel
    wps: float = 7.0                   # words/second of the streamed transcript (the per-word feel)


@app.api_route("/twiml", methods=["GET", "POST"])
async def twiml() -> Response:
    """Twilio Voice webhook → bridge the call to the Pipecat Cloud agent over Media Streams.
    Point the Twilio number's Voice URL here. Host = <agent>.<org> on Pipecat Cloud."""
    host = os.getenv("PCC_AGENT_HOST", "otto-agent.otto")
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response><Connect>"
        '<Stream url="wss://api.pipecat.daily.co/ws/twilio">'
        f'<Parameter name="_pipecatCloudServiceHost" value="{host}"/>'
        "</Stream></Connect></Response>"
    )
    return Response(content=xml, media_type="application/xml")


@app.get("/api/health")
async def health() -> dict:
    return {
        "ok": True,
        "swarm_mode": config.SWARM_MODE,
        "llm": config.llm_available(),
        "llm_provider": config.LLM_PROVIDER,
        "cekura": config.cekura_available(),
        "pass_gate": config.PASS_GATE,
        "personas": config.SWARM_PERSONAS,
    }


@app.post("/api/run")
async def run(req: RunReq) -> dict:
    session_id = uuid.uuid4().hex[:12]
    asyncio.create_task(run_pipeline(session_id, req.url, req.use_cached, req.cached, req.extra_info))
    return {"session_id": session_id}


@app.get("/api/events/{session_id}")
async def events(session_id: str) -> StreamingResponse:
    return StreamingResponse(
        sse_stream(session_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.get("/api/spec/{session_id}")
async def get_spec(session_id: str) -> dict:
    spec = store.get_spec(session_id)
    if not spec:
        raise HTTPException(status_code=404, detail="no spec for session")
    return spec.model_dump()


@app.get("/api/active-session")
async def active_session_route() -> dict:
    """The session id of the currently-deployed business. The live agent resolves this so its live
    transcript stream lands on the same session the dashboard is watching — zero manual wiring."""
    return {"session_id": store.active_session()}


@app.get("/api/active-spec")
async def active_spec() -> dict:
    """The most-recently-activated business's spec — the deployed voice agent fetches this so
    an inbound call is answered as whatever was last onboarded + activated through the UI."""
    spec = store.get_active_spec()
    if not spec:
        raise HTTPException(status_code=404, detail="no active business yet")
    return spec.model_dump()


@app.get("/api/report/{session_id}")
async def report_route(session_id: str) -> dict:
    """Evaluation report for a session — every number read back from the real run history."""
    from .report import build_report
    if not store.get_spec(session_id):
        raise HTTPException(status_code=404, detail="no session")
    return build_report(session_id)


@app.post("/api/activate/{session_id}")
async def activate_route(session_id: str) -> dict:
    spec = store.get_spec(session_id)
    if not spec:
        raise HTTPException(status_code=404, detail="no spec for session")
    await activate(session_id, spec)
    return {"ok": True, "active": store.get_active(session_id)}


@app.post("/api/observe/{session_id}")
async def observe_route(session_id: str, req: ObserveReq) -> dict:
    """Production loop: audit a live call; on failure, targeted swarm-heal + re-verify.

    Three inputs, richest first: a structured `trace`/`trace_id` (full event stream → the
    multi-dimensional failure taxonomy), or a `persona` signal / `transcript` (voice-only)."""
    from . import live
    from .observe import observe_call, observe_trace
    if req.trace or req.trace_id:
        from otto_spec import CallTrace
        from . import traces
        tr = CallTrace.model_validate(req.trace) if req.trace else traces.get(req.trace_id)
        if tr is None:
            raise HTTPException(status_code=404, detail=f"unknown trace_id {req.trace_id}")
        result = await observe_trace(session_id, tr)
        label = (traces.TRACES.get(req.trace_id) or {}).get("label", req.trace_id) if req.trace_id else "live call"
        total = live.record_call(session_id, {  # every finished call shows up in the feed
            "call_id": tr.call_id, "scenario": label,
            "turns": sum(1 for e in tr.events if e.kind in ("hear", "say")),
            "failed": bool(result.get("failed")), "failures": [f.get("title") for f in (result.get("failures") or [])],
            "pre_pass": result.get("pre_pass"), "post_pass": result.get("post_pass"), "spec_version": result.get("spec_version")})
        from .events import bus
        await bus.publish(session_id, {"type": "call_recorded", "call": {"scenario": label, "failed": bool(result.get("failed"))}, "total_calls": total})
        return result
    return await observe_call(session_id, transcript=req.transcript, persona=req.persona)


@app.post("/api/live/{session_id}")
async def live_route(session_id: str, ev: dict = Body(default={})) -> dict:
    """Ingest one LIVE event from the agent (or simulator) — streamed per turn / per word as the
    call happens. Fans out to SSE + runs incremental failure detection. See app/live.py."""
    from . import live
    return await live.ingest(session_id, ev)


@app.post("/api/simulate-live/{session_id}")
async def simulate_live_route(session_id: str, req: SimReq) -> dict:
    """Replay an enriched trace through the live channel word-by-word — the 'watch it live' demo
    with no phone needed. Runs in the background so the HTTP call returns immediately."""
    from . import live, traces
    if not store.get_spec(session_id):
        raise HTTPException(status_code=404, detail="no session — build/activate a business first")
    if traces.get(req.trace_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown trace_id {req.trace_id}")
    asyncio.create_task(live.simulate(session_id, req.trace_id, wps=req.wps))
    return {"ok": True, "streaming": req.trace_id}


@app.get("/api/calls/{session_id}")
async def calls_route(session_id: str) -> dict:
    """The finished-calls feed for a session — so every completed call shows up in the UI."""
    from . import live
    return {"calls": live.calls(session_id)}


@app.get("/api/traces")
async def traces_route(vertical: str | None = None) -> dict:
    """Synthetic action-failure call traces to replay. With ?vertical=, returns the ordered
    set to surface for that business (its own first, then universal ones)."""
    from . import traces
    if vertical:
        return {"traces": [{"id": tid, "label": label} for tid, label in traces.for_vertical(vertical)]}
    return {"traces": [{"id": tid, "label": t["label"], "vertical": t["vertical"]} for tid, t in traces.TRACES.items()]}


@app.post("/api/tool/{name}")
async def tool_route(name: str, args: dict = Body(default={}), session: str | None = None) -> dict:
    """Execute a business tool — shared by the live agent (as a middleman) + the dashboard. With a
    `session`, runs the per-website synthesized tool via the spec-driven engine (live_query fetches
    the site mid-call; stateful hits the sandbox backend); without one, falls back to the backend by name."""
    from . import tool_engine
    spec = store.get_spec(session) if session else None
    return await tool_engine.execute(spec, name, args)


@app.get("/api/state")
async def state_route() -> dict:
    from . import mock_services
    return mock_services.snapshot()


@app.get("/api/notifications")
async def notifications_route() -> dict:
    from . import mock_services
    return {"notifications": mock_services.get_notifications()}


# Serve the mission-control dashboard (apps/web) at / when present. Mounted last so
# /api/* routes above take precedence.
_WEB = pathlib.Path(__file__).resolve().parents[2] / "web"
if _WEB.exists():
    app.mount("/", StaticFiles(directory=str(_WEB), html=True), name="web")
