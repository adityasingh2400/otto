"""End-to-end tests for the Otto loop. No keys needed (static mode).

Run:  cd apps/orchestrator && uv run --python 3.12 --with pytest python -m pytest tests -q
"""

import asyncio

from otto_spec import AgentSpec

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
    ids = {r["persona"] for r in reports[0]["results"]}
    assert "emergency_dispatch" in ids, "contractor archetype not used"
    assert "severe_allergy" not in ids, "restaurant persona leaked into contractor swarm"


def test_clinic_pipeline_heals_and_routes():
    _run(run_pipeline("t-clinic", None, False, "clinic"))
    reports = [e["report"] for e in bus.history("t-clinic") if e.get("type") == "swarm_report"]
    assert reports and reports[-1]["pass_rate"] >= config.PASS_GATE
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
    ids = {r["persona"] for r in reports[0]["results"]}
    assert "stylist_request" in ids and "severe_allergy" not in ids


def test_law_pipeline_heals_and_routes():
    _run(run_pipeline("t-law", None, False, "law"))
    reports = [e["report"] for e in bus.history("t-law") if e.get("type") == "swarm_report"]
    assert reports and reports[-1]["pass_rate"] >= config.PASS_GATE
    ids = {r["persona"] for r in reports[0]["results"]}
    assert "legal_advice" in ids and "severe_allergy" not in ids
