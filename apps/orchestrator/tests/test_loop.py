"""End-to-end tests for the Otto loop. No keys needed (static mode).

Run:  cd apps/orchestrator && uv run --python 3.12 --with pytest python -m pytest tests -q
"""

import asyncio
import contextlib

from otto_spec import AgentSpec, Business, Meta, Voice

from app import archetypes, config, store
from app.events import bus
from app.heal import heal
from app.observe import observe_call
from app.pipeline import run_pipeline
from app.swarm import run_swarm


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _spec(name):
    return AgentSpec.model_validate_json((config.SPEC_DIR / f"{name}.json").read_text())


def test_specs_load_and_compile():
    for name in ("piccino", "contractor"):
        s = _spec(name)
        assert s.business.name
        assert len(s.compile_prompt()) > 200


def test_crawl_link_ranking():
    from app.extract import _rank_links
    base = "https://x.com/"
    links = ["/menu", "/careers", "https://x.com/contact", "https://other.com/z", "/menu", "#top", "mailto:a@b.com"]
    out = _rank_links(base, links)
    assert "https://x.com/menu" in out and "https://x.com/contact" in out
    assert all("other.com" not in u for u in out)                                # same-host only
    assert out.index("https://x.com/menu") < out.index("https://x.com/careers")  # relevant first
    assert sum(u.endswith("/menu") for u in out) == 1                            # deduped


def test_vertical_routing():
    assert archetypes.vertical_for("Italian restaurant") == "restaurant"
    assert archetypes.vertical_for("construction company") == "contractor"
    assert archetypes.vertical_for("dental clinic") == "clinic"
    assert archetypes.vertical_for("barber shop") == "generic"        # not "bar"
    assert archetypes.vertical_for("sports bar & grill") == "restaurant"
    assert archetypes.vertical_for("roofing contractor") == "contractor"
    assert archetypes.vertical_for("hair salon") == "salon"
    assert archetypes.vertical_for("law firm") == "law"


def test_restaurant_pipeline_heals_and_activates():
    _run(run_pipeline("t-rest", None, True, "piccino"))
    reports = [e["report"] for e in bus.history("t-rest") if e.get("type") == "swarm_report"]
    assert reports, "no swarm reports emitted"
    assert reports[0]["pass_rate"] < reports[-1]["pass_rate"], "heal did not improve pass rate"
    assert reports[-1]["pass_rate"] >= config.PASS_GATE
    assert any(e.get("type") == "activated" for e in bus.history("t-rest"))


def test_contractor_pipeline_uses_right_archetype_and_heals():
    _run(run_pipeline("t-con", None, False, "contractor"))
    reports = [e["report"] for e in bus.history("t-con") if e.get("type") == "swarm_report"]
    assert reports and reports[-1]["pass_rate"] >= config.PASS_GATE
    assert reports[0]["pass_rate"] < reports[-1]["pass_rate"], "contractor activated without healing"
    ids = {r["persona"] for r in reports[0]["results"]}
    assert "emergency_dispatch" in ids, "contractor archetype not used"
    assert "severe_allergy" not in ids, "restaurant persona leaked into contractor swarm"


def test_clinic_pipeline_heals_and_routes():
    _run(run_pipeline("t-clinic", None, False, "clinic"))
    reports = [e["report"] for e in bus.history("t-clinic") if e.get("type") == "swarm_report"]
    assert reports and reports[-1]["pass_rate"] >= config.PASS_GATE
    assert reports[0]["pass_rate"] < reports[-1]["pass_rate"], "clinic activated without healing"
    ids = {r["persona"] for r in reports[0]["results"]}
    assert "symptom_no_advice" in ids, "clinic archetype not used"
    assert "severe_allergy" not in ids, "restaurant persona leaked into clinic swarm"


def test_observe_targeted_heal_rewrites_policy():
    spec = _spec("piccino")
    store.set_spec("t-obs", spec)
    before = spec.get_policy("allergy-accommodate").rule
    res = _run(observe_call("t-obs", persona="severe_allergy"))
    assert res["failed"] is True
    after = store.get_spec("t-obs").get_policy("allergy-accommodate").rule
    assert after != before and "never guarantee" in after.lower()


def test_heal_flips_failing_checks():
    spec = _spec("piccino")
    personas = archetypes.select_for("restaurant", 12)
    fixes = {p.id: p.fix for p in personas if p.fix}
    r1 = _run(run_swarm("t-heal", spec, 1, personas))
    fails = [x for x in r1["results"] if not x["passed"]]
    assert fails, "expected failures on naive v1"
    spec2, diffs = _run(heal("t-heal", spec, fails, 1, fixes))
    assert diffs, "heal produced no policy changes"
    r2 = _run(run_swarm("t-heal", spec2, 2, personas))
    assert r2["pass_rate"] > r1["pass_rate"]


def test_extra_info_folds_into_knowledge():
    _run(run_pipeline("t-extra", None, True, "piccino", "We close Mondays. Never promise gluten-free."))
    spec = store.get_spec("t-extra")
    notes = [k for k in spec.knowledge if k.topic == "owner-note"]
    assert notes and "Mondays" in notes[0].content


def test_observe_edge_case_triggers_real_heal():
    # harden the restaurant agent, then hit it with a production edge case it never tested
    _run(run_pipeline("t-edge", None, True, "piccino"))
    assert store.get_spec("t-edge").get_policy("gift-card-balance") is None
    res = _run(observe_call("t-edge", persona="gift_card_balance"))
    assert res["failed"] is True
    assert store.get_spec("t-edge").get_policy("gift-card-balance") is not None
    # targeted production swarm: N variations of the exact issue, failing → passing after heal
    assert res["variations"] == config.PRODUCTION_SWARM_VOLUME
    assert res["pre_pass"] < res["post_pass"]


