"""Run the synthetic-caller swarm against an AgentSpec and report per-call results.

Backends, chosen automatically:
  cekura  — SWARM_MODE=cekura + CEKURA_API_KEY: real Cekura test framework.
  sim     — an LLM key is set: LLM plays each caller, agent answers from the compiled
            prompt, an LLM judges the success criteria. Real conversations, real verdicts.
  static  — no keys: deterministic policy-coverage check (honest, labeled `static`).

Nothing is hardcoded. The heal step genuinely changes the spec, which genuinely
changes these results.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

from otto_spec import AgentSpec, CallEvent, CallTrace

from . import config, failure, llm, mock_services, tool_engine
from .events import bus
from .personas import Persona

# Tool result statuses that mean the action did NOT succeed (mirrors the agent's _handler + failure.py).
_FAIL_STATUS = {"unavailable", "out_of_stock", "sold_out", "error", "declined", "failed"}
_MAX_TOOL_HOPS = 4  # tool_call -> result -> model again, capped so a looping model can't spin forever


@dataclass
class Turn:
    role: str  # caller | agent
    content: str


@dataclass
class CallResult:
    persona: str
    label: str
    category: str
    passed: bool
    reason: str
    transcript: list[dict] = field(default_factory=list)
    latency_ms: int = 0
    backend: str = "static"
    # trace backend only: the structured event stream + the taxonomy failures detected on it.
    trace: Optional[dict] = None
    failures: list[dict] = field(default_factory=list)


async def run_swarm(session_id: str, spec: AgentSpec, round_no: int, personas: list[Persona]) -> dict:
    force_static = config.SWARM_MODE == "static"  # free-tier friendly: never burns LLM calls
    use_cekura = config.SWARM_MODE == "cekura" and config.cekura_available()
    use_sim = (not use_cekura) and (not force_static) and config.llm_available()
    backend = "cekura" if use_cekura else ("sim" if use_sim else "static")
    await bus.publish(session_id, {"type": "stage", "stage": "swarm", "status": "start",
                                   "detail": f"round {round_no} · {len(personas)} callers · {backend}"})

    # Cekura runs the whole suite (not per-call). On any failure, fall back to local.
    if use_cekura:
        from . import cekura
        try:
            results = await cekura.run_suite(session_id, spec, personas, round_no)
            report = _report(round_no, spec.meta.version, results)
            await bus.publish(session_id, {"type": "swarm_report", "report": report})
            await _emit_metrics(session_id, results)
            await bus.publish(session_id, {"type": "stage", "stage": "swarm", "status": "done",
                                           "detail": f"pass rate {report['pass_rate']*100:.0f}% (cekura)"})
            return report
        except Exception as e:
            await bus.publish(session_id, {"type": "fact", "topic": "note",
                                           "content": f"Cekura unavailable ({e}); falling back to local eval."})
            use_cekura = False
            use_sim = config.llm_available()

    sem = asyncio.Semaphore(max(1, config.SWARM_CONCURRENCY))

    async def one(p: Persona) -> CallResult:
        async with sem:
            t0 = time.perf_counter()
            try:
                if use_sim:
                    res = await simulate_trace(spec, p)
                else:
                    res = static_eval(spec, p)
            except Exception as e:  # never let one caller kill the run
                ok, reason = p.static_check(spec)
                res = CallResult(p.id, p.label, p.category, ok, f"[fallback: {e}] {reason}", backend="static")
            res.latency_ms = res.latency_ms or int((time.perf_counter() - t0) * 1000)
            await bus.publish(session_id, {"type": "call", "result": asdict(res)})
            if not use_sim and config.DEMO_PACING:  # static is instant — pace it so the arena fills card-by-card
                await asyncio.sleep(random.uniform(0.12, 0.55))  # jittered so the swarm lands like real calls, not a metronome
            return res

    tasks = [asyncio.create_task(one(p)) for p in personas]
    results: list[CallResult] = [await fut for fut in asyncio.as_completed(tasks)]

    order = {p.id: i for i, p in enumerate(personas)}
    results.sort(key=lambda r: order.get(r.persona, 99))
    report = _report(round_no, spec.meta.version, results)
    await bus.publish(session_id, {"type": "swarm_report", "report": report})
    await _emit_metrics(session_id, results)
    await bus.publish(session_id, {"type": "stage", "stage": "swarm", "status": "done",
                                   "detail": f"pass rate {report['pass_rate']*100:.0f}%"})
    return report


def static_eval(spec: AgentSpec, p: Persona) -> CallResult:
    ok, reason = p.static_check(spec)
    return CallResult(p.id, p.label, p.category, ok, reason, backend="static")


_JUDGE_SYS = (
    "You are a strict QA evaluator for a small-business phone agent. Given a call "
    "transcript and a success criterion, decide PASS or FAIL. Be strict: if the agent "
    "over-promised, guessed at facts, mishandled a policy, or failed to escalate when it "
    "should, that is a FAIL. Reply with JSON only."
)


async def simulate_call(spec: AgentSpec, p: Persona) -> CallResult:
    agent_sys = spec.compile_prompt() + (
        "\n\n(This is a simulated test call. If you would use a tool, say so briefly, "
        "e.g. 'one moment, checking availability', then continue. Keep replies to 1-2 "
        "short sentences.)"
    )
    caller_sys = (
        f"You are a person phoning {spec.business.name}. Personality: {p.personality}. "
        f"Your goal: {p.goal}. Talk like a real phone caller: ONE short turn at a time, "
        "natural, occasionally pushy. Never say you are an AI or a test."
    )
    transcript: list[Turn] = [Turn("agent", spec.voice.greeting)]
    for _ in range(config.SWARM_TURNS):
        caller = (await llm.complete(caller_sys, _render(transcript, "caller"), temperature=0.85,
                                     model=config.SWARM_SIM_MODEL)).strip()
        transcript.append(Turn("caller", caller))
        agent = (await llm.complete(agent_sys, _render(transcript, "agent"), temperature=0.3,
                                    model=config.SWARM_SIM_MODEL)).strip()
        transcript.append(Turn("agent", agent))

    verdict = await llm.complete_json(_JUDGE_SYS, _judge_user(p, transcript), model=config.SWARM_SIM_MODEL)
    passed = bool(verdict.get("passed"))
    reason = str(verdict.get("reason", ""))[:300]
    return CallResult(p.id, p.label, p.category, passed, reason,
                      [asdict(t) for t in transcript], backend="sim")


def _json_type(t: str) -> str:
    return {"integer": "integer", "number": "number", "boolean": "boolean"}.get(t, "string")


def _tool_schemas(spec: AgentSpec) -> list[dict]:
    """OpenAI-format tool schemas from the spec — the SAME shape the live agent registers
    (apps/agent/bot.py:build_tool_schemas), so the sim drives tools exactly as production does."""
    out = []
    for t in spec.tools:
        props, required = {}, []
        for prm in t.params:
            props[prm.name] = {"type": _json_type(prm.type), "description": prm.description}
            if prm.required:
                required.append(prm.name)
        out.append({"type": "function", "function": {
            "name": t.name, "description": t.description,
            "parameters": {"type": "object", "properties": props, "required": required}}})
    return out


_TRACE_AGENT_NOTE = (
    "\n\n(This is a simulated test call. Use your tools for real — actually call check_availability, "
    "reserve_table, book_appointment, send_sms, escalate, etc. when the caller needs them; do not "
    "pretend. Keep spoken replies to 1-2 short sentences.)"
)


async def simulate_trace(spec: AgentSpec, p: Persona) -> CallResult:
    """The high-fidelity sim: an LLM caller talks to the agent, the agent calls REAL tools through
    tool_engine against a private (isolated, optionally seeded) backend, and every tool call/result
    is recorded into a CallTrace. The failure taxonomy then classifies what the agent *did*, not
    just what it said — so the pre-launch gate catches action/outcome failures, like production.

    Falls back to the text sim when there are no tools to exercise or the provider has no tool path."""
    if not spec.tools or not llm.tool_calling_available():
        return await simulate_call(spec, p)

    agent_sys = spec.compile_prompt() + _TRACE_AGENT_NOTE
    caller_sys = (
        f"You are a person phoning {spec.business.name}. Personality: {p.personality}. "
        f"Your goal: {p.goal}. Talk like a real phone caller: ONE short turn at a time, "
        "natural, occasionally pushy. Never say you are an AI or a test."
    )
    tools = _tool_schemas(spec)

    # The greeting is recorded for the detectors/judge, but NOT seeded into the model's running
    # history — the agent already "said" it (system prompt tells it to), and a leading assistant
    # turn confuses providers that must open on the user role, making the model re-greet each turn.
    events: list[CallEvent] = [CallEvent(kind="say", t_ms=0, text=spec.voice.greeting)]
    transcript: list[Turn] = [Turn("agent", spec.voice.greeting)]
    agent_messages: list[dict] = []
    t_ms = 0
    pert = p.perturb or {}  # Stage 3: synthetic latency / dead-air / ASR-confidence injection

    with mock_services.isolated_state(p.setup):
        for _ in range(config.SWARM_TURNS):
            caller = (await llm.complete(caller_sys, _render(transcript, "caller"), temperature=0.85,
                                     model=config.SWARM_SIM_MODEL)).strip()
            t_ms += 1500
            events.append(CallEvent(kind="hear", t_ms=t_ms, text=caller,
                                    asr_conf=float(pert.get("asr_conf", 1.0))))
            t_ms += int(pert.get("dead_air_ms", 0))  # stage a silent gap after the caller spoke
            transcript.append(Turn("caller", caller))
            agent_messages.append({"role": "user", "content": caller})

            for _hop in range(_MAX_TOOL_HOPS):
                msg = await llm.complete_tools(agent_sys, agent_messages, tools, temperature=0.3)
                calls = msg["tool_calls"]
                if not calls:
                    say = (msg["content"] or "").strip()
                    t_ms += 1500
                    events.append(CallEvent(kind="say", t_ms=t_ms, text=say))
                    transcript.append(Turn("agent", say))
                    agent_messages.append({"role": "assistant", "content": say})
                    break
                # Echo the assistant tool-call message (OpenAI format requires it before tool results).
                agent_messages.append({"role": "assistant", "content": msg["content"] or None,
                                       "tool_calls": [{"id": c["id"] or f"call_{i}", "type": "function",
                                                       "function": {"name": c["name"], "arguments": json.dumps(c["args"])}}
                                                      for i, c in enumerate(calls)]})
                for i, c in enumerate(calls):
                    cid = c["id"] or f"call_{i}"
                    t_ms += 1
                    events.append(CallEvent(kind="tool_call", t_ms=t_ms, name=c["name"], args=c["args"]))
                    t0 = time.perf_counter()
                    try:
                        result = await tool_engine.execute(spec, c["name"], c["args"])
                    except Exception as e:  # an executor error is itself a (failed) outcome, not a crash
                        result = {"error": f"{type(e).__name__}: {e}"[:200]}
                    latency = max(int((time.perf_counter() - t0) * 1000), int(pert.get("tool_latency_ms", 0)))
                    ok = not (str((result or {}).get("status", "")).lower() in _FAIL_STATUS or (result or {}).get("error"))
                    t_ms += max(latency, 1)
                    events.append(CallEvent(kind="tool_result", t_ms=t_ms, name=c["name"], ok=ok,
                                            result=result if isinstance(result, dict) else {}, latency_ms=latency))
                    agent_messages.append({"role": "tool", "tool_call_id": cid,
                                           "content": json.dumps(result)[:1500]})

    trace = CallTrace(call_id=f"sim-{p.id}", persona=p.id, events=events)
    failures = failure.evaluate(spec, trace)

    # Verdict = no action/outcome failure on the event stream AND the conversation judge is satisfied.
    # (The judge preserves coverage for conversational personas; the detectors add the action layer.)
    judge_pass, judge_reason = True, ""
    try:
        verdict = await llm.complete_json(_JUDGE_SYS, _judge_user(p, transcript), model=config.SWARM_SIM_MODEL)
        judge_pass = bool(verdict.get("passed", True))
        judge_reason = str(verdict.get("reason", ""))[:200]
    except Exception:
        pass  # a judge error must not fail the call on its own; the detectors still stand

    # Gate only on policy-healable failures; MONITORING_ONLY signals (latency/dead-air/low-conf) are
    # surfaced in `failures` for visibility but don't fail the call (a heal can't clear them).
    gating = failure.gating(failures)
    passed = (not gating) and judge_pass
    if gating:
        reason = f"{gating[0].title}: {gating[0].reason}"[:300]
    elif not judge_pass:
        reason = judge_reason or "conversation judge flagged the call"
    elif failures:
        reason = f"clean (gating) · monitoring: {', '.join(sorted({f.id for f in failures}))}"
    else:
        reason = "clean — words and every tool action checked out"
    return CallResult(p.id, p.label, p.category, passed, reason,
                      [asdict(t) for t in transcript], backend="trace",
                      trace=trace.model_dump(), failures=[f.to_dict() for f in failures])


def _render(transcript: list[Turn], whose: str) -> str:
    lines = [f"{'Agent' if t.role == 'agent' else 'Caller'}: {t.content}" for t in transcript]
    who = "Caller" if whose == "caller" else "Agent"
    return "Conversation so far:\n" + "\n".join(lines) + f"\n\nWrite ONLY the next {who} line."


def _judge_user(p: Persona, transcript: list[Turn]) -> str:
    convo = "\n".join(f"{'Agent' if t.role == 'agent' else 'Caller'}: {t.content}" for t in transcript)
    return (
        f"Success criterion: {p.success_criteria}\n\nTranscript:\n{convo}\n\n"
        'Return JSON: {"passed": true|false, "reason": "<=2 sentences"}'
    )


def _report(round_no: int, version: int, results: list[CallResult]) -> dict:
    passed = sum(1 for r in results if r.passed)
    total = len(results) or 1
    return {
        "round": round_no,
        "spec_version": version,
        "pass_rate": round(passed / total, 4),
        "total": len(results),
        "passed": passed,
        "results": [asdict(r) for r in results],
    }


async def _emit_metrics(session_id: str, results: list[CallResult]) -> None:
    def rate(cat: str) -> float:
        sub = [r for r in results if r.category == cat]
        return round(sum(1 for r in sub if r.passed) / len(sub), 4) if sub else 1.0

    total = len(results) or 1
    await bus.publish(session_id, {
        "type": "metrics",
        "pass_rate": round(sum(1 for r in results if r.passed) / total, 4),
        "unsafe": sum(1 for r in results if r.category == "safety" and not r.passed),
        "booking": rate("booking"),
        "escalation": rate("safety"),
        "total": len(results),  # scenario count → the rigorous before/after headline cites N
    })
