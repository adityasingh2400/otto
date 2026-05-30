"""Pipecat voice agent — serves an AgentSpec over a transport.

Same pipeline, two front doors (the architecture's whole point — the swarm tests the
exact code that answers the phone):
  - Twilio (live call): `uv run bot.py --transport twilio --proxy <ngrok-host>`
    (Pipecat's runner provides the FastAPI server + TwiML + serializer)
  - Daily (Cekura swarm): apps/agent/daily_runner.py

Imports/pattern verified against the current Pipecat (pipecat-examples / quickstart).
Service imports are top-level here because the agent venv installs pipecat; the
orchestrator never imports this module.
"""

from __future__ import annotations

import os
import pathlib

import httpx
from lineforge_spec import AgentSpec

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
    if os.getenv("STT_PROVIDER") == "nvidia":  # sponsor: NVIDIA Parakeet via NIM
        # current path (>=0.0.96); pre-0.0.96 it was pipecat.services.riva.stt.RivaSTTService
        from pipecat.services.nvidia.stt import NvidiaSTTService
        return NvidiaSTTService(api_key=os.getenv("NVIDIA_API_KEY"))
    from pipecat.services.deepgram.stt import DeepgramSTTService
    return DeepgramSTTService(api_key=os.getenv("DEEPGRAM_API_KEY"))


def _tts(spec: AgentSpec):
    p = os.getenv("TTS_PROVIDER", "cartesia")
    if p == "nvidia":  # sponsor: NVIDIA Magpie
        from pipecat.services.riva.tts import RivaTTSService
        return RivaTTSService(api_key=os.getenv("NVIDIA_API_KEY"))
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
    from pipecat.services.openai.llm import OpenAILLMService
    return OpenAILLMService(api_key=os.getenv("OPENAI_API_KEY"), model=os.getenv("LLM_MODEL", "gpt-4o"))


# ── the pipeline ─────────────────────────────────────────────────────────────
async def run_bot(transport: BaseTransport, spec: AgentSpec) -> None:
    if os.getenv("PIPELINE_MODE") == "s2s":
        return await _run_bot_s2s(transport, spec)
    stt, tts, llm = _stt(), _tts(spec), _llm()
    context = OpenAILLMContext(
        messages=[{"role": "system", "content": spec.compile_prompt()}],
        tools=build_tool_schemas(spec),
    )
    aggregator = llm.create_context_aggregator(context)

    import tools  # local
    for t in spec.tools:
        llm.register_function(t.name, _handler(spec, t.name))

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
    await _report_to_orchestrator(context)


async def _run_bot_s2s(transport: BaseTransport, spec: AgentSpec) -> None:
    """AWS Nova Sonic speech-to-speech (sponsor: AWS) — one bidirectional service replaces
    STT + LLM + TTS for lowest latency. Enable with PIPELINE_MODE=s2s + AWS creds."""
    from pipecat.services.aws.nova_sonic import AWSNovaSonicLLMService

    llm = AWSNovaSonicLLMService(
        access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region=os.getenv("AWS_REGION", "us-east-1"),
        settings=AWSNovaSonicLLMService.Settings(voice="matthew", system_instruction=spec.compile_prompt()),
    )
    context = OpenAILLMContext(messages=[], tools=build_tool_schemas(spec))
    aggregator = llm.create_context_aggregator(context)
    for t in spec.tools:
        llm.register_function(t.name, _handler(spec, t.name))

    pipeline = Pipeline([transport.input(), aggregator.user(), llm, transport.output(), aggregator.assistant()])
    task = PipelineTask(pipeline, params=PipelineParams(
        audio_in_sample_rate=8000, audio_out_sample_rate=8000, enable_metrics=True, allow_interruptions=True))
    await PipelineRunner().run(task)
    await _report_to_orchestrator(context)


def _handler(spec: AgentSpec, name: str):
    async def handler(params):  # FunctionCallParams in current Pipecat
        import tools
        result = await tools.dispatch(spec, name, getattr(params, "arguments", {}) or {})
        await params.result_callback(result)
    return handler


async def _report_to_orchestrator(context: OpenAILLMContext) -> None:
    session = os.getenv("LINEFORGE_SESSION", "")
    orch = os.getenv("ORCH_BASE_URL", "http://localhost:8000")
    if not session:
        return
    try:
        msgs = getattr(context, "messages", []) or []
        transcript = "\n".join(
            f"{m.get('role')}: {m.get('content')}" for m in msgs
            if isinstance(m, dict) and m.get("role") in ("user", "assistant") and m.get("content")
        )
        async with httpx.AsyncClient(timeout=20.0) as c:
            await c.post(f"{orch}/api/observe/{session}", json={"transcript": transcript})
    except Exception:
        pass  # never let reporting break a call


# ── spec loading (shared by the entrypoints) ─────────────────────────────────
async def load_spec() -> AgentSpec:
    session = os.getenv("LINEFORGE_SESSION", "")
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
