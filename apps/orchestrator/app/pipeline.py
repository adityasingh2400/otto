"""The pipeline: extract → build → swarm → heal → re-run → gated activation.

This is the loop the demo narrates. Activation is GATED on PASS_GATE — the number
does not go live until the agent clears the bar, which is the whole point.
"""

from __future__ import annotations

import asyncio

from otto_spec import AgentSpec

from . import archetypes, config, extract, failure, mock_services, store
from .events import bus
from .code_heal import heal_code
from .heal import heal
from .observe import _probe_persona
from .swarm import run_swarm


def _trace_probes(report: dict) -> list:
    """Reconstruct probe personas from the action/outcome failures the TRACE sim detected this round.
    Each carries the fix its detector authored + a `governed` static_check, so safe_apply can verify
    the fix genuinely closes the gap (and regresses nothing) before it ships — same gate production uses."""
    probes, seen = [], set()
    for r in report.get("results", []):
        if r.get("passed"):
            continue
        for d in r.get("failures", []) or []:
            pid = d.get("heal_policy_id") or d.get("id")
            if not pid or pid in seen:
                continue
            seen.add(pid)
            try:
                probes.append(_probe_persona(failure.FailureInstance(**d)))
            except TypeError:
                continue
    return probes


async def run_pipeline(session_id: str, url: str | None, use_cached: bool, cached: str | None = None,
                       extra_info: str | None = None) -> None:
    try:
        mock_services.reset()  # fresh stateful backend per build (inventory, bookings)
        spec = await extract.extract(session_id, url, use_cached, cached, extra_info)
        store.set_spec(session_id, spec)
        await _build(session_id, spec)

        # vertical-archetyped swarm: a contractor gets dispatch/licensing callers, a
        # clinic gets 'no medical advice' red-teams, never restaurant allergy tests.
        personas = archetypes.select_for(spec.business.type, config.SWARM_PERSONAS)
        fixes = {p.id: p.fix for p in personas if p.fix}
        await bus.publish(session_id, {"type": "fact", "topic": "swarm",
                                       "content": f"{archetypes.vertical_for(spec.business.type)} archetype · {len(personas)} caller types"})

        if config.DEMO_PACING:
            await asyncio.sleep(2.5)  # let the agent profile land before the swarm attack streams in
        round_no = 1
        report = await run_swarm(session_id, spec, round_no, personas)

        while report["pass_rate"] < config.PASS_GATE and round_no <= config.MAX_HEAL_ROUNDS:
            failures = [r for r in report["results"] if not r["passed"]]
            if not failures:
                break
            # Fold in the action/outcome failures the trace sim caught: each authored its own fix, and
            # each probe joins the regression suite so safe_apply proves the fix works without regressing.
            probes = _trace_probes(report)
            extra_patches = [pr.fix for pr in probes if pr.fix]
            spec, _diffs = await heal(session_id, spec, failures, round_no, fixes,
                                      scenarios=personas + probes, extra_patches=extra_patches)
            store.set_spec(session_id, spec)
            await bus.publish(session_id, {"type": "spec", "spec": spec.model_dump()})
            round_no += 1
            await bus.publish(session_id, {"type": "stage", "stage": "rerun", "status": "start",
                                           "detail": f"re-running swarm on v{spec.meta.version}"})
            report = await run_swarm(session_id, spec, round_no, personas)
            await bus.publish(session_id, {"type": "stage", "stage": "rerun", "status": "done",
                                           "detail": f"pass rate {report['pass_rate']*100:.0f}%"})

        # Route any STRUCTURAL failures the policy healer can't close (idempotency, a tool that always
        # returns, a secure boundary) to the coding agent: it writes a real diff to the tool layer,
        # verified by the SAME trace-sim oracle (replay → re-evaluate), and surfaces it for review.
        # A strict no-op when there are no code-space failures, so it's free on the happy path.
        if config.CODE_HEAL:
            await heal_code(session_id, spec, report, personas, round_no)

        if report["pass_rate"] >= config.PASS_GATE:
            if config.REQUIRE_APPROVAL:
                # Cleared the gate — hand to the owner. The Deploy button (POST /api/activate) puts it live.
                await bus.publish(session_id, {"type": "awaiting_deploy", "pass_rate": report["pass_rate"],
                                               "phone_number": config.TWILIO_PHONE_NUMBER or "+1 (555) 010-OTTO",
                                               "spec_version": spec.meta.version})
                await bus.publish(session_id, {"type": "stage", "stage": "activate", "status": "done",
                                               "detail": f"cleared {report['pass_rate']*100:.0f}% — awaiting owner deploy"})
            else:
                await activate(session_id, spec)
        else:
            await bus.publish(session_id, {"type": "stage", "stage": "activate", "status": "done",
                                           "detail": f"BLOCKED — {report['pass_rate']*100:.0f}% < gate {config.PASS_GATE*100:.0f}%"})
    except Exception as e:  # surface, don't swallow
        await bus.publish(session_id, {"type": "fact", "topic": "error", "content": str(e)})
        await bus.publish(session_id, {"type": "stage", "stage": "activate", "status": "done",
                                       "detail": f"pipeline error: {e}"})


