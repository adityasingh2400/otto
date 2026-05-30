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
import time
from dataclasses import asdict, dataclass, field

from lineforge_spec import AgentSpec

from . import config, llm
from .events import bus
from .personas import Persona


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


async def run_swarm(session_id: str, spec: AgentSpec, round_no: int, personas: list[Persona]) -> dict:
    use_cekura = config.SWARM_MODE == "cekura" and config.cekura_available()
    use_sim = (not use_cekura) and config.llm_available()
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

    sem = asyncio.Semaphore(6)

    async def one(p: Persona) -> CallResult:
        async with sem:
            t0 = time.perf_counter()
            try:
                if use_sim:
                    res = await simulate_call(spec, p)
                else:
                    res = static_eval(spec, p)
            except Exception as e:  # never let one caller kill the run
                ok, reason = p.static_check(spec)
                res = CallResult(p.id, p.label, p.category, ok, f"[fallback: {e}] {reason}", backend="static")
            res.latency_ms = res.latency_ms or int((time.perf_counter() - t0) * 1000)
            await bus.publish(session_id, {"type": "call", "result": asdict(res)})
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
    for _ in range(4):
        caller = (await llm.complete(caller_sys, _render(transcript, "caller"), temperature=0.85)).strip()
        transcript.append(Turn("caller", caller))
        agent = (await llm.complete(agent_sys, _render(transcript, "agent"), temperature=0.3)).strip()
        transcript.append(Turn("agent", agent))

    verdict = await llm.complete_json(_JUDGE_SYS, _judge_user(p, transcript))
    passed = bool(verdict.get("passed"))
    reason = str(verdict.get("reason", ""))[:300]
    return CallResult(p.id, p.label, p.category, passed, reason,
                      [asdict(t) for t in transcript], backend="sim")


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
    })