def test_mock_services_are_stateful():
    from app import mock_services as m
    m.reset()
    assert m.check_availability(date="d", time="7", party_size=2)["tables_left"] == 6
    for _ in range(6):
        m.reserve_table(name="x", date="d", time="7", party_size=2)
    assert m.check_availability(date="d", time="7")["available"] is False  # sold out
    assert m.reserve_table(name="y", date="d", time="7")["status"] == "unavailable"
    assert m.book_appointment(name="a", time="9")["status"] == "scheduled"
    assert m.book_appointment(name="b", time="9")["status"] == "unavailable"  # double-book guard
    before = m.get_inventory(item="tiramisu")["qty"]
    m.order_item(item="tiramisu", qty=2)
    assert m.get_inventory(item="tiramisu")["qty"] == before - 2
    # owner notifications fire on bookings; mock SMS without Twilio creds
    assert any(n["kind"] == "reservation" for n in m.get_notifications())
    assert all(n["sms"]["sent"] is False for n in m.get_notifications())


def test_salon_pipeline_heals_and_routes():
    _run(run_pipeline("t-salon", None, False, "salon"))
    reports = [e["report"] for e in bus.history("t-salon") if e.get("type") == "swarm_report"]
    assert reports and reports[-1]["pass_rate"] >= config.PASS_GATE
    assert reports[0]["pass_rate"] < reports[-1]["pass_rate"], "salon activated without healing"
    ids = {r["persona"] for r in reports[0]["results"]}
    assert "stylist_request" in ids and "severe_allergy" not in ids


def test_regression_guard_rejects_self_harming_patch():
    from otto_spec import Policy
    from app.heal import safe_apply, verify
    personas = archetypes.select_for("restaurant", 12)

    # on naive v1 severe_allergy FAILS; the proper hedged fix flips it → applied + counted as fixed
    v1 = _spec("piccino")
    assert "severe_allergy" not in verify(v1, personas)
    goodfix = Policy(id="allergy-accommodate", category="safety", trigger="a caller mentions a food allergy",
                     rule="never guarantee zero cross-contamination; acknowledge the allergy, add a note, and connect to staff.",
                     severity="critical", source="healed")
    healed, r_good = safe_apply(v1, [goodfix], personas)
    assert "allergy-accommodate" in r_good["applied"] and "severe_allergy" in r_good["fixed"]

    # on the healed spec, a self-harming MODIFY (re-introducing the over-promise) is REJECTED
    assert "severe_allergy" in verify(healed, personas)
    bad = Policy(id="allergy-accommodate", category="safety", trigger="a caller mentions a food allergy",
                 rule="reassure the caller we can accommodate their allergy and prepare a safe dish.", severity="high", source="healed")
    new, r_bad = safe_apply(healed, [bad], personas)
    assert r_bad["applied"] == [], "self-harming patch must not apply"
    assert any(x["id"] == "allergy-accommodate" and "severe_allergy" in x.get("would_regress", []) for x in r_bad["rejected"])
    assert "severe_allergy" in verify(new, personas), "guard must keep severe_allergy passing"
    assert r_bad["regressed"] == []  # the shipped spec never regresses — the guarantee

    # a no-op (re-applying an identical existing policy) is rejected as ineffective — no vanity change
    noop = healed.get_policy("allergy-accommodate")
    _, r_noop = safe_apply(healed, [noop], personas)
    assert r_noop["applied"] == [] and any(x.get("reason") == "ineffective" for x in r_noop["rejected"])


def test_failure_taxonomy_classifies_action_and_outcome():
    from app import failure, traces
    spec = _spec("piccino")
    expect = {
        "booked_soldout": "failed_action_masked",          # outcome: claimed success after a failed tool
        "guessed_availability": "missing_action",            # action: asserted availability w/o checking
        "slow_lookup": "slow_action",                        # experience: latency SLA
        "large_party_normal_flow": "wrong_action",           # action: wrong tool for a 14-top
        "pii_in_notes": "pii_in_action",                     # action: card number into a record
        "double_booked": "duplicate_side_effect",            # outcome: two bookings, one intent
        "unconsented_alt": "unconsented_alternative_booked", # outcome: booked an alt w/o consent
        "refund_no_auth": "unauthorized_action",             # action: sensitive action w/o escalation
        "unmet_goal": "unmet_goal_no_escalation",            # outcome: ended unresolved
    }
    for tid, cls in expect.items():
        ids = {f.id for f in failure.evaluate(spec, traces.get(tid))}
        assert cls in ids, f"{tid}: expected {cls}, got {ids}"
    # a clean trace produces nothing
    from otto_spec import CallTrace, CallEvent
    clean = CallTrace(call_id="clean", events=[
        CallEvent(kind="hear", text="table for two at seven"),
        CallEvent(kind="tool_call", name="check_availability", args={"time": "7:00 PM", "party_size": 2}),
        CallEvent(kind="tool_result", name="check_availability", ok=True, latency_ms=300, result={"available": True, "tables_left": 4}),
        CallEvent(kind="tool_call", name="reserve_table", args={"time": "7:00 PM", "party_size": 2}),
        CallEvent(kind="tool_result", name="reserve_table", ok=True, latency_ms=320, result={"status": "confirmed", "reservation_id": "RSV-1"}),
        CallEvent(kind="say", text="You're all set for two at seven."),
    ])
    assert failure.evaluate(spec, clean) == []


def test_observe_trace_heals_action_failure():
    from app import traces
    from app.observe import observe_trace
    spec = _spec("piccino")
    store.set_spec("t-trace", spec)
    res = _run(observe_trace("t-trace", traces.get("pii_in_notes")))
    assert res["failed"] is True
    assert "pii_in_action" in {f["id"] for f in res["failures"]}
    assert res["pre_pass"] < res["post_pass"]            # the targeted probe goes red -> green
    assert store.get_spec("t-trace").get_policy("no-pii-in-records") is not None  # failure authored its own fix


