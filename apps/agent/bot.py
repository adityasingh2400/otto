"""Pipecat voice agent — serves an AgentSpec over a transport.

Same pipeline, two front doors (the architecture's whole point — the swarm tests the
exact code that answers the phone):
  - Twilio (live call): `uv run bot.py --transport twilio --proxy <ngrok-host>`
    (Pipecat's runner provides the FastAPI server + TwiML + serializer)
  - Daily (Cekura swarm): apps/agent/daily_runner.py

Imports/pattern are written against the documented Pipecat 0.0.x API (pyproject pins
`pipecat-ai>=0.0.96,<1.0`; the 1.x line renamed several of these). This module is not
executed in this repo — install the agent venv and verify on the day. Service imports
are top-level because the agent venv installs pipecat; the orchestrator never imports it.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import time

import httpx
from otto_spec import AgentSpec, CallEvent, CallTrace

# Load the repo-root .env so the live agent picks up its provider keys + model ids the same way the
# orchestrator does — `uv run bot.py` / daily_runner then "just works" without exporting env first.
# (Guarded: if python-dotenv or the file is absent, the process env still wins.)
try:
    from dotenv import load_dotenv
    load_dotenv(pathlib.Path(__file__).resolve().parents[2] / ".env")
except Exception:
    pass

_FAIL_STATUS = {"unavailable", "out_of_stock", "sold_out", "error", "failed", "declined"}


class _Recorder:
    """PER-CALL tool event recorder. One instance per run_bot() invocation, so concurrent
    calls never corrupt each other's CallTrace (the old module-level globals were a race)."""

    def __init__(self) -> None:
        self.t0 = time.monotonic()
        self.events: list[dict] = []

    def ms(self) -> int:
        return int((time.monotonic() - self.t0) * 1000)

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.daily.transport import DailyParams
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams
from pipecat.frames.frames import InterimTranscriptionFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


# ── tool schemas from the spec ───────────────────────────────────────────────
def build_tool_schemas(spec: AgentSpec) -> ToolsSchema | None:
    """Pipecat 1.x ToolsSchema (provider-agnostic) built from the spec's tools."""
    fns = []
    for t in spec.tools:
        props, required = {}, []
        for p in t.params:
            props[p.name] = {"type": _json_type(p.type), "description": p.description}
            if p.required:
                required.append(p.name)
        fns.append(FunctionSchema(name=t.name, description=t.description,
                                  properties=props, required=required))
    return ToolsSchema(standard_tools=fns) if fns else None


def _json_type(t: str) -> str:
    return {"integer": "integer", "number": "number", "boolean": "boolean"}.get(t, "string")


# ── provider selection (reliability default + sponsor swaps via .env) ────────
def _stt():
    if os.getenv("STT_PROVIDER") == "nvidia":  # sponsor: NVIDIA Nemotron Speech Streaming (Parakeet) — full-NVIDIA stack
        # The hackathon ASR is a raw websocket fleet (NVIDIA_ASR_URL=ws://…:8080), NOT the NIM/Riva
        # endpoint — so we use the vendored NVidiaWebSocketSTTService that speaks its protocol.
        from nvidia_stt import NVidiaWebSocketSTTService
        return NVidiaWebSocketSTTService(
            url=os.getenv("NVIDIA_ASR_URL", "ws://localhost:8080"),
            strip_interim_prefix=True,  # the fleet emits cumulative interims
        )
    from pipecat.services.deepgram.stt import DeepgramSTTService
    # VERBATIM transcription on purpose: filler_words=true keeps "uh"/"um", smart_format=false stops
    # the cleaner from rewriting hesitations/false-starts into a tidy sentence, and interim_results
    # gives the per-word stream the live view renders. The disfluency is signal (a struggling caller),
    # so we keep it rather than letting the ASR scrub it. (NVIDIA-fleet path above can't be configured
    # this way — switch STT_PROVIDER=deepgram if you need the guaranteed verbatim capture.)
    try:
        from deepgram import LiveOptions
        return DeepgramSTTService(api_key=os.getenv("DEEPGRAM_API_KEY"), live_options=LiveOptions(
            model="nova-2", language="en-US", interim_results=True,
            smart_format=False, punctuate=True, filler_words=True))
    except Exception:
        return DeepgramSTTService(api_key=os.getenv("DEEPGRAM_API_KEY"))


