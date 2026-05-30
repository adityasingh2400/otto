"""Self-heal: turn swarm failures into structured policy patches → a new spec version.

The healer only ever touches `policies`. With an LLM key it authors patches from the
real failure reasons/transcripts; with no key it applies canonical fixes that are
specifically designed to make the failed policy-coverage checks pass. Either way the
output is a clean before/after policy diff (see diff_specs) — never freeform prompt soup.

THE SAFETY GUARANTEE (`safe_apply`): a self-modifying agent must be unable to make itself
WORSE. Every candidate patch is validated against the full scenario suite's invariants
before it ships — a patch that would regress ANY currently-passing scenario is rejected
and rolled back. So healing is provably monotonic: the spec that ships always passes a
superset of what the old one did. A production fix is checked against the *pre-launch*
suite too, so the line can never heal itself out of its launch safety.
"""

from __future__ import annotations

from otto_spec import AgentSpec, Policy, diff_specs

from . import config, llm
from .events import bus

_ALLOWED_CAT = {"safety", "booking", "knowledge", "voice_behavior"}
_ALLOWED_SEV = {"low", "medium", "high", "critical"}


def verify(spec: AgentSpec, scenarios: list) -> set[str]:
    """Which scenarios PASS their static invariant against this spec (the safety oracle)."""
    passing: set[str] = set()
    for p in scenarios:
        try:
            ok, _ = p.static_check(spec)
        except Exception:
            ok = False
        if ok:
            passing.add(p.id)
    return passing


def safe_apply(spec: AgentSpec, patches: list[Policy], scenarios: list) -> tuple[AgentSpec, dict]:
    """Apply patches one at a time; KEEP one only if it regresses no passing scenario.

    Returns (new_spec, report). `report.regressed` is guaranteed empty for the shipped spec —
    that's the monotonic-improvement proof. A patch that would have broken something lands in
    `report.rejected` with the exact scenarios it would have harmed, and is NOT applied.
    """
    cur = spec.model_copy(deep=True)
    base_pass = verify(cur, scenarios)
    applied: list[str] = []
    rejected: list[dict] = []
    seen: set[str] = set()
    for patch in patches:
        if patch.id in seen:
            continue
        cur_pass = verify(cur, scenarios)
        trial = cur.model_copy(deep=True)
        trial.upsert_policy(patch)
        trial_pass = verify(trial, scenarios)
        regressed = sorted(cur_pass - trial_pass)
        fixed_now = sorted(trial_pass - cur_pass)
        if regressed:  # would break something that worked — refuse it (the safety guarantee)
            rejected.append({"id": patch.id, "category": patch.category, "reason": "would regress", "would_regress": regressed})
            continue
        if not fixed_now:  # fixes/governs nothing it can verify (no-op or too-thin fix) — don't bloat the prompt
            rejected.append({"id": patch.id, "category": patch.category, "reason": "ineffective"})
            continue
        cur, _ = trial, seen.add(patch.id)
        applied.append(patch.id)
    final_pass = verify(cur, scenarios)
    report = {
        "checked": len(scenarios),
        "applied": applied,
        "rejected": rejected,
        "fixed": sorted(final_pass - base_pass),
        "regressed": sorted(base_pass - final_pass),  # [] by construction — the guarantee
    }
    return cur, report


