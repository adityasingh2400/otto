"""Pipecat voice agent: serves an AgentSpec over a transport.

Same pipeline, two front doors:
  - twilio_server.py -> FastAPIWebsocketTransport  (the live phone call)
  - daily_runner.py  -> DailyTransport             (the room Cekura's sim callers join)

That sameness is the architecture: the swarm tests the exact pipeline that answers the
phone. Pipecat/keys are NOT needed to import this module (service imports are lazy).

D2 TODO(team): confirm exact Pipecat import paths against the pinned version and
github.com/pipecat-ai/pipecat-quickstart-phone-bot. Service classes below match the
common Pipecat 0.0.x layout.
"""

from __future__ import annotations

import os

from lineforge_spec import AgentSpec


def build_tool_schemas(spec: AgentSpec) -> list[dict]:
    """OpenAI-style function schemas generated from the spec's tools."""
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
    if os.getenv("STT_PROVIDER") == "nvidia":  # sponsor: NVIDIA Parakeet
        from pipecat.services.riva.stt import RivaSTTService
        return RivaSTTService(api_key=os.getenv("NVIDIA_API_KEY"))
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
                              voice_id=spec.voice.tts_voice or "a0e99841-438c-4a64-b679-ae501e7d6091")


def _llm():
    if os.getenv("LLM_PROVIDER") == "bedrock":  # sponsor: AWS Nova
        from pipecat.services.aws.llm import AWSBedrockLLMService
        return AWSBedrockLLMService(model=os.getenv("BEDROCK_MODEL_ID", "us.amazon.nova-pro-v1:0"))
    from pipecat.services.openai.llm import OpenAILLMService
    return OpenAILLMService(api_key=os.getenv("OPENAI_API_KEY"), model=os.getenv("LLM_MODEL", "gpt-4o"))


async def run_bot(transport, spec: AgentSpec) -> None:
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.runner import PipelineRunner
    from pipecat.pipeline.task import PipelineParams, PipelineTask
    from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext

    import tools  # local module

    stt, tts, llm = _stt(), _tts(spec), _llm()
    context = OpenAILLMContext(
        messages=[{"role": "system", "content": spec.compile_prompt()}],
        tools=build_tool_schemas(spec),
    )
    aggregator = llm.create_context_aggregator(context)

    for t in spec.tools:  # route LLM tool calls to our mock handlers
        llm.register_function(t.name, _handler(spec, t.name))

    pipeline = Pipeline([
        transport.input(), stt, aggregator.user(), llm, tts, transport.output(), aggregator.assistant(),
    ])
    task = PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True, allow_interruptions=True),
        observers=[_eval_observer(spec)],  # ships finished calls to the production loop
    )
    await PipelineRunner().run(task)


def _handler(spec: AgentSpec, name: str):
    async def handler(params):  # FunctionCallParams in current Pipecat
        import tools
        result = await tools.dispatch(spec, name, getattr(params, "arguments", {}) or {})
        await params.result_callback(result)
    return handler


def _eval_observer(spec: AgentSpec):
    # D2 TODO(team): a real observer that, on call end, POSTs the transcript to the
    # orchestrator's /api/observe (production loop) and/or Cekura observe(). See
    # docs/TECH.md §Pipecat (observers) and §Cekura (observability).
    class _NoopObserver:
        async def on_push_frame(self, *a, **k):
            return None
    return _NoopObserver()