async def _build(session_id: str, spec: AgentSpec) -> None:
    await bus.publish(session_id, {"type": "stage", "stage": "build", "status": "start",
                                   "detail": "compiling agent from spec"})
    await bus.publish(session_id, {"type": "spec", "spec": spec.model_dump()})
    await bus.publish(session_id, {"type": "stage", "stage": "build", "status": "done",
                                   "detail": f"greeting + {len(spec.policies)} policies + {len(spec.tools)} tools"})


async def activate(session_id: str, spec: AgentSpec) -> None:
    """Owner pressed Deploy: mark this the active business AND auto-point the Twilio number's webhook
    at the live agent, so an inbound call just works — no console step."""
    await bus.publish(session_id, {"type": "stage", "stage": "activate", "status": "start",
                                   "detail": "provisioning inbound line"})
    number = config.TWILIO_PHONE_NUMBER or "+1 (555) 010-OTTO"
    try:
        wh = await _point_twilio_webhook()
    except Exception as e:
        wh = f"webhook not auto-set ({str(e)[:70]})"
    store.set_active(session_id, number, spec.meta.version)  # the agent serves THIS business now
    await bus.publish(session_id, {"type": "fact", "topic": "deploy", "content": f"Twilio webhook: {wh}"})
    await bus.publish(session_id, {"type": "activated", "phone_number": number})
    await bus.publish(session_id, {"type": "stage", "stage": "activate", "status": "done",
                                   "detail": f"inbound line live on v{spec.meta.version} · {number}"})


async def _point_twilio_webhook() -> str:
    """Best-effort: set the Twilio number's Voice webhook to PUBLIC_BASE_URL/twiml via the REST API,
    so a real call reaches the live agent automatically. Returns a short status for the deploy log."""
    sid, tok = config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN
    num, base = config.TWILIO_PHONE_NUMBER, config.PUBLIC_BASE_URL
    if not (sid and tok and num and base):
        return "skipped — set PUBLIC_BASE_URL (run scripts/golive.sh) to auto-point it"
    import httpx
    api = f"https://api.twilio.com/2010-04-01/Accounts/{sid}"
    voice_url = base.rstrip("/") + "/twiml"
    async with httpx.AsyncClient(timeout=8.0, auth=(sid, tok)) as c:
        r = await c.get(f"{api}/IncomingPhoneNumbers.json", params={"PhoneNumber": num})
        r.raise_for_status()
        items = r.json().get("incoming_phone_numbers", [])
        if not items:
            return f"{num} not found on this account"
        nsid = items[0]["sid"]
        u = await c.post(f"{api}/IncomingPhoneNumbers/{nsid}.json",
                         data={"VoiceUrl": voice_url, "VoiceMethod": "POST"})
        u.raise_for_status()
    return f"→ {voice_url}"