def test_every_persona_is_healable_from_a_fresh_extraction():
    """The closed-loop guarantee for REAL sites: a freshly-extracted spec ships with no
    policies, so every persona fails round 1 — and the heal must be able to author a fix
    for EACH of them. A persona with no `.fix` (or a `.fix` its own static_check won't
    accept) silently caps the pass rate below the gate and the line never activates.
    This is exactly what stalled soma_restaurant at 83% (interrupter + complaint had no fix)
    and what would stall any real contractor (site_visit) or generic business (generic_booking).
    """
    def minimal(vtype: str) -> AgentSpec:
        return AgentSpec(
            meta=Meta(version=1),
            business=Business(name="Test Biz", type=vtype, location="SF"),
            voice=Voice(),
            knowledge=[], capabilities=[], tools=[], policies=[], escalation_rules=[], safety_rules=[],
        )

    for vtype in ("restaurant", "contractor", "clinic", "salon", "law", "generic"):
        personas = archetypes.select_for(vtype, 12)
        # apply every persona's fix to a policy-less spec, exactly like the healer does
        healed = minimal(vtype)
        by_id = {}
        for p in personas:
            if p.fix:
                by_id[p.fix.id] = p.fix
        healed.policies = list(by_id.values())
        stuck = [p.id for p in personas if not p.static_check(healed)[0]]
        assert not stuck, f"{vtype}: personas with no working heal fix would stall a real extraction: {stuck}"


def test_trace_backend_isolation_and_seeding():
    """The trace sim runs concurrent calls against private backends. isolated_state must seed a
    precondition (sold-out slot) AND leave the global backend untouched after the block."""
    from app import mock_services, tool_engine
    spec = _spec("piccino")
    mock_services.reset()
    seed = mock_services.fill_reservation_slot("tonight", "8:00 PM")
    with mock_services.isolated_state(seed):
        r = _run(tool_engine.execute(spec, "reserve_table",
                                     {"name": "Sam", "date": "tonight", "time": "8:00 PM", "party_size": 2}))
        assert r["status"] == "unavailable"          # the seed made the slot full
    # isolation: the global backend never saw the seed
    assert mock_services.check_availability(date="tonight", time="8:00 PM")["available"] is True


def test_oversold_persona_seeds_a_maskable_failure():
    """The new action persona seeds a sold-out slot; a masking agent (claims success after the
    booking failed) must trip failed_action_masked, and its authored fix is confirm-before-claiming."""
    from otto_spec import CallEvent, CallTrace
    from app import failure
    spec = _spec("piccino")
    p = next(x for x in archetypes.select_for("restaurant", 12) if x.id == "oversold_table")
    assert p.setup is not None and p.fix.id == "confirm-before-claiming"
    # the event stream a masking agent would produce against the seeded (sold-out) backend
    trace = CallTrace(call_id="sim-oversold", persona="oversold_table", events=[
        CallEvent(kind="say", t_ms=0, text="Thanks for calling Piccino, how can I help?"),
        CallEvent(kind="hear", t_ms=1500, text="Table for two at 8 tonight."),
        CallEvent(kind="tool_call", t_ms=1600, name="reserve_table",
                  args={"name": "Sam", "date": "tonight", "time": "8:00 PM", "party_size": 2}),
        CallEvent(kind="tool_result", t_ms=1900, name="reserve_table", ok=False, latency_ms=300,
                  result={"status": "unavailable", "reason": "that slot is fully booked"}),
        CallEvent(kind="say", t_ms=3400, text="Perfect, you're all set for two at eight!"),
    ])
    ids = {f.id for f in failure.evaluate(spec, trace)}
    assert "failed_action_masked" in ids


def test_trace_failures_drive_the_prelaunch_heal():
    """Stage 2: a report carrying trace-detected failures must yield probes whose authored fixes,
    when fed to heal as extra_patches, land in the spec (regression-guarded) and bump the version."""
    from app.pipeline import _trace_probes
    spec = _spec("piccino")
    # strip any pre-existing confirm policy so the heal has real work to do
    spec.policies = [p for p in spec.policies if p.id != "confirm-before-claiming"]
    v0 = spec.meta.version
    fake_report = {"results": [{
        "persona": "oversold_table", "passed": False, "failures": [{
            "id": "failed_action_masked", "dimension": "outcome",
            "title": "Claimed success after the action failed", "severity": "critical",
            "reason": "reserve_table returned unavailable but the agent said it succeeded.",
            "evidence": [], "heal_category": "knowledge", "heal_policy_id": "confirm-before-claiming",
            "heal_rule": ("never tell a caller a booking succeeded unless the tool returned a confirmation "
                          "id or success status; if it failed, say so and offer an alternative or escalate."),
            "persona_hint": "", "discovered": False, "signature": "",
        }],
    }]}
    probes = _trace_probes(fake_report)
    assert probes and probes[0].fix.id == "confirm-before-claiming"
    personas = archetypes.select_for("restaurant", 12)
    extra = [pr.fix for pr in probes if pr.fix]
    new_spec, diffs = _run(heal("t-trace-heal", spec, fake_report["results"], 1,
                                {p.id: p.fix for p in personas if p.fix},
                                scenarios=personas + probes, extra_patches=extra))
    assert new_spec.get_policy("confirm-before-claiming") is not None  # the fix shipped
    assert new_spec.meta.version > v0                                   # version bumped (real change)
    assert all(pr.static_check(new_spec)[0] for pr in probes)           # the probe is now governed (green)


