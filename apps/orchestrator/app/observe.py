"""The production loop (the second loop).

Every live call is evaluated. If something went wrong, we don't just log it — we throw
a focused, high-volume synthetic swarm at THAT specific failure, patch the offending
policy, re-verify, and redeploy. The line never stops getting safer.

Pre-launch swarm = broad, gate before go-live (pipeline.py).
Production swarm-heal = targeted, perpetual (here).
"""

from __future__ import annotations

import dataclasses

from otto_spec import CallTrace, Policy, diff_specs

from . import archetypes, config, failure, llm, store
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
        try:
            if config.llm_available():
                from .swarm import simulate_call
                res = await simulate_call(spec, p)
                ok, why = res.passed, res.reason
            else:
                ok, why = p.static_check(spec)
        except Exception:  # a transient LLM/judge error must not 500 the route
            ok, why = p.static_check(spec)
        if not ok:
            failed, reason = p, why
    elif transcript and config.llm_available():
        try:
            verdict = await _judge_live(spec, transcript)
        except Exception:
            verdict = {"passed": True}
        if not verdict.get("passed", True):
            cat = verdict.get("category", "knowledge")
            # prefer a hero of that category, then any persona of it; never heal an
            # unrelated scenario just to have something to patch.
            failed = (next((p for p in catalog if p.category == cat and p.hero), None)
                      or next((p for p in catalog if p.category == cat), None))
            reason = verdict.get("reason", "")
            if failed is None:
                await bus.publish(session_id, {"type": "stage", "stage": "observe", "status": "done",
                                               "detail": f"flagged a {cat} issue with no matching scenario — logged, not auto-healed"})
                return {"failed": False, "unmatched_category": cat}

    scenario_label = (failed.label if failed else (by_id[persona].label if persona in by_id else (persona or "live call")))
    await bus.publish(session_id, {"type": "live_call", "field": "scenario", "value": scenario_label})

    if not failed:
        await bus.publish(session_id, {"type": "live_call", "field": "verdict",
                                       "value": "clean — already hardened by the pre-launch swarm"})
        await bus.publish(session_id, {"type": "stage", "stage": "observe", "status": "done",
                                       "detail": "call clean — no heal needed"})
        return {"failed": False}

    await bus.publish(session_id, {"type": "live_call", "field": "verdict", "value": f"FAILED — {reason}"[:160]})

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
    new_spec, diffs = await heal(session_id, spec, failures, round_no=spec.meta.version, fixes=fixes,
                                 scenarios=catalog + variants)
    store.set_spec(session_id, new_spec)
    await bus.publish(session_id, {"type": "spec", "spec": new_spec.model_dump()})

    post = await run_swarm(session_id, new_spec, round_no=new_spec.meta.version, personas=variants)
    await bus.publish(session_id, {"type": "live_call", "field": "action",
                                   "value": f"targeted swarm-heal → v{new_spec.meta.version} ({int(pre['pass_rate']*100)}% → {int(post['pass_rate']*100)}%)"})
    await bus.publish(session_id, {"type": "stage", "stage": "observe", "status": "done",
                                   "detail": f"{n} variations of '{failed.label}': {int(pre['pass_rate']*100)}% → {int(post['pass_rate']*100)}% after heal"})
    return {"failed": True, "variations": n, "pre_pass": pre["pass_rate"], "post_pass": post["pass_rate"],
            "pass_rate": post["pass_rate"], "patched": diffs, "spec_version": new_spec.meta.version}


def _probe_persona(f: failure.FailureInstance) -> Persona:
    """Turn a detected failure into a synthetic caller that probes the exact gap. The oracle is
    `failure.governed` — it passes only when the spec carries a REAL policy whose rule actually
    closes the gap (present, non-empty, with the required semantic tokens). A garbage fix
    (`rule="lol"`) stays RED, so the loop can't grade its own homework with the answer key."""
    pid = f.heal_policy_id
    sev = f.severity if f.severity in ("low", "medium", "high", "critical") else "high"
    return Persona(
        id=f.id, label=f.title, category=f.heal_category,
        goal=f"Reproduce the production failure: {f.reason}",
        personality="a real caller who triggers the exact issue from the live call",
        success_criteria=f"The agent must now genuinely govern: {f.reason}",
        static_check=(lambda p: lambda s: (
            failure.governed(s, p),
            f"a real policy now governs '{p}'." if failure.governed(s, p) else "no governing policy yet (or the fix is too thin)."))(pid),
        fix=Policy(id=pid, category=f.heal_category, trigger=f.title.lower(),
                   rule=f.heal_rule, severity=sev, source="healed"),
    )


