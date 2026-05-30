"""Live-call channel — stream a call AS IT HAPPENS to the dashboard, with incremental failure
detection firing per turn (the "watch it catch the error live" view).

Fed two ways, both driving the SAME SSE events so the demo works with or without a phone:
  * the real agent (apps/agent/bot.py) POSTs frames to POST /api/live/{session} as they occur, OR
  * the live-replay simulator (`simulate`) streams a recorded enriched trace word-by-word.

On each finalized turn we re-run the full failure taxonomy on the PARTIAL trace and announce any
NEW failure as a `live_detect` event — so an accent / mishear / shouting caller lights up mid-call,
in real time. At call end we assemble the trace, hand off to the existing `observe_trace` heal loop
(the targeted swarm-heal), and record the call in a per-session feed so every finished call shows up.

SSE events emitted (consumed by apps/web/app/index.html):
  live_start    {call_id, scenario}
  live_partial  {who: caller|agent, text, asr_conf}     — the per-WORD streaming transcript
  live_event    {event: CallEvent, asr_conf}            — a finalized turn / tool event
  live_detect   {failure: FailureInstance, gating: bool} — a NEW failure caught live
  live_end      {call_id}
  call_recorded {call: summary, total_calls}            — appended to the finished-calls feed
"""

from __future__ import annotations

import asyncio

from otto_spec import AudioFeatures, CallEvent, CallTrace

from . import failure, store
from .events import bus

_LIVE: dict[str, dict] = {}          # session -> {"events": [...], "seen": set()}  (in-flight call)
_CALLS: dict[str, list[dict]] = {}   # session -> [finished-call summaries]  (the UI feed)


def _state(session_id: str) -> dict:
    return _LIVE.setdefault(session_id, {"events": [], "seen": set()})


def calls(session_id: str) -> list[dict]:
    return list(_CALLS.get(session_id, ()))


def record_call(session_id: str, summary: dict) -> int:
    _CALLS.setdefault(session_id, []).append(summary)
    return len(_CALLS[session_id])


def _to_event(ev: dict) -> CallEvent | None:
    kind = ev.get("kind")
    if kind == "hear":
        audio = ev.get("audio")
        return CallEvent(kind="hear", t_ms=int(ev.get("t_ms", 0)), text=ev.get("text", ""),
                         asr_conf=float(ev.get("asr_conf", 1.0)),
                         audio=AudioFeatures(**audio) if isinstance(audio, dict) else None)
    if kind == "say":
        return CallEvent(kind="say", t_ms=int(ev.get("t_ms", 0)), text=ev.get("text", ""))
    if kind in ("tool_call", "tool_result"):
        return CallEvent(kind=kind, t_ms=int(ev.get("t_ms", 0)), name=ev.get("name", ""),
                         args=ev.get("args") or {}, ok=ev.get("ok"),
                         result=ev.get("result") or {}, latency_ms=int(ev.get("latency_ms", 0)))
    return None


async def ingest(session_id: str, ev: dict) -> dict:
    """Ingest one live event from the agent (or simulator): fan out to SSE + run live detection."""
    kind = ev.get("kind") or ev.get("type")

    if kind == "call_start":
        _LIVE[session_id] = {"events": [], "seen": set()}
        await bus.publish(session_id, {"type": "live_start", "call_id": ev.get("call_id", "live"),
                                       "scenario": ev.get("scenario", "")})
        return {"ok": True}

    if kind in ("hear_partial", "say_partial"):  # the per-WORD streaming feel
        await bus.publish(session_id, {"type": "live_partial",
                                       "who": "caller" if kind == "hear_partial" else "agent",
                                       "text": ev.get("text", ""), "asr_conf": float(ev.get("asr_conf", 1.0))})
        return {"ok": True}

    st = _state(session_id)
    ce = _to_event(ev)
    if ce is not None:
        st["events"].append(ce)
        await bus.publish(session_id, {"type": "live_event", "event": ce.model_dump(),
                                       "asr_conf": ce.asr_conf if ce.kind == "hear" else None})

    # incremental detection: re-run the taxonomy on the partial trace, announce anything NEW.
    spec = store.get_spec(session_id)
    if spec is not None and ce is not None and ce.kind in ("hear", "say", "tool_result"):
        trace = CallTrace(call_id="live", events=list(st["events"]))
        try:
            found = failure.evaluate(spec, trace)
        except Exception:
            found = []
        for f in found:
            if f.id in st["seen"]:
                continue
            st["seen"].add(f.id)
            await bus.publish(session_id, {"type": "live_detect", "failure": f.to_dict(),
                                           "gating": f.id not in failure.MONITORING_ONLY})

    if kind == "call_end":
        return await _finalize(session_id, ev.get("scenario", ""))
    return {"ok": True}


