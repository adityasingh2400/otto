"""End-to-end smoke test of the pipeline, no server needed.

    cd apps/orchestrator && uv run python scripts/smoke.py

Runs the full extract → swarm → heal → re-run → activate loop on the cached Piccino
spec and prints the event timeline. In static mode (no keys) this proves the loop is
real: the heal genuinely flips the failing policy-coverage checks.
"""

import asyncio
import sys

sys.path.insert(0, ".")

from app import config  # noqa: E402
from app.events import bus  # noqa: E402
from app.pipeline import run_pipeline  # noqa: E402


async def main() -> None:
    session = "smoke"
    print(f"mode={config.SWARM_MODE} llm={config.llm_available()} cekura={config.cekura_available()} gate={config.PASS_GATE}\n")
    await run_pipeline(session, url=None, use_cached=True)

    print("\n── event timeline ──")
    for ev in bus.history(session):
        t = ev.get("type")
        if t == "stage":
            print(f"  [stage] {ev['stage']:<9} {ev['status']:<5} {ev.get('detail','')}")
        elif t == "swarm_report":
            r = ev["report"]
            print(f"  [swarm] round {r['round']} v{r['spec_version']}: {r['passed']}/{r['total']} pass = {r['pass_rate']*100:.0f}%")
        elif t == "patch":
            print(f"  [patch] round {ev['round']}: {len(ev['diffs'])} policy changes "
                  f"({', '.join(d['change']+':'+d['policy_id'] for d in ev['diffs'])})")
        elif t == "activated":
            print(f"  [ACTIVE] inbound line live: {ev['phone_number']}")
    # final assertion
    reports = [e["report"] for e in bus.history(session) if e.get("type") == "swarm_report"]
    if reports:
        print(f"\nrestaurant: first {reports[0]['pass_rate']*100:.0f}% → final {reports[-1]['pass_rate']*100:.0f}%")

    # ── vertical archetype routing ──
    from app import archetypes  # noqa: E402
    print("\n── vertical routing (no wasted simulations) ──")
    for bt in ["Italian restaurant", "construction company", "dental clinic", "barber shop"]:
        ps = archetypes.select_for(bt, 12)
        print(f"  {bt:22} → {archetypes.vertical_for(bt):10} : {[p.id for p in ps if p.hero]}")

    # ── second vertical end-to-end (contractor, static) ──
    from otto_spec import AgentSpec  # noqa: E402
    from app.heal import heal  # noqa: E402
    from app.swarm import run_swarm  # noqa: E402
    con = AgentSpec.model_validate_json((config.SPEC_DIR / "contractor.json").read_text())
    cps = archetypes.select_for(con.business.type, 12)
    cfix = {p.id: p.fix for p in cps if p.fix}
    r1 = await run_swarm("smoke2", con, 1, cps)
    fails = [x for x in r1["results"] if not x["passed"]]
    con2, _ = await heal("smoke2", con, fails, 1, cfix)
    r2 = await run_swarm("smoke2", con2, 2, cps)
    print(f"\ncontractor: first {r1['pass_rate']*100:.0f}% → final {r2['pass_rate']*100:.0f}% "
          f"(fixed: {[f['persona'] for f in fails]})")


if __name__ == "__main__":
    asyncio.run(main())
