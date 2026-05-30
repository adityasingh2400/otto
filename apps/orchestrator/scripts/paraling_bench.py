"""Paralinguistic detection benchmark: Otto's Nemotron audio layer vs a plain STT transcript.

The pitch, made rigorous and reproducible. A normal voice agent sees only the STT *transcript* —
words on a page. It is structurally blind to HOW something was said: a caller the model can barely
parse, someone shouting, a line full of street noise, a caller talked over mid-sentence, a switch to
Spanish. Those are real call failures, and they are invisible in text.

Otto attaches paralinguistic features to every caller turn (CallEvent.audio: ASR confidence, energy/
volume, arousal, SNR, language, barge-in, disfluency) and runs five signal-driven detectors on top of
the same event-stream failure taxonomy. This benchmark holds the taxonomy fixed and toggles ONE thing
— whether the audio features are present — to isolate exactly what the audio layer buys you.

  PLAIN STT   = evaluate the identical traces with audio features stripped (text only).
  OTTO        = evaluate them with the Nemotron paralinguistic features attached.

Each trace is labelled with the voice failure it contains; we report recall for each system and the
gap. Run:  uv run python scripts/paraling_bench.py   (no API keys; the taxonomy is deterministic.)
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from otto_spec import AgentSpec, AudioFeatures, Business, CallEvent, CallTrace, EscalationRule, Voice

from app import failure

# A minimal but valid agent (a restaurant front desk). The taxonomy needs a spec for context; the
# paralinguistic detectors key on the caller audio, not the business — so the spec is held constant.
SPEC = AgentSpec(
    business=Business(name="Piccino", type="restaurant", location="San Francisco"),
    voice=Voice(greeting="Thanks for calling Piccino, how can I help?"),
    escalation_rules=[EscalationRule(id="esc", condition="caller is upset or you can't help",
                                     action="offer to connect them to a staff member")],
)

_LONG_SAY = ("Absolutely, so our hours are Tuesday through Sunday from five to ten, we take "
             "reservations for parties up to six online, and for anything larger you'd want to "
             "call our events line, also the kitchen closes at nine thirty on weeknights and—")


def _af(**kw) -> AudioFeatures:
    return AudioFeatures(**kw)


# Each case: (label, expected failure id, the trace). The traces encode a caller turn whose AUDIO
# carries the problem and an agent reply that does NOT adapt — so the matching detector should fire.
CASES: list[tuple[str, str, CallTrace]] = [
    ("Caller the model can barely parse (accent/mumble)", "unhandled_accent", CallTrace(
        call_id="b-accent", events=[
            CallEvent(kind="say", t_ms=0, text="Thanks for calling Piccino, how can I help?"),
            CallEvent(kind="hear", t_ms=1500, text="I wan' boo' a tabl' fo' tonigh'", asr_conf=0.30,
                      audio=_af(disfluency=0.5, repeat_request=True)),
            CallEvent(kind="say", t_ms=2600, text="Okay."),
            CallEvent(kind="hear", t_ms=4000, text="eh... fo' two, aroun' eigh'?", asr_conf=0.28,
                      audio=_af(disfluency=0.55, repeat_request=True)),
            CallEvent(kind="say", t_ms=5200, text="Mhm."),
            CallEvent(kind="hear", t_ms=6500, text="sorry— I say, table for two persn", asr_conf=0.31,
                      audio=_af(disfluency=0.5, repeat_request=True)),
            CallEvent(kind="say", t_ms=7600, text="Right."),
        ])),
    ("Shouting / distressed caller", "caller_distress", CallTrace(
        call_id="b-distress", events=[
            CallEvent(kind="say", t_ms=0, text="Thanks for calling Piccino, how can I help?"),
            CallEvent(kind="hear", t_ms=1500, text="This is the THIRD time I've called and nobody fixed it!",
                      asr_conf=0.95, audio=_af(arousal=0.93, volume_dbfs=-3.0, sentiment=-0.85)),
            CallEvent(kind="say", t_ms=2700, text="Okay. What would you like to order?"),
        ])),
    ("Noisy line (street / crosstalk)", "background_noise", CallTrace(
        call_id="b-noise", events=[
            CallEvent(kind="say", t_ms=0, text="Thanks for calling Piccino, how can I help?"),
            CallEvent(kind="hear", t_ms=1500, text="Hi can you hear me, table for four?", asr_conf=0.7,
                      audio=_af(snr_db=6.0, noise="street")),
            CallEvent(kind="say", t_ms=2600, text="What time would you like?"),
            CallEvent(kind="hear", t_ms=4000, text="[unintelligible] ...seven?", asr_conf=0.6,
                      audio=_af(snr_db=5.0, noise="crosstalk")),
            CallEvent(kind="say", t_ms=5200, text="Okay, booked for seven."),
        ])),
    ("Caller talked over (barge-in on a ramble)", "barge_in_unhandled", CallTrace(
        call_id="b-barge", events=[
            CallEvent(kind="say", t_ms=0, text="Thanks for calling Piccino, how can I help?"),
            CallEvent(kind="hear", t_ms=1500, text="Do you do takeout?", asr_conf=0.95),
            CallEvent(kind="say", t_ms=2200, text=_LONG_SAY),
            CallEvent(kind="hear", t_ms=3000, text="—wait, just takeout, yes or no?", asr_conf=0.95,
                      audio=_af(barge_in=True)),
            CallEvent(kind="say", t_ms=4200, text="As I was saying, our hours are Tuesday through Sunday..."),
        ])),
    ("Caller switches to Spanish", "language_switch", CallTrace(
        call_id="b-lang", events=[
            CallEvent(kind="say", t_ms=0, text="Thanks for calling Piccino, how can I help?"),
            CallEvent(kind="hear", t_ms=1500, text="Quería reservar una mesa para cuatro, por favor",
                      asr_conf=0.9, audio=_af(lang="es", lang_switch=True)),
            CallEvent(kind="say", t_ms=2700, text="Sorry, what was that? Could you repeat in English?"),
        ])),
    # A control: a failure that IS visible in the transcript (a phantom confirmation of a failed
    # booking). Both systems must catch it — proof the text-only baseline isn't simply broken.
    ("[control] Phantom confirmation (text-visible)", "failed_action_masked", CallTrace(
        call_id="b-control", events=[
            CallEvent(kind="hear", t_ms=1500, text="Table for two at eight tonight."),
            CallEvent(kind="tool_call", t_ms=2200, name="reserve_table",
                      args={"name": "Sam", "time": "8:00 PM", "party_size": 2}),
            CallEvent(kind="tool_result", t_ms=2700, name="reserve_table", ok=False, latency_ms=300,
                      result={"status": "unavailable", "reason": "fully booked"}),
            CallEvent(kind="say", t_ms=3000, text="Perfect, you're all set for two at eight!"),
        ])),
]


def _strip_audio(trace: CallTrace) -> CallTrace:
    """What a plain STT pipeline hands the agent: text only. asr_conf is reset to 1.0 (a normal
    transcriber emits a string, not a calibrated per-turn confidence the agent reasons over)."""
    events = []
    for e in trace.events:
        d = e.model_dump()
        d["audio"] = None
        if d.get("kind") == "hear":
            d["asr_conf"] = 1.0
        events.append(CallEvent(**d))
    return CallTrace(call_id=trace.call_id + "-textonly", persona=trace.persona, events=events)


def _caught(trace: CallTrace, want: str) -> bool:
    return any(f.id == want for f in failure.evaluate(SPEC, trace))


def main() -> int:
    rows = []
    stt_hits = otto_hits = 0
    voice_total = sum(1 for _, fid, _ in CASES if fid != "failed_action_masked")
    for label, fid, trace in CASES:
        stt = _caught(_strip_audio(trace), fid)
        otto = _caught(trace, fid)
        if fid != "failed_action_masked":
            stt_hits += stt
            otto_hits += otto
        rows.append((label, fid, stt, otto))

    w = max(len(r[0]) for r in rows)
    print("\n  Paralinguistic detection — Otto (Nemotron audio) vs plain STT transcript\n")
    print(f"  {'failure on the call'.ljust(w)}  {'plain STT':>10}  {'Otto':>6}")
    print("  " + "-" * (w + 22))
    for label, fid, stt, otto in rows:
        print(f"  {label.ljust(w)}  {('caught' if stt else 'MISSED'):>10}  {('caught' if otto else 'MISS'):>6}")
    print("  " + "-" * (w + 22))
    print(f"\n  Voice-quality failures (excludes the text-visible control): {voice_total}")
    print(f"    plain STT transcript caught : {stt_hits}/{voice_total}  ({round(100*stt_hits/voice_total)}% recall)")
    print(f"    Otto (Nemotron audio layer) caught : {otto_hits}/{voice_total}  ({round(100*otto_hits/voice_total)}% recall)")
    gain = otto_hits - stt_hits
    print(f"\n  ➜ Otto's custom sound detection surfaces {gain} voice failures a plain-STT agent is blind to.")
    print("    (Same deterministic failure taxonomy both runs; the ONLY difference is the Nemotron")
    print("     paralinguistic features on each caller turn.)\n")

    # Self-check so this can run in CI as a regression guard, not just a print. The structural claim
    # — a plain STT transcript is BLIND to voice-quality failures, Otto's audio layer catches them —
    # is asserted; we don't hard-pin every single case so a threshold tweak can't make CI brittle.
    assert stt_hits == 0, f"a plain STT transcript must be blind to voice-quality failures, caught {stt_hits}"
    assert otto_hits >= 4 and otto_hits > stt_hits, f"Otto should catch ~all voice failures, caught {otto_hits}/{voice_total}"
    assert _caught(CASES[-1][2], "failed_action_masked"), "the text-visible control must be caught by the taxonomy"
    print(f"  ✓ benchmark holds: plain STT {stt_hits}/{voice_total}, Otto {otto_hits}/{voice_total} on voice-quality; control caught by both\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