def _tts(spec: AgentSpec):
    p = os.getenv("TTS_PROVIDER", "cartesia")
    if p == "nvidia":  # sponsor: NVIDIA Magpie (the Nemotron voice blueprint TTS)
        # Same riva.* -> nvidia.* rename as STT — try both so the install can't ImportError.
        try:
            from pipecat.services.nvidia.tts import NvidiaTTSService as _TTS
        except ImportError:
            from pipecat.services.riva.tts import RivaTTSService as _TTS  # older module family
        return _TTS(api_key=os.getenv("NVIDIA_API_KEY"))
    if p == "elevenlabs":
        from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
        return ElevenLabsTTSService(api_key=os.getenv("ELEVENLABS_API_KEY"),
                                    voice_id=spec.voice.tts_voice or "EXAVITQu4vr4xnSDxMaL")
    from pipecat.services.cartesia.tts import CartesiaTTSService
    return CartesiaTTSService(api_key=os.getenv("CARTESIA_API_KEY"),
                              voice_id=spec.voice.tts_voice or "71a7ad14-091c-4e8e-a314-022ece01c121")


def _llm():
    if os.getenv("LLM_PROVIDER") == "bedrock":  # sponsor: AWS Bedrock cascaded text LLM (e.g. Nova Pro)
        from pipecat.services.aws.llm import AWSBedrockLLMService
        return AWSBedrockLLMService(model=os.getenv("BEDROCK_MODEL_ID", "us.amazon.nova-pro-v1:0"))
        # Lowest-latency alternative: AWSNovaSonicLLMService (pipecat.services.aws.nova_sonic)
        # is speech-to-speech — it REPLACES stt+tts, so use a different pipeline if you pick it.
    if os.getenv("LLM_PROVIDER") == "nvidia":  # sponsor: NVIDIA Nemotron via NIM (OpenAI-compatible) — the blueprint LLM
        from pipecat.services.openai.llm import OpenAILLMService
        # A fast Nemotron (e.g. Nano) keeps turn latency low on a live call; confirm the exact id on build.nvidia.com.
        return OpenAILLMService(api_key=os.getenv("NVIDIA_API_KEY"),
                                base_url=os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
                                model=os.getenv("AGENT_NVIDIA_MODEL") or os.getenv("NVIDIA_MODEL", "nvidia/llama-3.3-nemotron-super-49b-v1"))
    if os.getenv("LLM_PROVIDER") == "gemini":  # free Gemini/Gemma via the OpenAI-compatible endpoint
        from pipecat.services.openai.llm import OpenAILLMService
        # Use a fast Flash model for the live call (a 27B open model is too slow for low-latency voice).
        return OpenAILLMService(api_key=os.getenv("GEMINI_API_KEY"),
                                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                                model=os.getenv("AGENT_LLM_MODEL", "gemini-2.5-flash-lite"))
    from pipecat.services.openai.llm import OpenAILLMService
    return OpenAILLMService(api_key=os.getenv("OPENAI_API_KEY"), model=os.getenv("LLM_MODEL", "gpt-4o"))


