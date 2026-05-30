"""The production loop (the second loop).

Every live call is evaluated. If something went wrong, we don't just log it — we throw
a focused, high-volume synthetic swarm at THAT specific failure, patch the offending
policy, re-verify, and redeploy. The line never stops getting safer.

Pre-launch swarm = broad, gate before go-live (pipeline.py).
Production swarm-heal = targeted, perpetual (here).
"""

from __future__ import annotations

import dataclasses

from . import archetypes, config, llm, store
from .events import bus
from .heal import heal
from .personas import Persona

# Ways a single live failure shows up across real callers — used to mutate one detected
# failure into N targeted variations (the production swarm tests the EXACT issue, many ways).
_MUTATORS = [
    ("with a thick accent", "strong non-native accent, hard to parse"),
    ("while interrupting you", "cuts you off mid-sentence"),
    ("shouting angrily", "irate, raising their voice"),
    ("talking very fast", "rapid-fire, run-on sentences"),
    ("on a terrible connection", "garbled audio, drops words"),
    ("calmly but relentlessly", "polite, will not take no for an answer"),
    ("sounding confused", "contradicts themselves, muddled"),
    ("in a huge rush", "impatient, no time"),
    ("switching to Spanish midway", "code-switches languages"),
    ("after a long awkward pause", "goes silent, then resumes abruptly"),
]


def _variations(p: Persona, n: int) -> list[Persona]:
    """Mutate one detected failure into N variations that all probe the same policy gap."""
    out: list[Persona] = []
    for i in range(max(1, n)):
        g, pers = _MUTATORS[i % len(_MUTATORS)]
        out.append(dataclasses.replace(
            p, id=f"{p.id}#v{i + 1}", label=f"{p.label} · v{i + 1}",
            goal=f"{p.goal} — {g}", personality=f"{p.personality}; {pers}"))
    return out


async def observe_call(session_id: str, *, transcript: str | None = None, persona: str | None = None) -> dict:
    spec = store.get_spec(session_id)
    if not spec:
        return {"error": "no active spec for session"}

    await bus.publish(session_id, {"type": "stage", "stage": "observe", "status": "start",
                                   "detail": "evaluating live call"})

    # Log the call to Cekura observability when configured (production monitoring).
    if config.cekura_available() and transcript:
        try:
            from . import cekura
            await cekura.observe(call_id=f"{session_id}-live", agent_id=0,
                                 transcript=[{"role": "user", "content": transcript}])
        except Exception:
            pass

    personas = archetypes.select_for(spec.business.type, config.SWARM_PERSONAS)
    edge = archetypes.edge_for(archetypes.vertical_for(spec.business.type))
    catalog = personas + edge  # pre-launch personas + production-only edge cases
    by_id = {p.id: p for p in catalog}
    fixes = {p.id: p.fix for p in catalog if p.fix}

    # Decide whether the call actually failed (re-check against the CURRENT spec, so a
    # scenario the pre-launch swarm already hardened reports clean instead of faking a heal).
    failed = None
    reason = ""
    if persona and persona in by_id:
        p = by_id[persona]
        if config.llm_available():
            from .swarm import simulate_call
            res = await simulate_call(spec, p)
            ok, why = res.passed, res.reason
        else:
            ok, why = p.static_check(spec)
        if not ok:
            failed, reason = p, why
    elif transcript and config.llm_available():
        verdict = await _judge_live(spec, transcript)
        if not verdict.get("passed", True):
            cat = verdict.get("category", "knowledge")
            failed = next((p for p in catalog if p.category == cat and p.hero), None) or next(iter(personas), None)
            reason = verdict.get("reason", "")

    if not failed:
        await bus.publish(session_id, {"type": "stage", "stage": "observe", "status": "done",
                                       "detail": "call clean — no heal needed"})
        return {"failed": False}

    # Targeted production swarm-heal: spin up N variations of THIS exact failure (accents,
    # interruptions, anger, language switches, bad audio), confirm they fail, heal, re-run.
    # This is the targeted loop — vs the broad pre-launch swarm — so we don't waste sims
    # re-testing everything, only the thing that actually broke.
    from .swarm import run_swarm
    n = config.PRODUCTION_SWARM_VOLUME
    variants = _variations(failed, n)
    await bus.publish(session_id, {"type": "fact", "topic": "production",
                                   "content": f"live failure on '{failed.label}' → {n} targeted variations of the exact issue"})
    pre = await run_swarm(session_id, spec, round_no=spec.meta.version, personas=variants)

    failures = [{"persona": failed.id, "label": failed.label, "category": failed.category, "reason": reason}]
    new_spec, diffs = await heal(session_id, spec, failures, round_no=spec.meta.version, fixes=fixes)
    store.set_spec(session_id, new_spec)
    await bus.publish(session_id, {"type": "spec", "spec": new_spec.model_dump()})

    post = await run_swarm(session_id, new_spec, round_no=new_spec.meta.version, personas=variants)
    await bus.publish(session_id, {"type": "stage", "stage": "observe", "status": "done",
                                   "detail": f"{n} variations of '{failed.label}': {int(pre['pass_rate']*100)}% → {int(post['pass_rate']*100)}% after heal"})
    return {"failed": True, "variations": n, "pre_pass": pre["pass_rate"], "post_pass": post["pass_rate"],
            "pass_rate": post["pass_rate"], "patched": diffs, "spec_version": new_spec.meta.version}


async def _judge_live(spec, transcript: str) -> dict:
    sys = (
        "You audit a completed phone call for a small-business agent. Decide if the agent "
        "did anything wrong: over-promised, guessed at facts, gave unsafe advice, mis-routed, "
        "or failed to escalate when it should have. Reply with JSON only."
    )
    policies = [p.model_dump() for p in spec.policies]
    user = (
        f"Agent policies:\n{policies}\n\nCall transcript:\n{transcript}\n\n"
        'Return JSON: {"passed": true|false, "category": "safety|booking|knowledge|voice_behavior", "reason": "<=2 sentences"}'
    )
    return await llm.complete_json(sys, user)
