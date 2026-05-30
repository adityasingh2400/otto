"""LineForge orchestrator API.

  POST /api/run            {url?, use_cached?}  -> {session_id}; starts the pipeline
  GET  /api/events/{sid}    Server-Sent Events stream of LineForgeEvent (the dashboard)
  GET  /api/spec/{sid}      current AgentSpec
  POST /api/activate/{sid}  manually (re)activate the line for a session
  GET  /api/health
"""

from __future__ import annotations

import asyncio
import pathlib
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, store
from .events import sse_stream
from .pipeline import activate, run_pipeline

app = FastAPI(title="LineForge Orchestrator", version="0.1.0")
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
    transcript: str | None = None  # a completed live-call transcript to audit
    persona: str | None = None     # or an explicit failure signal (persona id)


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


@app.post("/api/activate/{session_id}")
async def activate_route(session_id: str) -> dict:
    spec = store.get_spec(session_id)
    if not spec:
        raise HTTPException(status_code=404, detail="no spec for session")
    await activate(session_id, spec)
    return {"ok": True, "active": store.get_active(session_id)}


@app.post("/api/observe/{session_id}")
async def observe_route(session_id: str, req: ObserveReq) -> dict:
    """Production loop: audit a live call; on failure, targeted swarm-heal + re-verify."""
    from .observe import observe_call
    return await observe_call(session_id, transcript=req.transcript, persona=req.persona)


# Serve the mission-control dashboard (apps/web) at / when present. Mounted last so
# /api/* routes above take precedence.
_WEB = pathlib.Path(__file__).resolve().parents[2] / "web"
if _WEB.exists():
    app.mount("/", StaticFiles(directory=str(_WEB), html=True), name="web")