def _today_line(spec: AgentSpec) -> str:
    """A current-date anchor so the agent resolves 'tonight'/'Saturday' to real dates
    (and never books in the wrong year). Computed in the business's timezone per call."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    try:
        now = datetime.now(ZoneInfo(spec.business.timezone or "America/Los_Angeles"))
    except Exception:
        now = datetime.now()
    return (f"\n\n# Current date/time\nRight now it is {now:%A, %B %-d, %Y, %-I:%M %p} "
            f"({spec.business.timezone}). Resolve relative dates like 'tonight', 'tomorrow', "
            f"or 'Saturday' against this, and always pass tool dates as YYYY-MM-DD in the current year.")


# ── the pipeline ─────────────────────────────────────────────────────────────
async def run_bot(transport: BaseTransport, spec: AgentSpec) -> None:
    if os.getenv("PIPELINE_MODE") == "s2s":
        return await _run_bot_s2s(transport, spec)
    rec = _Recorder()  # per-call state — concurrency-safe across simultaneous calls
    stt, tts, llm = _stt(), _tts(spec), _llm()
    tools = build_tool_schemas(spec)
    context = LLMContext(
        messages=[{"role": "system", "content": spec.compile_prompt() + _today_line(spec)}],
        **({"tools": tools} if tools else {}),
    )
    aggregator = LLMContextAggregatorPair(context)

    for t in spec.tools:
        llm.register_function(t.name, _handler(spec, t.name, rec))

    # Live mirror: stream the caller's transcription to the dashboard word-by-word as the call
    # happens (the live view + real-time detection). Best-effort + wrapped — if anything about the
    # tap fails, we fall back to the plain pipeline so the call itself is never at risk.
    orch = os.getenv("ORCH_BASE_URL", "http://localhost:8000")
    session = os.getenv("OTTO_SESSION", "") or await _active_session(orch)  # auto: the deployed business
    tap = None
    if session:
        asyncio.create_task(_live_post(orch, session, {"kind": "call_start", "call_id": "live",
                                                       "scenario": f"live call · {spec.business.name}"}))
        try:
            tap = _LiveTap(session, orch)
        except Exception:
            tap = None
    stages = [transport.input(), stt] + ([tap] if tap else []) + \
             [aggregator.user(), llm, tts, transport.output(), aggregator.assistant()]
    pipeline = Pipeline(stages)
    # Telephony (Twilio Media Streams) is genuinely 8kHz; WebRTC/Daily are wideband. NVIDIA's
    # Parakeet ASR requires 16kHz and the STT service forwards raw transport audio un-resampled,
    # so an 8kHz pipeline silently breaks transcription. Pick the rate from the transport.
    is_telephony = "websocket" in type(transport).__module__.lower()  # FastAPIWebsocketTransport = Twilio
    rate = 8000 if is_telephony else 16000
    task = PipelineTask(pipeline, params=PipelineParams(
        audio_in_sample_rate=rate, audio_out_sample_rate=rate,
        enable_metrics=True, enable_usage_metrics=True, allow_interruptions=True,
    ))
    await PipelineRunner().run(task)

    # Production loop: ship the finished transcript to the orchestrator's /api/observe,
    # which audits it and, on a failure, fires a targeted swarm-heal. (Loop #2.)
    await _report_to_orchestrator(context, rec)


async def _run_bot_s2s(transport: BaseTransport, spec: AgentSpec) -> None:
    """AWS Nova Sonic speech-to-speech (sponsor: AWS) — one bidirectional service replaces
    STT + LLM + TTS for lowest latency. Enable with PIPELINE_MODE=s2s + AWS creds."""
    from pipecat.services.aws.nova_sonic import AWSNovaSonicLLMService

    rec = _Recorder()
    llm = AWSNovaSonicLLMService(
        access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region=os.getenv("AWS_REGION", "us-east-1"),
        settings=AWSNovaSonicLLMService.Settings(voice="matthew", system_instruction=spec.compile_prompt() + _today_line(spec)),
    )
    tools = build_tool_schemas(spec)
    context = LLMContext(messages=[], **({"tools": tools} if tools else {}))
    aggregator = LLMContextAggregatorPair(context)
    for t in spec.tools:
        llm.register_function(t.name, _handler(spec, t.name, rec))

    pipeline = Pipeline([transport.input(), aggregator.user(), llm, transport.output(), aggregator.assistant()])
    task = PipelineTask(pipeline, params=PipelineParams(
        audio_in_sample_rate=8000, audio_out_sample_rate=8000, enable_metrics=True, allow_interruptions=True))
    await PipelineRunner().run(task)
    await _report_to_orchestrator(context, rec)


def _handler(spec: AgentSpec, name: str, rec: "_Recorder"):
    async def handler(params):  # FunctionCallParams in current Pipecat
        import tools
        args = getattr(params, "arguments", {}) or {}
        rec.events.append({"kind": "tool_call", "t_ms": rec.ms(), "name": name, "args": args})
        t0 = time.monotonic()
        result = await tools.dispatch(spec, name, args)
        rd = result if isinstance(result, dict) else {}
        ok = not (str(rd.get("status", "")).lower() in _FAIL_STATUS or rd.get("error"))
        rec.events.append({"kind": "tool_result", "t_ms": rec.ms(), "name": name, "ok": ok,
                           "result": rd, "latency_ms": int((time.monotonic() - t0) * 1000)})
        await params.result_callback(result)
    return handler


import re as _re
_FILLER = _re.compile(r"\b(u+h+|u+m+|e+r+m?|e+h+m?|h+m+|mm+|err+)\b|—|\.\.\.|\b\w+—", _re.I)


def _disf(text: str) -> float:
    """Verbatim disfluency density of a caller turn (fillers/elongations/false-starts per ~6 words).
    We KEEP these (Deepgram filler_words=true) because a hesitant 'uhh… ehm…' caller is real signal."""
    words = max(1, len((text or "").split()))
    return round(min(1.0, len(_FILLER.findall(text or "")) / (words / 6 + 1)), 2)


async def _active_session(orch: str) -> str:
    """The currently-deployed business's session id, so the agent's live stream lands where the
    dashboard is watching — no OTTO_SESSION needed. Empty string if nothing is deployed yet."""
    try:
        async with httpx.AsyncClient(timeout=4.0) as c:
            r = await c.get(f"{orch}/api/active-session")
            if r.status_code == 200:
                return (r.json() or {}).get("session_id") or ""
    except Exception:
        pass
    return ""


async def _live_post(orch: str, session: str, ev: dict) -> None:
    """Fire-and-forget one live event to the orchestrator's /api/live (the real-time dashboard
    stream). Best-effort: a slow/absent orchestrator must never affect the call."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            await c.post(f"{orch}/api/live/{session}", json=ev)
    except Exception:
        pass


