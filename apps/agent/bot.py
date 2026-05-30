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

import os
import pathlib
import time

import httpx
from otto_spec import AgentSpec, CallEvent, CallTrace

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
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import parse_telephony_websocket
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.transports.base_transport import BaseTransport
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport


# ── tool schemas from the spec ───────────────────────────────────────────────
def build_tool_schemas(spec: AgentSpec) -> list[dict]:
    out = []
    for t in spec.tools:
        props, required = {}, []
        for p in t.params:
            props[p.name] = {"type": _json_type(p.type), "description": p.description}
            if p.required:
                required.append(p.name)
        out.append({"type": "function", "function": {
            "name": t.name, "description": t.description,
            "parameters": {"type": "object", "properties": props, "required": required}}})
    return out


def _json_type(t: str) -> str:
    return {"integer": "integer", "number": "number", "boolean": "boolean"}.get(t, "string")


# ── provider selection (reliability default + sponsor swaps via .env) ────────
def _stt():
    if os.getenv("STT_PROVIDER") == "nvidia":  # sponsor: NVIDIA Parakeet via NIM (the Nemotron voice blueprint STT)
        # The module family moved riva.* -> nvidia.* across pipecat versions; try the current
        # path, fall back to the older one, so a fresh install can't ImportError on the day.
        try:
            from pipecat.services.nvidia.stt import NvidiaSTTService as _STT
        except ImportError:
            from pipecat.services.riva.stt import RivaSTTService as _STT  # older module family
        return _STT(api_key=os.getenv("NVIDIA_API_KEY"))
    from pipecat.services.deepgram.stt import DeepgramSTTService
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


# ── the pipeline ─────────────────────────────────────────────────────────────
async def run_bot(transport: BaseTransport, spec: AgentSpec) -> None:
    if os.getenv("PIPELINE_MODE") == "s2s":
        return await _run_bot_s2s(transport, spec)
    rec = _Recorder()  # per-call state — concurrency-safe across simultaneous calls
    stt, tts, llm = _stt(), _tts(spec), _llm()
    context = OpenAILLMContext(
        messages=[{"role": "system", "content": spec.compile_prompt()}],
        tools=build_tool_schemas(spec),
    )
    aggregator = llm.create_context_aggregator(context)

    for t in spec.tools:
        llm.register_function(t.name, _handler(spec, t.name, rec))

    pipeline = Pipeline([
        transport.input(), stt, aggregator.user(), llm, tts, transport.output(), aggregator.assistant(),
    ])
    task = PipelineTask(pipeline, params=PipelineParams(
        audio_in_sample_rate=8000, audio_out_sample_rate=8000,  # Twilio Media Streams = 8kHz
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
        settings=AWSNovaSonicLLMService.Settings(voice="matthew", system_instruction=spec.compile_prompt()),
    )
    context = OpenAILLMContext(messages=[], tools=build_tool_schemas(spec))
    aggregator = llm.create_context_aggregator(context)
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


async def _report_to_orchestrator(context: OpenAILLMContext, rec: "_Recorder") -> None:
    """Ship a structured CallTrace (dialogue + the real tool event stream) to /api/observe,
    which classifies it across the failure taxonomy and, on a failure, fires a targeted
    swarm-heal. The tool events carry real outcomes + latency; dialogue turns are interleaved
    by order (best-effort timing). Loop #2."""
    session = os.getenv("OTTO_SESSION", "")
    orch = os.getenv("ORCH_BASE_URL", "http://localhost:8000")
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
    session = os.getenv("OTTO_SESSION", "")
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


# ── Twilio entrypoint via Pipecat's runner ───────────────────────────────────
async def bot(runner_args: RunnerArguments) -> None:
    """Called by `pipecat.runner` per inbound call. Run: uv run bot.py --transport twilio --proxy <host>"""
    transport_type, call_data = await parse_telephony_websocket(runner_args.websocket)
    serializer = TwilioFrameSerializer(
        stream_sid=call_data["stream_id"],
        call_sid=call_data["call_id"],
        account_sid=os.getenv("TWILIO_ACCOUNT_SID"),
        auth_token=os.getenv("TWILIO_AUTH_TOKEN"),
    )
    transport = FastAPIWebsocketTransport(
        websocket=runner_args.websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True, audio_out_enabled=True, add_wav_header=False,
            vad_analyzer=SileroVADAnalyzer(), serializer=serializer,
        ),
    )
    spec = await load_spec()
    await run_bot(transport, spec)


if __name__ == "__main__":
    from pipecat.runner.run import main
    main()