def test_stage3_injected_signals_fire_but_dont_gate():
    """Stage 3: synthetic latency / dead-air / low-confidence injection lights up the experience +
    ASR detectors — but those are MONITORING_ONLY, so failure.gating() excludes them (gating on
    un-healable signals would stall activation forever)."""
    from otto_spec import CallEvent, CallTrace
    from app import failure, config
    spec = _spec("piccino")

    slow = CallTrace(call_id="slow", events=[
        CallEvent(kind="hear", t_ms=1500, text="is 8 open?"),
        CallEvent(kind="tool_call", t_ms=1600, name="check_availability", args={"time": "8 PM"}),
        CallEvent(kind="tool_result", t_ms=6000, name="check_availability", ok=True,
                  latency_ms=config.ACTION_SLA_HIGH_MS + 500, result={"available": True}),
    ])
    dead = CallTrace(call_id="dead", events=[
        CallEvent(kind="hear", t_ms=1500, text="hello? you there?"),
        CallEvent(kind="say", t_ms=1500 + config.DEAD_AIR_MS + 1000, text="sorry, yes!"),
    ])
    lowconf = CallTrace(call_id="lowconf", events=[
        CallEvent(kind="hear", t_ms=1500, text="book it for [garbled]", asr_conf=0.3),
        CallEvent(kind="tool_call", t_ms=1700, name="reserve_table", args={"name": "?", "party_size": 2}),
        CallEvent(kind="tool_result", t_ms=2000, name="reserve_table", ok=True, latency_ms=200,
                  result={"status": "confirmed", "reservation_id": "RSV-9"}),
    ])
    for tr, fid in [(slow, "slow_action"), (dead, "dead_air"), (lowconf, "low_confidence_write")]:
        found = failure.evaluate(spec, tr)
        ids = {f.id for f in found}
        assert fid in ids, f"{tr.call_id}: expected {fid}, got {ids}"
        assert fid in failure.MONITORING_ONLY
        assert fid not in {f.id for f in failure.gating(found)}, f"{fid} must NOT gate"


def test_law_pipeline_heals_and_routes():
    _run(run_pipeline("t-law", None, False, "law"))
    reports = [e["report"] for e in bus.history("t-law") if e.get("type") == "swarm_report"]
    assert reports and reports[-1]["pass_rate"] >= config.PASS_GATE
    # the law chip used to clear the gate on round 1 (88%) and activate with NO heal —
    # the signature 'watch it fix itself' moment silently skipped. This guards that.
    assert reports[0]["pass_rate"] < reports[-1]["pass_rate"], "law activated without healing"
    ids = {r["persona"] for r in reports[0]["results"]}
    assert "legal_advice" in ids and "severe_allergy" not in ids


# ── code-heal: the coding-agent sibling of the policy healer ────────────────────────────────
def _dup_booking_report(spec):
    """A repro where the agent booked the SAME table twice — duplicate_side_effect, a structural
    (code-space) failure: reserve_table isn't idempotent, so two identical calls mint two ids.
    No prompt reliably prevents a double-write under retry; the fix is an idempotency key in code."""
    from otto_spec import CallEvent, CallTrace
    from app import failure
    args = {"name": "Sam", "phone": "555-1212", "date": "tonight", "time": "7:00 PM", "party_size": 2}
    trace = CallTrace(call_id="sim-dup", persona="dup_caller", events=[
        CallEvent(kind="say", t_ms=0, text="Thanks for calling Piccino, how can I help?"),
        CallEvent(kind="hear", t_ms=1500, text="Table for two at 7, under Sam, 555-1212."),
        CallEvent(kind="tool_call", t_ms=1600, name="reserve_table", args=args),
        CallEvent(kind="tool_result", t_ms=1900, name="reserve_table", ok=True, latency_ms=300,
                  result={"status": "confirmed", "reservation_id": "RSV-1000"}),
        CallEvent(kind="hear", t_ms=3000, text="Wait, did that go through? Book it again to be safe."),
        CallEvent(kind="tool_call", t_ms=3100, name="reserve_table", args=args),
        CallEvent(kind="tool_result", t_ms=3400, name="reserve_table", ok=True, latency_ms=300,
                  result={"status": "confirmed", "reservation_id": "RSV-1001"}),
        CallEvent(kind="say", t_ms=3700, text="Done, you're all set."),
    ])
    found = failure.evaluate(spec, trace)
    return trace, found, {"results": [{"persona": "dup_caller", "passed": False,
                                       "trace": trace.model_dump(),
                                       "failures": [f.to_dict() for f in found]}]}


def _dup_persona():
    from app.personas import Persona
    return Persona(id="dup_caller", label="Double-booking retry", category="booking",
                   goal="book a table and re-confirm", personality="anxious",
                   success_criteria="exactly one reservation is created",
                   static_check=lambda s: (True, ""))


# The idempotency fix a coding agent should write: before minting a new id, return any existing
# reservation that matches the same (name, phone, date, time). Stubbed so the test needs no LLM.
_IDEMPOTENT_BLOCK = (
    "    existing = next((r for r in _state()[\"reservations\"]\n"
    "                     if r.get(\"name\") == name and r.get(\"phone\") == phone\n"
    "                     and r.get(\"date\") == date and r.get(\"time\") == time), None)\n"
    "    if existing:\n"
    "        return {\"status\": \"confirmed\", \"reservation_id\": existing[\"id\"], "
    "\"tables_left\": avail[\"tables_left\"]}\n"
)
_RID_ANCHOR = "    rid = f\"RSV-{1000 + len(_state()['reservations'])}\""


def _idempotent_patch(fd, src):
    return src.replace(_RID_ANCHOR, _IDEMPOTENT_BLOCK + _RID_ANCHOR, 1)


def test_duplicate_booking_is_a_code_space_failure():
    """The router signal: a structural failure is stamped fix_space='code' and selected by code_space()."""
    from app import failure
    spec = _spec("piccino")
    _trace, found, _report = _dup_booking_report(spec)
    ids = {f.id for f in found}
    assert "duplicate_side_effect" in ids, ids
    dup = next(f for f in found if f.id == "duplicate_side_effect")
    assert dup.fix_space == "code"
    assert dup.id in {f.id for f in failure.code_space(found)}


