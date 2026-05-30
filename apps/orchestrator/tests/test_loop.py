"""End-to-end tests for the LineForge loop. No keys needed (static mode).

Run:  cd apps/orchestrator && uv run --python 3.12 --with pytest python -m pytest tests -q
"""

import asyncio

from lineforge_spec import AgentSpec

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


def test_vertical_routing():
    assert archetypes.vertical_for("Italian restaurant") == "restaurant"
    assert archetypes.vertical_for("construction company") == "contractor"
    assert archetypes.vertical_for("dental clinic") == "clinic"
    assert archetypes.vertical_for("barber shop") == "generic"        # not "bar"
    assert archetypes.vertical_for("sports bar & grill") == "restaurant"
    assert archetypes.vertical_for("roofing contractor") == "contractor"


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