class _LiveTap(FrameProcessor):
    """Mirror the CALLER's transcription to the dashboard live, word by word. Sits right after STT,
    forwards every frame untouched, and on the side POSTs interim/final transcripts (with confidence
    + verbatim disfluency) to /api/live so the live view streams + fires detection as the caller
    speaks. Defensive by construction — it pushes the frame first and swallows any tap error, so it
    can never block or break the voice pipeline."""

    def __init__(self, session: str, orch: str) -> None:
        super().__init__()
        self._session, self._orch = session, orch

    async def process_frame(self, frame, direction: "FrameDirection") -> None:
        await super().process_frame(frame, direction)
        try:
            if isinstance(frame, InterimTranscriptionFrame) and frame.text:
                self._send({"kind": "hear_partial", "text": frame.text, "asr_conf": _conf(frame)})
            elif isinstance(frame, TranscriptionFrame) and frame.text:
                self._send({"kind": "hear", "text": frame.text, "asr_conf": _conf(frame),
                            "audio": {"disfluency": _disf(frame.text)}})
        except Exception:
            pass
        await self.push_frame(frame, direction)

    def _send(self, ev: dict) -> None:
        if self._session:
            asyncio.create_task(_live_post(self._orch, self._session, ev))


def _conf(frame) -> float:
    """Per-result ASR confidence if the STT frame carries it (Deepgram does), else 1.0."""
    for attr in ("confidence", "stability"):
        v = getattr(frame, attr, None)
        if isinstance(v, (int, float)):
            return float(v)
    return 1.0