def test_code_heal_writes_and_verifies_a_real_fix():
    """The whole loop, LLM-free: a structural failure → idempotency patch → replay oracle flips it
    red→green with no regressions → accepted. This is heal(), with a code diff as the artifact."""
    from app import code_heal
    spec = _spec("piccino")
    _trace, _found, report = _dup_booking_report(spec)
    out = _run(code_heal.heal_code("t-codeheal", spec, report, [_dup_persona()], 1,
                                   patch_fn=_idempotent_patch))
    assert out["applied"] == ["duplicate_side_effect"], out
    fix = next(f for f in out["fixes"] if f["failure_id"] == "duplicate_side_effect")
    assert fix["accepted"] is True
    assert "duplicate_side_effect" in fix["before_failures"]      # the bug reproduced on replay...
    assert "duplicate_side_effect" not in fix["after_failures"]   # ...and the patch cleared it
    assert "existing" in fix["diff"] and fix["diff"].lstrip().startswith("---"), "expected a real unified diff"
    assert out["wrote"] is False  # CODE_HEAL_APPLY defaults off — diff only, no working-tree mutation


def test_code_heal_rejects_a_noop_and_a_cheat_patch():
    """The anti-cheat boundary (the code analog of failure.governed): a patch that doesn't actually
    change tool behavior cannot pass the gate — the grader is ours and runs on the real replay."""
    from app import code_heal
    spec = _spec("piccino")
    _trace, _found, report = _dup_booking_report(spec)
    persona = [_dup_persona()]

    noop = _run(code_heal.heal_code("t-noop", spec, report, persona, 1, patch_fn=lambda fd, src: src))
    assert noop["applied"] == [], "a no-op patch must be rejected"

    cheat = _run(code_heal.heal_code("t-cheat", spec, report, persona, 1,
                                     patch_fn=lambda fd, src: src + "\n# looks busy, fixes nothing\n"))
    assert cheat["applied"] == [], "a cosmetic patch that doesn't fix the invariant must be rejected"
    assert "still present" in next(f for f in cheat["fixes"]
                                   if f["failure_id"] == "duplicate_side_effect")["reason"]


# ── code-heal: production entry point + Cekura-gated live merge ──────────────────────────────
def test_code_heal_for_trace_is_the_production_entry_point():
    """observe_trace hands a single (trace, failures); heal_code_for_trace fixes it WITHOUT a swarm
    report and, with merge off, leaves the tree untouched (diff-only)."""
    from app import code_heal
    spec = _spec("piccino")
    trace, found, _report = _dup_booking_report(spec)
    before_file = code_heal.TARGET_PATH.read_text()
    out = _run(code_heal.heal_code_for_trace("t-prod", spec, trace, found, persona=None,
                                             patch_fn=_idempotent_patch))
    assert out["applied"] == ["duplicate_side_effect"], out
    assert out["merged"] is False
    assert code_heal.TARGET_PATH.read_text() == before_file, "merge off must not touch the file"


def test_code_heal_merges_to_live_line_only_after_harness_passes():
    """The two-tier gate: local replay oracle THEN the conversational harness. Only if the harness
    clears PASS_GATE is the verified fix written + reloaded into the live line."""
    from app import code_heal, swarm, config as cfg
    spec = _spec("piccino")
    trace, found, _ = _dup_booking_report(spec)
    original = code_heal.TARGET_PATH.read_text()
    saved = (cfg.CODE_HEAL_MERGE, cfg.CODE_HEAL_COMMIT, swarm.run_swarm)

    async def harness_passes(*a, **k):
        return {"pass_rate": 1.0, "results": []}
    cfg.CODE_HEAL_MERGE, cfg.CODE_HEAL_COMMIT, swarm.run_swarm = True, False, harness_passes
    try:
        out = _run(code_heal.heal_code_for_trace("t-merge", spec, trace, found, patch_fn=_idempotent_patch))
        assert out["merged"] is True, out
        assert out["gate_pass_rate"] == 1.0
        assert "existing" in code_heal.TARGET_PATH.read_text(), "the fix must be live in the file"
    finally:
        code_heal.TARGET_PATH.write_text(original)
        code_heal._reload_target()
        cfg.CODE_HEAL_MERGE, cfg.CODE_HEAL_COMMIT, swarm.run_swarm = saved


def test_code_heal_rolls_back_when_harness_regresses():
    """Safety: a fix that passes the replay oracle but regresses CONVERSATIONS is rolled back, not merged.
    The live line is never left worse off — the same monotonic guarantee the policy healer gives."""
    from app import code_heal, swarm, config as cfg
    spec = _spec("piccino")
    trace, found, _ = _dup_booking_report(spec)
    original = code_heal.TARGET_PATH.read_text()
    saved = (cfg.CODE_HEAL_MERGE, cfg.CODE_HEAL_COMMIT, swarm.run_swarm)

    async def harness_regresses(*a, **k):
        return {"pass_rate": 0.0, "results": []}
    cfg.CODE_HEAL_MERGE, cfg.CODE_HEAL_COMMIT, swarm.run_swarm = True, False, harness_regresses
    try:
        out = _run(code_heal.heal_code_for_trace("t-rollback", spec, trace, found, patch_fn=_idempotent_patch))
        assert out["merged"] is False, out
        assert out["gate_pass_rate"] == 0.0
        assert code_heal.TARGET_PATH.read_text() == original, "a regressing fix must be rolled back"
    finally:
        code_heal.TARGET_PATH.write_text(original)
        code_heal._reload_target()
        cfg.CODE_HEAL_MERGE, cfg.CODE_HEAL_COMMIT, swarm.run_swarm = saved


