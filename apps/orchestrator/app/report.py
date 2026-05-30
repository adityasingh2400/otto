"""Evaluation report — a shareable artifact built entirely from a session's REAL run data.

Nothing here is invented. Every number is read back from the event history (swarm reports,
patches) and the spec lineage in the store. This is the "move beyond vibes" deliverable:
a legible record of what was tested, what failed, what the agent patched in itself, and the
safety gate it cleared — the takeaway a judge can keep.

  GET /api/report/{session_id}  ->  this dict
  /report/?s=<session_id>       ->  the printable page that renders it
"""

from __future__ import annotations

from . import config, store
from .events import bus

_CATS = ("safety", "booking", "knowledge", "voice_behavior")


def _category_breakdown(results: list[dict]) -> dict[str, dict]:
    by: dict[str, dict] = {}
    for r in results:
        c = r.get("category", "other")
        d = by.setdefault(c, {"passed": 0, "total": 0})
        d["total"] += 1
        d["passed"] += 1 if r.get("passed") else 0
    return by


def build_report(session_id: str) -> dict:
    events = bus.history(session_id)
    spec = store.get_spec(session_id)
    active = store.get_active(session_id)
    versions = store.history(session_id)

    # Split rounds at activation: everything before the line goes live is "certification"
    # (the gate story); everything after is "production" (the line kept getting safer).
    activated_idx = next((i for i, e in enumerate(events) if e.get("type") == "activated"), None)

    rounds: list[dict] = []
    for i, e in enumerate(events):
        if e.get("type") != "swarm_report":
            continue
        r = e["report"]
        results = r.get("results", [])
        rounds.append({
            "round": r.get("round"),
            "spec_version": r.get("spec_version"),
            "pass_rate": r.get("pass_rate"),
            "passed": r.get("passed"),
            "total": r.get("total"),
            "by_category": _category_breakdown(results),
            "phase": "production" if (activated_idx is not None and i > activated_idx) else "certification",
            "results": [
                {"persona": x.get("persona"), "label": x.get("label"), "category": x.get("category"),
                 "passed": x.get("passed"), "reason": x.get("reason"), "backend": x.get("backend")}
                for x in results
            ],
        })

    cert = [r for r in rounds if r["phase"] == "certification"]
    prod = [r for r in rounds if r["phase"] == "production"]

    # Per-scenario fail→pass journey across certification rounds (first verdict vs final verdict).
    first_round = cert[0] if cert else None
    final_round = cert[-1] if cert else None
    journey: list[dict] = []
    if first_round and final_round:
        first_by = {x["persona"]: x for x in first_round["results"]}
        for x in final_round["results"]:
            f = first_by.get(x["persona"])
            journey.append({
                "label": x["label"], "category": x["category"], "backend": x.get("backend"),
                "first_passed": (f or x).get("passed"), "final_passed": x.get("passed"),
                "reason": x.get("reason"),
            })

    # Patches grouped by round (the self-authored policy diffs — the auto-improve evidence).
    patches = [{"round": e.get("round"), "diffs": e.get("diffs", [])}
               for e in events if e.get("type") == "patch"]

    # Production failure classifications (the multi-dimensional taxonomy — what live calls hit).
    failure_reports = [{"call_id": e.get("call_id"), "summary": e.get("summary", {}),
                        "failures": e.get("failures", [])}
                       for e in events if e.get("type") == "failure_report"]
    taxonomy_dims: dict[str, int] = {}
    for fr in failure_reports:
        for f in fr["failures"]:
            taxonomy_dims[f.get("dimension", "?")] = taxonomy_dims.get(f.get("dimension", "?"), 0) + 1

    # The safety guarantee, summed across every heal this session: regression-guarded, monotonic.
    regression_checks = [e.get("report", {}) for e in events if e.get("type") == "regression_check"]
    discovery_events = [e for e in events if e.get("type") == "discovery"]
    guarantee = {
        "heals_guarded": len(regression_checks),
        "scenarios_checked": max((r.get("checked", 0) for r in regression_checks), default=0),
        "total_fixed": sum(len(r.get("fixed", [])) for r in regression_checks),
        "total_regressed": sum(len(r.get("regressed", [])) for r in regression_checks),
        "patches_rejected": sum(len(r.get("rejected", [])) for r in regression_checks),
        "discovered_modes": (discovery_events[-1].get("total_modes", 0) if discovery_events else 0),
    }

    # Version lineage with notes (v1 -> vN, what each round recorded).
    lineage = [{"version": s.meta.version, "parent_version": s.meta.parent_version,
                "notes": s.meta.notes, "policy_count": len(s.policies)} for s in versions]

    backend = next((x.get("backend") for r in rounds for x in r["results"]), "static")

    return {
        "session_id": session_id,
        "business": spec.business.model_dump() if spec else None,
        "voice": spec.voice.model_dump() if spec else None,
        "gate": config.PASS_GATE,
        "passed_gate": bool(final_round and final_round["pass_rate"] >= config.PASS_GATE),
        "first_pass_rate": first_round["pass_rate"] if first_round else None,
        "final_pass_rate": final_round["pass_rate"] if final_round else None,
        "final_version": spec.meta.version if spec else None,
        "active": active,
        "backend": backend,
        "swarm_mode": config.SWARM_MODE,
        "certification_rounds": cert,
        "production_rounds": prod,
        "journey": journey,
        "patches": patches,
        "failure_reports": failure_reports,
        "taxonomy_dimensions": taxonomy_dims,
        "guarantee": guarantee,
        "lineage": lineage,
        "policies": [p.model_dump() for p in spec.policies] if spec else [],
        "total_scenarios_run": sum(r["total"] for r in rounds),
        "categories": list(_CATS),
        "extraction": _extraction_confidence(spec),
    }


def _extraction_confidence(spec) -> dict:
    """How much Otto actually learned from the site — so a JS-only/thin site that healed to 100%
    is reported honestly ('read limited info') instead of masquerading as a rich agent. A green
    pass rate certifies SAFETY (the agent won't over-promise); this certifies COVERAGE."""
    if not spec:
        return {"confidence": "low", "knowledge": 0, "generic_type": True, "reason": "no spec"}
    kn = len(spec.knowledge)
    generic = spec.business.type.strip().lower() in ("", "business")
    if kn >= 5 and not generic:
        conf = "high"
    elif kn >= 2 or spec.tools:
        conf = "medium"
    else:
        conf = "low"
    reason = f"{kn} fact(s), {len(spec.tools)} tool(s)" + (", unclassified business type" if generic else "")
    return {"confidence": conf, "knowledge": kn, "tools": len(spec.tools), "generic_type": generic, "reason": reason}