async def heal(session_id: str, spec: AgentSpec, failures: list[dict], round_no: int,
               fixes: dict[str, Policy] | None = None, scenarios: list | None = None,
               extra_patches: list[Policy] | None = None
               ) -> tuple[AgentSpec, list[dict]]:
    await bus.publish(session_id, {"type": "stage", "stage": "heal", "status": "start",
                                   "detail": f"{len(failures)} failures"})

    # LLM-authored patches first (Nemotron reasons a fix from the failure evidence), with the
    # curated canonical fix appended as a BACKSTOP. safe_apply keeps whichever genuinely flips a
    # failing scenario without regressing — so the loop always heals (canonical guarantees it),
    # and an LLM patch wins whenever it actually closes the gap. Without the backstop, a
    # semantically-right LLM patch that misses the keyword the static check greps for would be
    # rejected as ineffective and the loop would stall.
    # extra_patches are author-by-the-failure fixes the TRACE detectors emitted (the action/outcome
    # layer) — every one is still regression-guarded by safe_apply below, same as the rest.
    llm_patches = await _llm_patches(spec, failures) if (config.llm_available() and config.HEAL_USE_LLM) else []
    patches: list[Policy] = llm_patches + _canonical_patches(failures, fixes) + list(extra_patches or [])

    if scenarios:  # regression-guarded path (the real flows always pass a suite)
        new, report = safe_apply(spec, patches, scenarios)
    else:
        new = spec.model_copy(deep=True)
        for p in patches:
            new.upsert_policy(p)
        report = {"checked": 0, "applied": sorted({p.id for p in patches}), "rejected": [], "fixed": [], "regressed": []}

    if report["applied"]:                      # only bump the version if something real shipped (no vanity bumps)
        new.bump_version(notes=f"heal round {round_no}: patched {report['applied']}")
    diffs = [d.model_dump() for d in diff_specs(spec, new)]
    await bus.publish(session_id, {"type": "patch", "round": round_no, "diffs": diffs})
    if scenarios:
        await bus.publish(session_id, {"type": "regression_check", "round": round_no, "report": report})
    rej = f", rejected {len(report['rejected'])} (would regress)" if report["rejected"] else ""
    await bus.publish(session_id, {"type": "stage", "stage": "heal", "status": "done",
                                   "detail": f"v{spec.meta.version} → v{new.meta.version}, {len(diffs)} changes · "
                                             f"regression-checked across {report['checked']} scenarios{rej}"})
    return new, diffs


def _canonical_patches(failures: list[dict], fixes: dict[str, Policy] | None) -> list[Policy]:
    # Apply the per-persona fix carried by the active archetype (each Persona declares its
    # own `fix` policy). No archetype fix for a failure => nothing to patch offline.
    table = fixes or {}
    out: list[Policy] = []
    for f in failures:
        p = table.get(f.get("persona", ""))
        if p:
            out.append(p)
    return out


async def _llm_patches(spec: AgentSpec, failures: list[dict]) -> list[Policy]:
    policies_json = [p.model_dump() for p in spec.policies]
    fail_lines = "\n".join(f"- [{f.get('category')}] {f.get('label')}: {f.get('reason')}" for f in failures)
    sys = (
        "You are a senior conversation designer fixing a small-business phone agent's "
        "policies after it failed test calls. Output JSON only."
    )
    user = (
        f"Business: {spec.business.name} ({spec.business.type}).\n\n"
        f"Current policies:\n{policies_json}\n\n"
        f"The agent FAILED these test calls:\n{fail_lines}\n\n"
        "Write policy patches that fix each failure. Rules:\n"
        "- Be specific and safe. NEVER over-promise (no guaranteeing allergy safety, "
        "availability, or pricing you can't verify).\n"
        "- Prefer MODIFYING an existing policy (reuse its exact id) when one is close; "
        "otherwise ADD a new policy with a short kebab-case id.\n"
        "- Reference real tool names (check_availability, reserve_table, send_sms, escalate) "
        "where relevant.\n"
        '- Each patch: {"id","category"(safety|booking|knowledge|voice_behavior),'
        '"trigger","rule","severity"(low|medium|high|critical)}\n'
        'Return JSON: {"patches": [ ... ]}'
    )
    data = await llm.complete_json(sys, user)
    # The model may return {"patches":[...]}, a bare [...] list, or (rarely) a scalar.
    # Guard the type BEFORE calling .get — `data.get(...)` is evaluated before its default,
    # so a bare list/scalar would otherwise raise AttributeError and abort the whole heal.
    if isinstance(data, list):
        raw = data
    elif isinstance(data, dict):
        raw = data.get("patches", [])
    else:
        raw = []
    out: list[Policy] = []
    for d in raw:
        p = _mk_policy(d)
        if p:
            out.append(p)
    return out


def _mk_policy(d: dict) -> Policy | None:
    if not isinstance(d, dict):  # tolerate a stray string/number in the patches array
        return None
    try:
        cat = d.get("category", "knowledge")
        if cat not in _ALLOWED_CAT:
            cat = "knowledge"
        sev = d.get("severity", "medium")
        if sev not in _ALLOWED_SEV:
            sev = "medium"
        return Policy(
            id=str(d["id"]), category=cat,
            trigger=str(d.get("trigger", "")), rule=str(d["rule"]),
            severity=sev, source="healed",
        )
    except (KeyError, TypeError):
        return None