def test_model_ladder_escalates_then_stops():
    """The coding agent spends cheap hops first, then one shot on the stronger model; empty escalate = no ladder."""
    from app import code_heal, config as cfg
    saved = (cfg.CODE_HEAL_MAX_HOPS, cfg.CODE_HEAL_MODEL, cfg.CODE_HEAL_ESCALATE_MODEL)
    cfg.CODE_HEAL_MAX_HOPS, cfg.CODE_HEAL_MODEL, cfg.CODE_HEAL_ESCALATE_MODEL = 2, "haiku-x", "sonnet-x"
    try:
        assert code_heal._models() == ["haiku-x", "haiku-x", "sonnet-x"]
        cfg.CODE_HEAL_ESCALATE_MODEL = ""
        assert code_heal._models() == ["haiku-x", "haiku-x"]
    finally:
        cfg.CODE_HEAL_MAX_HOPS, cfg.CODE_HEAL_MODEL, cfg.CODE_HEAL_ESCALATE_MODEL = saved


# ── code-heal: trace-derived replay seeding (the live-trace precondition fix) ────────────────
def test_seed_from_trace_reconstructs_scarcity_preconditions():
    """A live trace has no persona.setup, so replay must rebuild the backend precondition from the
    trace's OWN recorded results — a full slot, out-of-stock item, or taken appointment slot."""
    from otto_spec import CallEvent, CallTrace
    from app import code_heal, mock_services
    oos = CallTrace(call_id="oos", events=[
        CallEvent(kind="tool_call", t_ms=10, name="order_item", args={"item": "tiramisu", "qty": 1}),
        CallEvent(kind="tool_result", t_ms=20, name="order_item", ok=False,
                  result={"status": "out_of_stock", "item": "tiramisu", "qty_available": 0}),
    ])
    seed = code_heal._seed_from_trace(oos.model_dump())
    assert seed is not None
    st = mock_services._fresh(); seed(st)
    assert st["inventory"]["tiramisu"] == 0

    appt = CallTrace(call_id="appt", events=[
        CallEvent(kind="tool_call", t_ms=10, name="book_appointment", args={"time": "9:00 AM"}),
        CallEvent(kind="tool_result", t_ms=20, name="book_appointment", ok=False,
                  result={"status": "unavailable", "reason": "already booked"}),
    ])
    seed2 = code_heal._seed_from_trace(appt.model_dump())
    st2 = mock_services._fresh(); seed2(st2)
    assert any(a["time"] == "9:00 AM" for a in st2["appointments"])


def test_seed_from_trace_leaves_self_contained_failures_alone():
    """Regression guard: duplicate_side_effect's precondition is its OWN first booking (recreated by
    replay), and the call DID reserve successfully — so no scarcity seed is derived. Stays self-contained."""
    from app import code_heal
    spec = _spec("piccino")
    trace, _found, _ = _dup_booking_report(spec)
    assert code_heal._seed_from_trace(trace.model_dump()) is None


def test_replay_seeding_makes_a_state_dependent_failure_reproduce():
    """The seam, closed: a failure whose trigger is backend state (a booking the agent claimed after the
    tool returned 'unavailable') reproduces on replay ONLY when the precondition is seeded from the trace.
    Without the seed the fresh backend would book successfully and the failure would vanish (inconclusive)."""
    from otto_spec import CallEvent, CallTrace
    from app import code_heal, mock_services
    from app.personas import Persona
    spec = _spec("piccino")
    masking = CallTrace(call_id="mask", persona="live", events=[
        CallEvent(kind="hear", t_ms=1500, text="Book a table for two at 8 tonight."),
        CallEvent(kind="tool_call", t_ms=1600, name="reserve_table",
                  args={"name": "Lee", "date": "tonight", "time": "8:00 PM", "party_size": 2}),
        CallEvent(kind="tool_result", t_ms=1900, name="reserve_table", ok=False, latency_ms=300,
                  result={"status": "unavailable", "reason": "fully booked"}),
        CallEvent(kind="say", t_ms=2200, text="Perfect, you're all set for two at eight!"),
    ])
    td = masking.model_dump()
    noop = Persona(id="x", label="x", category="booking", goal="", personality="",
                   success_criteria="", static_check=lambda s: (True, ""), setup=lambda st: None)
    ctrl = _run(code_heal.replay(spec, td, noop, mock_services))
    assert "failed_action_masked" not in {f.id for f in ctrl}, "control should NOT reproduce"
    treat = _run(code_heal.replay(spec, td, None, mock_services))
    assert "failed_action_masked" in {f.id for f in treat}, "seeded replay must reproduce the failure"


# ── code-heal: heavy STUBBED coverage of the live agent path (no real LLM calls) ─────────────
# A scripted edit_file tool-call; [] = the model emitted no tool call.
def _edit(old, new):
    return [{"id": "t", "name": "edit_file", "args": {"old_string": old, "new_string": new}}]


@contextlib.contextmanager
def _stub_agent(scripts):
    """Replace llm.complete_tools with a fake that pops scripted tool_calls and records each model
    used, and force can_author True — so the whole live orchestration (hops, escalation, retries,
    verification) runs WITHOUT a single real API call."""
    from app import llm, config as cfg
    rec = {"models": [], "calls": 0}
    queue = list(scripts)

    async def fake(system, messages, tools, *, temperature=0.0, model=None, max_tokens=1024, force_tool=False):
        rec["models"].append(model)
        rec["calls"] += 1
        return {"content": "", "tool_calls": queue.pop(0) if queue else []}

    saved = (llm.complete_tools, llm.tool_calling_available, cfg.llm_available)
    llm.complete_tools = fake
    llm.tool_calling_available = lambda: True
    cfg.llm_available = lambda: True
    try:
        yield rec
    finally:
        llm.complete_tools, llm.tool_calling_available, cfg.llm_available = saved