async def _report_to_orchestrator(context: LLMContext, rec: "_Recorder") -> None:
    """Ship a structured CallTrace (dialogue + the real tool event stream) to /api/observe,
    which classifies it across the failure taxonomy and, on a failure, fires a targeted
    swarm-heal. The tool events carry real outcomes + latency; dialogue turns are interleaved
    by order (best-effort timing). Loop #2."""
    orch = os.getenv("ORCH_BASE_URL", "http://localhost:8000")
    session = os.getenv("OTTO_SESSION", "") or await _active_session(orch)
    if not session:
        return
    try:
        events: list[CallEvent] = []
        msgs = [m for m in (getattr(context, "messages", []) or [])
                if isinstance(m, dict) and m.get("role") in ("user", "assistant") and m.get("content")]
        for i, m in enumerate(msgs):  # spread dialogue turns across the call timeline
            events.append(CallEvent(kind="say" if m["role"] == "assistant" else "hear",
                                    t_ms=i * 1500, text=str(m["content"])[:500]))
        events += [CallEvent(**e) for e in rec.events]
        events.sort(key=lambda e: e.t_ms)
        trace = CallTrace(call_id=f"{session}-live", events=events)
        async with httpx.AsyncClient(timeout=20.0) as c:
            await c.post(f"{orch}/api/observe/{session}", json={"trace": trace.model_dump()})
    except Exception:
        pass  # never let reporting break a call


# ── spec loading (shared by the entrypoints) ─────────────────────────────────
async def load_spec() -> AgentSpec:
    """Resolve which business this call is for, richest source first:
      1. a pinned OTTO_SESSION (a specific build), else
      2. the orchestrator's currently-active business (what the UI last activated), else
      3. the cached Piccino spec (offline / demo fallback).
    """
    session = os.getenv("OTTO_SESSION", "")
    orch = os.getenv("ORCH_BASE_URL", "http://localhost:8000")
    endpoint = f"/api/spec/{session}" if session else "/api/active-spec"
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(f"{orch}{endpoint}")
            if r.status_code == 200:
                return AgentSpec.model_validate(r.json())
    except Exception:
        pass
    cached = pathlib.Path(__file__).resolve().parents[2] / "packages" / "spec" / "piccino.json"
    return AgentSpec.model_validate_json(cached.read_text())


# ── unified entrypoint via Pipecat's runner ──────────────────────────────────
# ONE bot() for every front door — the architecture's whole point. The runner picks
# the transport and create_transport builds it from this factory dict:
#   webrtc → local browser dev (uv run bot.py → http://localhost:7860)
#   daily  → the Cekura swarm + Pipecat Cloud (room is injected by the runner)
#   twilio → the live phone call (the Twilio serializer is wired automatically)
def _transport_params() -> dict:
    return {
        "webrtc": lambda: TransportParams(
            audio_in_enabled=True, audio_out_enabled=True, vad_analyzer=SileroVADAnalyzer()),
        "daily": lambda: DailyParams(
            audio_in_enabled=True, audio_out_enabled=True, vad_analyzer=SileroVADAnalyzer()),
        "twilio": lambda: FastAPIWebsocketParams(
            audio_in_enabled=True, audio_out_enabled=True, add_wav_header=False,
            vad_analyzer=SileroVADAnalyzer()),  # add_wav_header + serializer set automatically
    }


async def bot(runner_args: RunnerArguments) -> None:
    """Pipecat runner entrypoint — local dev (WebRTC), the Cekura swarm + Pipecat Cloud
    (Daily), and the live phone (Twilio), all from the same pipeline. The swarm tests the
    exact code that answers the phone. Run locally: `uv run bot.py` → http://localhost:7860"""
    transport = await create_transport(runner_args, _transport_params())
    spec = await load_spec()
    await run_bot(transport, spec)


if __name__ == "__main__":
    from pipecat.runner.run import main
    main()