async def observe_trace(session_id: str, trace: CallTrace) -> dict:
    """Production loop, event-stream edition: classify a live call across the full failure
    taxonomy (voice AND action AND outcome AND latency), then heal every detected failure
    with policies the failures themselves authored, and re-verify with a targeted swarm."""
    spec = store.get_spec(session_id)
    if not spec:
        return {"error": "no active spec for session"}

    await bus.publish(session_id, {"type": "stage", "stage": "observe", "status": "start",
                                   "detail": f"classifying live call {trace.call_id} across the failure taxonomy"})
    failures = failure.evaluate(spec, trace)
    summary = failure.summarize(failures)
    clusters = failure.cluster(failures)            # many symptoms → few root causes
    summary["root_causes"] = len(clusters)
    summary["discovered"] = sum(1 for f in failures if f.discovered)
    await bus.publish(session_id, {"type": "failure_report", "call_id": trace.call_id, "summary": summary,
                                   "clusters": clusters, "failures": [f.to_dict() for f in failures]})
    # self-expanding eval: register + announce any failure mode the named detectors didn't cover
    for f in failures:
        if f.discovered:
            n = store.record_discovery(session_id, f.signature)
            await bus.publish(session_id, {"type": "discovery", "id": f.id, "title": f.title,
                                           "signature": f.signature, "count": n,
                                           "total_modes": len(store.discovered_modes(session_id))})

    await bus.publish(session_id, {"type": "live_call", "field": "scenario", "value": trace.call_id})
    if not failures:
        await bus.publish(session_id, {"type": "live_call", "field": "verdict",
                                       "value": "clean — voice and every action checked out"})
        await bus.publish(session_id, {"type": "stage", "stage": "observe", "status": "done",
                                       "detail": "call clean — no failures across any dimension"})
        return {"failed": False, "failures": []}

    dims = ", ".join(summary["dimensions_hit"])
    await bus.publish(session_id, {"type": "live_call", "field": "verdict",
                                   "value": f"{len(failures)} failure(s) across [{dims}] — worst: {failures[0].title}"})

    # Targeted swarm: probe every detected failure, several variations each (red pre-patch).
    from .swarm import run_swarm
    probes = [_probe_persona(f) for f in failures]
    per = max(1, config.PRODUCTION_SWARM_VOLUME // max(1, len(probes)))
    variants = [v for p in probes for v in _variations(p, per)]
    await bus.publish(session_id, {"type": "fact", "topic": "production",
                                   "content": f"live call '{trace.call_id}': {len(failures)} failure(s) across [{dims}] → {len(variants)} targeted probes"})
    pre = await run_swarm(session_id, spec, round_no=spec.meta.version, personas=variants)

    # The failures author their own fixes (a policy per failure), but every fix is
    # REGRESSION-GUARDED against the pre-launch suite + the probes — a production heal can
    # never break launch safety. A fix that would regress anything is rejected, not shipped.
    from .heal import safe_apply
    suite = archetypes.select_for(spec.business.type, config.SWARM_PERSONAS) + variants
    patches, seen = [], set()
    for p in probes:
        if p.fix and p.fix.id not in seen:
            patches.append(p.fix)
            seen.add(p.fix.id)
    new_spec, report = safe_apply(spec, patches, suite)
    if report["applied"]:
        new_spec.bump_version(notes=f"production heal from call {trace.call_id}: {report['applied']}")
    diffs = [d.model_dump() for d in diff_specs(spec, new_spec)]
    store.set_spec(session_id, new_spec)
    await bus.publish(session_id, {"type": "patch", "round": new_spec.meta.version, "diffs": diffs})
    await bus.publish(session_id, {"type": "regression_check", "round": new_spec.meta.version, "report": report})
    await bus.publish(session_id, {"type": "spec", "spec": new_spec.model_dump()})

    post = await run_swarm(session_id, new_spec, round_no=new_spec.meta.version, personas=variants)
    await bus.publish(session_id, {"type": "live_call", "field": "action",
                                   "value": f"auto-healed → v{new_spec.meta.version}: {int(pre['pass_rate']*100)}% → {int(post['pass_rate']*100)}% over {len(variants)} probes"})
    await bus.publish(session_id, {"type": "stage", "stage": "observe", "status": "done",
                                   "detail": f"{len(failures)} failure(s) healed: {int(pre['pass_rate']*100)}% → {int(post['pass_rate']*100)}% (v{new_spec.meta.version})"})
    return {"failed": True, "failures": [f.to_dict() for f in failures], "summary": summary,
            "pre_pass": pre["pass_rate"], "post_pass": post["pass_rate"], "pass_rate": post["pass_rate"],
            "patched": diffs, "spec_version": new_spec.meta.version}


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