_IDEM_NEW = _IDEMPOTENT_BLOCK + _RID_ANCHOR          # the new_string a correct fix edit supplies
_GETINV_LINE = 'def get_inventory(item: str = "", **_) -> dict:'  # a benign unique line for cosmetic edits


def _src():
    from app import code_heal
    return code_heal.TARGET_PATH.read_text()


# --- _run_agent: the edit_file application contract ---
def test_run_agent_applies_a_valid_unique_edit():
    from app import code_heal
    with _stub_agent([_edit(_RID_ANCHOR, _IDEM_NEW)]):
        new_src, note = _run(code_heal._run_agent([{"role": "user", "content": "x"}], _src(), "m"))
    assert note == "ok" and new_src is not None and "existing" in new_src


def test_run_agent_rejects_unfindable_ambiguous_and_missing():
    from app import code_heal
    src = _src()
    with _stub_agent([_edit("NOT_IN_THE_FILE_AT_ALL", "x")]):
        ns, note = _run(code_heal._run_agent([{"role": "user", "content": "x"}], src, "m"))
        assert ns is None and "not found" in note
    with _stub_agent([_edit("_state()", "x")]):   # appears many times → ambiguous
        ns, note = _run(code_heal._run_agent([{"role": "user", "content": "x"}], src, "m"))
        assert ns is None and "multiple" in note
    with _stub_agent([[{"id": "t", "name": "edit_file", "args": {"new_string": "x"}}]]):  # no old_string
        ns, note = _run(code_heal._run_agent([{"role": "user", "content": "x"}], src, "m"))
        assert ns is None and "missing" in note
    with _stub_agent([[]]):  # model replied with no tool call at all
        ns, note = _run(code_heal._run_agent([{"role": "user", "content": "x"}], src, "m"))
        assert ns is None and "no edit_file" in note


# --- the hop loop + model escalation (stubbed agent) ---
def test_live_agent_fixes_on_first_hop():
    from app import code_heal
    spec = _spec("piccino")
    trace, found, _ = _dup_booking_report(spec)
    with _stub_agent([_edit(_RID_ANCHOR, _IDEM_NEW)]) as rec:
        out = _run(code_heal.heal_code_for_trace("t-h1", spec, trace, found))
    assert out["applied"] == ["duplicate_side_effect"]
    assert rec["calls"] == 1 and rec["models"][0] == config.CODE_HEAL_MODEL


def test_live_agent_escalates_to_the_stronger_model_only_after_cheap_hops_fail():
    from app import code_heal, config as cfg
    spec = _spec("piccino")
    trace, found, _ = _dup_booking_report(spec)
    saved = (cfg.CODE_HEAL_MAX_HOPS, cfg.CODE_HEAL_MODEL, cfg.CODE_HEAL_ESCALATE_MODEL)
    cfg.CODE_HEAL_MAX_HOPS, cfg.CODE_HEAL_MODEL, cfg.CODE_HEAL_ESCALATE_MODEL = 2, "haiku-x", "sonnet-x"
    script = [
        _edit("NOT_IN_FILE", "x"),                              # hop0 (haiku): unapplicable
        _edit(_GETINV_LINE, _GETINV_LINE + "  # noop"),         # hop1 (haiku): applies but doesn't fix
        _edit(_RID_ANCHOR, _IDEM_NEW),                          # hop2 (sonnet): the real fix
    ]
    try:
        with _stub_agent(script) as rec:
            out = _run(code_heal.heal_code_for_trace("t-esc", spec, trace, found))
        assert out["applied"] == ["duplicate_side_effect"], out
        assert rec["models"] == ["haiku-x", "haiku-x", "sonnet-x"], rec["models"]
    finally:
        cfg.CODE_HEAL_MAX_HOPS, cfg.CODE_HEAL_MODEL, cfg.CODE_HEAL_ESCALATE_MODEL = saved


def test_live_agent_gives_up_after_the_ladder_is_exhausted():
    from app import code_heal, config as cfg
    spec = _spec("piccino")
    trace, found, _ = _dup_booking_report(spec)
    saved = (cfg.CODE_HEAL_MAX_HOPS, cfg.CODE_HEAL_MODEL, cfg.CODE_HEAL_ESCALATE_MODEL)
    cfg.CODE_HEAL_MAX_HOPS, cfg.CODE_HEAL_MODEL, cfg.CODE_HEAL_ESCALATE_MODEL = 2, "haiku-x", "sonnet-x"
    cos = _edit(_GETINV_LINE, _GETINV_LINE + "  # noop")
    try:
        with _stub_agent([cos, cos, cos]) as rec:   # never fixes it
            out = _run(code_heal.heal_code_for_trace("t-giveup", spec, trace, found))
        assert out["applied"] == []
        assert len(rec["models"]) == 3                # cheap, cheap, escalated — then stop
        assert "still present" in out["fixes"][0]["reason"]
    finally:
        cfg.CODE_HEAL_MAX_HOPS, cfg.CODE_HEAL_MODEL, cfg.CODE_HEAL_ESCALATE_MODEL = saved


def test_live_agent_retries_after_an_unapplicable_edit_then_succeeds():
    from app import code_heal, config as cfg
    spec = _spec("piccino")
    trace, found, _ = _dup_booking_report(spec)
    saved = cfg.CODE_HEAL_ESCALATE_MODEL
    cfg.CODE_HEAL_ESCALATE_MODEL = ""   # no escalation: prove it recovers within the cheap hops
    try:
        with _stub_agent([_edit("NOT_IN_FILE", "x"), _edit(_RID_ANCHOR, _IDEM_NEW)]) as rec:
            out = _run(code_heal.heal_code_for_trace("t-retry", spec, trace, found))
        assert out["applied"] == ["duplicate_side_effect"]
        assert rec["calls"] == 2
    finally:
        cfg.CODE_HEAL_ESCALATE_MODEL = saved


