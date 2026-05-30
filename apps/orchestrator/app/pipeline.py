"""The pipeline: extract → build → swarm → heal → re-run → gated activation.

This is the loop the demo narrates. Activation is GATED on PASS_GATE — the number
does not go live until the agent clears the bar, which is the whole point.
"""

from __future__ import annotations

from otto_spec import AgentSpec

from . import archetypes, config, extract, mock_services, store
from .events import bus
from .heal import heal
from .swarm import run_swarm


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

        round_no = 1
        report = await run_swarm(session_id, spec, round_no, personas)

        while report["pass_rate"] < config.PASS_GATE and round_no <= config.MAX_HEAL_ROUNDS:
            failures = [r for r in report["results"] if not r["passed"]]
            if not failures:
                break
            spec, _diffs = await heal(session_id, spec, failures, round_no, fixes, scenarios=personas)
            store.set_spec(session_id, spec)
            await bus.publish(session_id, {"type": "spec", "spec": spec.model_dump()})
            round_no += 1
            await bus.publish(session_id, {"type": "stage", "stage": "rerun", "status": "start",
                                           "detail": f"re-running swarm on v{spec.meta.version}"})
            report = await run_swarm(session_id, spec, round_no, personas)
            await bus.publish(session_id, {"type": "stage", "stage": "rerun", "status": "done",
                                           "detail": f"pass rate {report['pass_rate']*100:.0f}%"})

        if report["pass_rate"] >= config.PASS_GATE:
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
    await bus.publish(session_id, {"type": "stage", "stage": "activate", "status": "start",
                                   "detail": "provisioning inbound line"})
    number = _provision_twilio(spec)
    store.set_active(session_id, number, spec.meta.version)
    await bus.publish(session_id, {"type": "activated", "phone_number": number})
    await bus.publish(session_id, {"type": "stage", "stage": "activate", "status": "done",
                                   "detail": f"inbound line live on v{spec.meta.version}"})


def _provision_twilio(spec: AgentSpec) -> str:
    # D2 TODO(team): point the Twilio number's voice webhook at PUBLIC_BASE_URL/twiml
    # (apps/agent/twilio_server.py) via the Twilio REST API. Guarded best-effort here.
    if config.TWILIO_PHONE_NUMBER:
        return config.TWILIO_PHONE_NUMBER
    return "+1 (555) 010-OTTO"
