"""Self-heal: turn swarm failures into structured policy patches → a new spec version.

The healer only ever touches `policies`. With an LLM key it authors patches from the
real failure reasons/transcripts; with no key it applies canonical fixes that are
specifically designed to make the failed policy-coverage checks pass. Either way the
output is a clean before/after policy diff (see diff_specs) — never freeform prompt soup.
"""

from __future__ import annotations

from otto_spec import AgentSpec, Policy, diff_specs

from . import config, llm
from .events import bus

_ALLOWED_CAT = {"safety", "booking", "knowledge", "voice_behavior"}
_ALLOWED_SEV = {"low", "medium", "high", "critical"}


# Canonical offline fixes, keyed by the persona that failed. Same id as a v1 policy
# => a MODIFY (clean diff); a new id => an ADD. large-party-routing intentionally
# satisfies both the large_party and private_event checks.
def _canonical() -> dict[str, Policy]:
    return {
        "severe_allergy": Policy(
            id="allergy-accommodate", category="safety",
            trigger="a caller mentions a food allergy",
            rule="never guarantee zero cross-contamination; acknowledge the allergy, offer to add a note to the order for the kitchen, and offer to connect the caller with staff who can advise.",
            severity="critical", source="healed"),
        "large_party": Policy(
            id="large-party-routing", category="booking",
            trigger="a caller wants to book for 6 or more guests",
            rule="for parties of 6-15 use the large-party flow and confirm by text; for 16+ or a private event, collect event details and escalate to staff. Do not book large parties through the normal reservation tool.",
            severity="high", source="healed"),
        "private_event": Policy(
            id="large-party-routing", category="booking",
            trigger="a caller wants to book for 6 or more guests",
            rule="for parties of 6-15 use the large-party flow and confirm by text; for 16+ or a private event (e.g. whole-restaurant buyout), collect event details and escalate to staff. Do not book large parties through the normal reservation tool.",
            severity="high", source="healed"),
        "guess_availability": Policy(
            id="availability-answer", category="knowledge",
            trigger="a caller asks whether a date or time is available",
            rule="always call check_availability before answering; never guess. If you cannot check, offer to take their details and have staff confirm.",
            severity="high", source="healed"),
        "sms_confirmation": Policy(
            id="sms-confirm", category="booking",
            trigger="a reservation is successfully booked",
            rule="always call send_sms to text a confirmation to the caller's number.",
            severity="medium", source="healed"),
    }


async def heal(session_id: str, spec: AgentSpec, failures: list[dict], round_no: int,
               fixes: dict[str, Policy] | None = None) -> tuple[AgentSpec, list[dict]]:
    await bus.publish(session_id, {"type": "stage", "stage": "heal", "status": "start",
                                   "detail": f"{len(failures)} failures"})
    new = spec.model_copy(deep=True)

    patches: list[Policy] = []
    if config.llm_available():
        patches = await _llm_patches(spec, failures)
    if not patches:
        patches = _canonical_patches(failures, fixes)

    applied_ids: set[str] = set()
    for p in patches:
        if p.id in applied_ids:
            continue
        new.upsert_policy(p)
        applied_ids.add(p.id)

    new.bump_version(notes=f"heal round {round_no}: patched {sorted(applied_ids)}")
    diffs = [d.model_dump() for d in diff_specs(spec, new)]
    await bus.publish(session_id, {"type": "patch", "round": round_no, "diffs": diffs})
    await bus.publish(session_id, {"type": "stage", "stage": "heal", "status": "done",
                                   "detail": f"v{spec.meta.version} → v{new.meta.version}, {len(diffs)} policy changes"})
    return new, diffs


def _canonical_patches(failures: list[dict], fixes: dict[str, Policy] | None) -> list[Policy]:
    # Prefer the per-persona fixes carried by the active archetype; fall back to the
    # built-in restaurant table for legacy callers.
    table = fixes if fixes else _canonical()
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
    raw = data.get("patches", data if isinstance(data, list) else [])
    out: list[Policy] = []
    for d in raw:
        p = _mk_policy(d)
        if p:
            out.append(p)
    return out


def _mk_policy(d: dict) -> Policy | None:
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