# --- _verify edge cases (pure replay oracle, no LLM) ---
def test_verify_rejects_a_syntax_error_patch():
    from app import code_heal
    spec = _spec("piccino")
    trace, _f, _ = _dup_booking_report(spec)
    v = _run(code_heal._verify(spec, trace.model_dump(), None, "duplicate_side_effect",
                               _src(), _src() + "\ndef oops(:\n  pass\n", "ut_syn"))
    assert v["accepted"] is False and "syntax" in v["reason"]


def test_verify_rejects_a_patch_that_breaks_the_public_interface():
    from app import code_heal
    spec = _spec("piccino")
    trace, _f, _ = _dup_booking_report(spec)
    broken = _src().replace("def call(name: str, args: dict) -> dict:",
                            "def call_RENAMED(name: str, args: dict) -> dict:", 1)
    v = _run(code_heal._verify(spec, trace.model_dump(), None, "duplicate_side_effect",
                               _src(), broken, "ut_iface"))
    assert v["accepted"] is False and "interface" in v["reason"]


def test_verify_rejects_a_patch_that_regresses_another_dimension():
    """Clears duplicate_side_effect but, by making reserve_table always 'unavailable', creates a NEW
    masking failure (the trace still claims success) → monotonic guard rejects it."""
    from app import code_heal
    spec = _spec("piccino")
    trace, _f, _ = _dup_booking_report(spec)
    regress = _src().replace(_RID_ANCHOR, '    return {"status": "unavailable", "reason": "x"}\n' + _RID_ANCHOR, 1)
    v = _run(code_heal._verify(spec, trace.model_dump(), None, "duplicate_side_effect",
                               _src(), regress, "ut_regress"))
    assert v["accepted"] is False and "regress" in v["reason"]
    assert "failed_action_masked" in v["after"]


def test_verify_is_inconclusive_when_the_repro_does_not_reproduce():
    from app import code_heal
    spec = _spec("piccino")
    trace, _f, _ = _dup_booking_report(spec)
    fixed = _idempotent_patch({}, _src())
    v = _run(code_heal._verify(spec, trace.model_dump(), None, "orphaned_action",
                               _src(), fixed, "ut_inconc"))
    assert v["accepted"] is False and "inconclusive" in v["reason"]


# --- routing / target selection / no-op behavior ---
def test_targets_dedup_and_ignore_policy_and_traceless_results():
    from app import code_heal
    code_fail = {"id": "duplicate_side_effect", "fix_space": "code"}
    policy_fail = {"id": "unconfirmed_success", "fix_space": "policy"}
    report = {"results": [
        {"persona": "p1", "trace": {"events": []}, "failures": [code_fail]},
        {"persona": "p1", "trace": {"events": []}, "failures": [code_fail]},   # same (id,persona) → dedup
        {"persona": "p2", "trace": {"events": []}, "failures": [policy_fail]},  # policy → ignored
        {"persona": "p3", "failures": [code_fail]},                            # no trace → skipped
    ]}
    targets = code_heal._targets_from_report(report, [])
    assert len(targets) == 1 and targets[0][0]["id"] == "duplicate_side_effect"


def test_heal_code_is_a_noop_without_code_space_failures():
    from app import code_heal
    spec = _spec("piccino")
    report = {"results": [{"persona": "p", "trace": {"events": []},
                           "failures": [{"id": "unconfirmed_success", "fix_space": "policy"}]}]}
    out = _run(code_heal.heal_code("t-noop2", spec, report, [], 1))
    assert out["checked"] == 0 and out["applied"] == []


def test_heal_code_reports_cleanly_when_no_agent_backend_is_available():
    from app import code_heal, llm, config as cfg
    spec = _spec("piccino")
    trace, found, _ = _dup_booking_report(spec)
    saved = (llm.tool_calling_available, cfg.llm_available)
    llm.tool_calling_available = lambda: False
    cfg.llm_available = lambda: False
    try:
        out = _run(code_heal.heal_code_for_trace("t-noback", spec, trace, found))  # no patch_fn, no stub
        assert out["applied"] == []
        assert "no coding-agent backend" in out["fixes"][0]["reason"]
    finally:
        llm.tool_calling_available, cfg.llm_available = saved


def test_accepted_fix_emits_a_code_patch_event():
    from app import code_heal
    spec = _spec("piccino")
    trace, found, _ = _dup_booking_report(spec)
    _run(code_heal.heal_code_for_trace("t-evt", spec, trace, found, patch_fn=_idempotent_patch))
    evs = [e for e in bus.history("t-evt") if e.get("type") == "code_patch"]
    assert evs and evs[0]["fix"]["failure_id"] == "duplicate_side_effect"
    assert evs[0]["fix"]["accepted"] is True


def test_gate_and_merge_rolls_back_when_the_harness_errors():
    from app import code_heal, swarm, config as cfg
    spec = _spec("piccino")
    trace, found, _ = _dup_booking_report(spec)
    original = code_heal.TARGET_PATH.read_text()
    saved = (cfg.CODE_HEAL_MERGE, cfg.CODE_HEAL_COMMIT, swarm.run_swarm)

    async def boom(*a, **k):
        raise RuntimeError("harness down")
    cfg.CODE_HEAL_MERGE, cfg.CODE_HEAL_COMMIT, swarm.run_swarm = True, False, boom
    try:
        out = _run(code_heal.heal_code_for_trace("t-gateerr", spec, trace, found, patch_fn=_idempotent_patch))
        assert out["merged"] is False
        assert code_heal.TARGET_PATH.read_text() == original, "a harness error must roll the file back"
    finally:
        code_heal.TARGET_PATH.write_text(original)
        code_heal._reload_target()
        cfg.CODE_HEAL_MERGE, cfg.CODE_HEAL_COMMIT, swarm.run_swarm = saved