async def _finalize(session_id: str, scenario: str) -> dict:
    """Call ended: assemble the trace, run the existing heal loop, record it in the feed."""
    st = _state(session_id)
    events = list(st.get("events", []))
    _LIVE.pop(session_id, None)
    n = len(_CALLS.get(session_id, ())) + 1
    trace = CallTrace(call_id=f"live-{n}", events=events)
    await bus.publish(session_id, {"type": "live_end", "call_id": trace.call_id})

    from .observe import observe_trace
    try:
        result = await observe_trace(session_id, trace)   # the targeted swarm-heal (loop #2)
    except Exception as e:
        result = {"failed": False, "error": str(e)[:160]}

    summary = {
        "call_id": trace.call_id, "scenario": scenario or "live call",
        "turns": sum(1 for e in events if e.kind in ("hear", "say")),
        "failed": bool(result.get("failed")),
        "failures": [f.get("title") for f in (result.get("failures") or [])],
        "pre_pass": result.get("pre_pass"), "post_pass": result.get("post_pass"),
        "spec_version": result.get("spec_version"),
    }
    total = record_call(session_id, summary)
    await bus.publish(session_id, {"type": "call_recorded", "call": summary, "total_calls": total})
    return {"ok": True, "result": result}


async def simulate(session_id: str, trace_id: str, *, wps: float = 7.0) -> dict:
    """Replay a recorded enriched trace through the live channel word-by-word with realistic
    timing — the 'watch it live' demo, no phone required. Drives the exact same events a real
    call would, so the dashboard live view + incremental detection + swarm-heal all fire."""
    from . import traces
    tr = traces.get(trace_id)
    if tr is None:
        return {"error": f"unknown trace {trace_id}"}
    label = (traces.TRACES.get(trace_id) or {}).get("label", trace_id)
    await ingest(session_id, {"kind": "call_start", "call_id": tr.call_id, "scenario": label})
    word_delay = 1.0 / max(1.0, wps)
    for e in tr.events:
        if e.kind in ("hear", "say"):
            who = "hear_partial" if e.kind == "hear" else "say_partial"
            acc = ""
            for w in e.text.split():
                acc = (acc + " " + w).strip()
                await ingest(session_id, {"kind": who, "text": acc,
                                          "asr_conf": e.asr_conf if e.kind == "hear" else 1.0})
                await asyncio.sleep(word_delay)
            payload: dict = {"kind": e.kind, "t_ms": e.t_ms, "text": e.text}
            if e.kind == "hear":
                payload["asr_conf"] = e.asr_conf
                if e.audio is not None:
                    payload["audio"] = e.audio.model_dump()
            await ingest(session_id, payload)
            await asyncio.sleep(0.35)
        else:  # tool_call / tool_result — show the action mid-call
            await ingest(session_id, {"kind": e.kind, "t_ms": e.t_ms, "name": e.name,
                                      "args": e.args, "ok": e.ok, "result": e.result, "latency_ms": e.latency_ms})
            await asyncio.sleep(0.25)
    return await ingest(session_id, {"kind": "call_end", "scenario": label})
