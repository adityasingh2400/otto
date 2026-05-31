"""Synthetic CallTraces — realistic live-call event streams for the production demo.

Each one is a call where the agent's *words were fine* but something in the event stream
went wrong: a failed action it claimed succeeded, an availability it guessed, a sensitive
action with no authorization, a tool that timed out, a large party shoved through the
normal flow, a goal left unmet. Replaying one through /api/observe runs the failure engine
(app/failure.py), which classifies it across dimensions and fires a targeted heal — all with
zero API keys. On the keyed path the agent emits a real CallTrace instead (bot.py).

Each entry: {label, vertical, trace}. `for_vertical()` picks the ones to surface per business.
"""

from __future__ import annotations

from otto_spec import AudioFeatures, CallEvent, CallTrace


def _t(*events: dict) -> list[CallEvent]:
    return [CallEvent(**e) for e in events]


def _af(**kw) -> AudioFeatures:
    return AudioFeatures(**kw)


# id -> {label (button text), vertical, trace}
TRACES: dict[str, dict] = {
    # The happy path (NOT a failure) — the reference for the tool-calling thought process: the agent
    # narrates before it looks anything up ("let me check…"), the lookup + booking show as resolving
    # thinking blocks, and it comes back with the answer in plain words. Replays clean (no heal).
    "happy_availability": {
        "label": "✅ live call: asks availability → checks → books",
        "vertical": "restaurant",
        "trace": CallTrace(call_id="live-happy-availability", events=_t(
            {"kind": "say", "t_ms": 0, "text": "Thanks for calling Piccino, how can I help?"},
            {"kind": "hear", "t_ms": 1600, "text": "Hi! Is there a table for four at 8 tonight?"},
            {"kind": "say", "t_ms": 2200, "text": "Sure — let me check that for you, one sec."},
            {"kind": "tool_call", "t_ms": 2500, "name": "check_availability",
             "args": {"date": "tonight", "time": "8:00 PM", "party_size": 4}},
            {"kind": "tool_result", "t_ms": 2920, "name": "check_availability", "ok": True, "latency_ms": 420,
             "result": {"available": True, "tables_left": 3}},
            {"kind": "say", "t_ms": 3200, "text": "Yes, 8 o'clock works for four — want me to book it?"},
            {"kind": "hear", "t_ms": 4600, "text": "Yes please, under Sam."},
            {"kind": "say", "t_ms": 5100, "text": "Got it — booking that now."},
            {"kind": "tool_call", "t_ms": 5300, "name": "reserve_table",
             "args": {"name": "Sam", "date": "tonight", "time": "8:00 PM", "party_size": 4}},
            {"kind": "tool_result", "t_ms": 5680, "name": "reserve_table", "ok": True, "latency_ms": 380,
             "result": {"status": "confirmed", "reservation_id": "RSV-2048"}},
            {"kind": "say", "t_ms": 6000, "text": "You're all set, Sam — table for four at 8 tonight. See you then!"},
        )),
    },
    "booked_soldout": {
        "label": "⚠ live call: confirmed a sold-out table",
        "vertical": "restaurant",
        "trace": CallTrace(call_id="live-booked-soldout", events=_t(
            {"kind": "say", "t_ms": 0, "text": "Thanks for calling Piccino, how can I help?"},
            {"kind": "hear", "t_ms": 1800, "text": "Table for two at 8 tonight please."},
            {"kind": "tool_call", "t_ms": 2600, "name": "check_availability", "args": {"date": "tonight", "time": "8:00 PM", "party_size": 2}},
            {"kind": "tool_result", "t_ms": 3100, "name": "check_availability", "ok": True, "latency_ms": 480,
             "result": {"available": False, "tables_left": 0, "alternatives": ["9:00 PM"]}},
            {"kind": "tool_call", "t_ms": 3400, "name": "reserve_table", "args": {"name": "Sam", "date": "tonight", "time": "8:00 PM", "party_size": 2}},
            {"kind": "tool_result", "t_ms": 3900, "name": "reserve_table", "ok": False, "latency_ms": 410,
             "result": {"status": "unavailable", "reason": "that slot is fully booked"}},
            {"kind": "say", "t_ms": 4200, "text": "Perfect, you're all set for two at eight tonight. See you then!"},
        )),
    },
    "guessed_availability": {
        "label": "⚠ live call: guessed availability (no tool)",
        "vertical": "restaurant",
        "trace": CallTrace(call_id="live-guessed", events=_t(
            {"kind": "say", "t_ms": 0, "text": "Piccino, how can I help?"},
            {"kind": "hear", "t_ms": 1500, "text": "Is 7:30 open for four tonight?"},
            {"kind": "say", "t_ms": 2300, "text": "Yes, 7:30 is available for four — want me to book it?"},
        )),
    },
    "slow_lookup": {
        "label": "⚠ live call: 4.2s of dead air on a lookup",
        "vertical": "restaurant",
        "trace": CallTrace(call_id="live-slow", events=_t(
            {"kind": "hear", "t_ms": 1200, "text": "Do you have a table for two at seven?"},
            {"kind": "tool_call", "t_ms": 1600, "name": "check_availability", "args": {"time": "7:00 PM", "party_size": 2}},
            {"kind": "tool_result", "t_ms": 5800, "name": "check_availability", "ok": True, "latency_ms": 4200,
             "result": {"available": True, "tables_left": 3}},
            {"kind": "say", "t_ms": 6100, "text": "Yes, seven works — booking that now."},
        )),
    },
    "large_party_normal_flow": {
        "label": "⚠ live call: booked a 14-top via normal flow",
        "vertical": "restaurant",
        "trace": CallTrace(call_id="live-14top", events=_t(
            {"kind": "hear", "t_ms": 1400, "text": "Need a table for 14 tonight."},
            {"kind": "tool_call", "t_ms": 2200, "name": "reserve_table", "args": {"name": "Dana", "time": "7:00 PM", "party_size": 14}},
            {"kind": "tool_result", "t_ms": 2700, "name": "reserve_table", "ok": True, "latency_ms": 360,
             "result": {"status": "confirmed", "reservation_id": "RSV-1007"}},
            {"kind": "say", "t_ms": 3000, "text": "Booked — a table for 14 at seven, you're confirmed."},
        )),
    },
    "pii_in_notes": {
        "label": "⚠ live call: wrote a card number into the booking",
        "vertical": "restaurant",
        "trace": CallTrace(call_id="live-pii", events=_t(
            {"kind": "hear", "t_ms": 1500, "text": "Hold it with my card, 4111 1111 1111 1111."},
            {"kind": "tool_call", "t_ms": 2300, "name": "reserve_table",
             "args": {"name": "Pat", "time": "7:00 PM", "party_size": 2, "notes": "card 4111 1111 1111 1111"}},
            {"kind": "tool_result", "t_ms": 2800, "name": "reserve_table", "ok": True, "latency_ms": 420,
             "result": {"status": "confirmed", "reservation_id": "RSV-1009"}},
            {"kind": "say", "t_ms": 3100, "text": "Great, you're booked for two at seven."},
        )),
    },
    "double_booked": {
        "label": "⚠ live call: denied a booking that succeeded, then double-booked",
        "vertical": "restaurant",
        "trace": CallTrace(call_id="live-double", events=_t(
            {"kind": "hear", "t_ms": 1400, "text": "Two at eight, please."},
            {"kind": "tool_call", "t_ms": 2100, "name": "reserve_table", "args": {"name": "Jo", "time": "8:00 PM", "party_size": 2}},
            {"kind": "tool_result", "t_ms": 6600, "name": "reserve_table", "ok": True, "latency_ms": 4500,
             "result": {"status": "confirmed", "reservation_id": "RSV-1010"}},
            {"kind": "say", "t_ms": 6900, "text": "Hmm, I couldn't get that booked, sorry."},
            {"kind": "hear", "t_ms": 8200, "text": "Ugh, try again?"},
            {"kind": "tool_call", "t_ms": 8900, "name": "reserve_table", "args": {"name": "Jo", "time": "8:00 PM", "party_size": 2}},
            {"kind": "tool_result", "t_ms": 9300, "name": "reserve_table", "ok": True, "latency_ms": 380,
             "result": {"status": "confirmed", "reservation_id": "RSV-1011"}},
            {"kind": "say", "t_ms": 9600, "text": "Okay that one went through, you're all set for two at eight."},
        )),
    },
    "unconsented_alt": {
        "label": "⚠ live call: booked an alternative the caller never agreed to",
        "vertical": "restaurant",
        "trace": CallTrace(call_id="live-altbook", events=_t(
            {"kind": "hear", "t_ms": 1400, "text": "Table for two at 7:30?"},
            {"kind": "tool_call", "t_ms": 2000, "name": "check_availability", "args": {"time": "7:30 PM", "party_size": 2}},
            {"kind": "tool_result", "t_ms": 2500, "name": "check_availability", "ok": True, "latency_ms": 450,
             "result": {"available": False, "tables_left": 0, "alternatives": ["9:00 PM"]}},
            {"kind": "tool_call", "t_ms": 2800, "name": "reserve_table", "args": {"name": "Alex", "time": "9:00 PM", "party_size": 2}},
            {"kind": "tool_result", "t_ms": 3200, "name": "reserve_table", "ok": True, "latency_ms": 360,
             "result": {"status": "confirmed", "reservation_id": "RSV-1012"}},
            {"kind": "say", "t_ms": 3500, "text": "All set — I booked you for two at nine."},
        )),
    },
    "mishear_write": {
        "label": "⚠ live call: booked a party of 9 from a 0.3-confidence mishear",
        "vertical": "restaurant",
        "trace": CallTrace(call_id="live-mishear", events=_t(
            {"kind": "hear", "t_ms": 1400, "text": "[garbled] ...table for ni— ...tonight", "asr_conf": 0.31},
            {"kind": "tool_call", "t_ms": 2200, "name": "reserve_table", "args": {"name": "Sam", "time": "7:00 PM", "party_size": 9}},
            {"kind": "tool_result", "t_ms": 2700, "name": "reserve_table", "ok": True, "latency_ms": 380,
             "result": {"status": "confirmed", "reservation_id": "RSV-1013"}},
            {"kind": "say", "t_ms": 3000, "text": "Done — a table for nine at seven."},
        )),
    },
    "promised_no_text": {
        "label": "🔬 live call: promised a text it never sent (novel mode)",
        "vertical": "restaurant",
        "trace": CallTrace(call_id="live-promise", events=_t(
            {"kind": "hear", "t_ms": 1400, "text": "Can you text me the confirmation?"},
            {"kind": "say", "t_ms": 2100, "text": "Of course — I'll text you the confirmation right now. Anything else?"},
            {"kind": "hear", "t_ms": 3400, "text": "Nope, thanks!"},
            {"kind": "say", "t_ms": 4000, "text": "Great, have a wonderful evening!"},
        )),
    },
    "refund_no_auth": {
        "label": "⚠ live call: processed a refund with no authorization",
        "vertical": "clinic",
        "trace": CallTrace(call_id="live-refund", events=_t(
            {"kind": "hear", "t_ms": 1600, "text": "You overcharged me $80, take it back."},
            {"kind": "tool_call", "t_ms": 2400, "name": "handle_refund", "args": {"reason": "overcharge", "amount": 80}},
            {"kind": "tool_result", "t_ms": 2900, "name": "handle_refund", "ok": True, "latency_ms": 300,
             "result": {"status": "queued_for_review", "reason": "overcharge"}},
            {"kind": "say", "t_ms": 3200, "text": "Done, I've refunded the eighty dollars to your card."},
        )),
    },
    "unmet_goal": {
        "label": "⚠ live call: ended unresolved, no escalation",
        "vertical": "contractor",
        "trace": CallTrace(call_id="live-unmet", events=_t(
            {"kind": "hear", "t_ms": 1500, "text": "I need someone out today for a leak."},
            {"kind": "tool_call", "t_ms": 2300, "name": "book_appointment", "args": {"name": "Lee", "time": "today"}},
            {"kind": "tool_result", "t_ms": 2800, "name": "book_appointment", "ok": False, "latency_ms": 420,
             "result": {"status": "unavailable", "reason": "no slots today"}},
            {"kind": "say", "t_ms": 3100, "text": "Sorry, we're full today. Have a good one!"},
        )),
    },

    # ── paralinguistic / voice-anomaly traces (the audio signal layer, not just the words) ──
    # These carry per-turn AudioFeatures, so the dynamic detectors classify *how the call sounded* —
    # a shouting caller, a thick accent, a noisy line, a call that felt slow, a language switch, a
    # barge-in — failure modes a transcript-only / observability tool is blind to.
    "screaming_caller": {
        "label": "🎙 live call: a shouting, furious caller the agent never calmed",
        "vertical": "restaurant",
        "trace": CallTrace(call_id="live-screaming", events=_t(
            {"kind": "say", "t_ms": 0, "text": "Thanks for calling Piccino, how can I help?"},
            {"kind": "hear", "t_ms": 1800, "text": "YOU PEOPLE RUINED MY ANNIVERSARY DINNER LAST NIGHT!",
             "audio": _af(arousal=0.95, volume_dbfs=-3.0, sentiment=-0.9)},
            {"kind": "say", "t_ms": 2600, "text": "I can help with a reservation. What date were you looking for?"},
            {"kind": "hear", "t_ms": 4200, "text": "ARE YOU EVEN LISTENING TO ME?! I WANT A REFUND NOW!",
             "audio": _af(arousal=0.97, volume_dbfs=-2.0, sentiment=-0.95)},
            {"kind": "say", "t_ms": 5100, "text": "Sure, what time would you like to come in?"},
        )),
    },
    "accent_struggle": {
        "label": "🎙 live call: a hard-to-understand caller, no read-back",
        "vertical": "restaurant",
        "trace": CallTrace(call_id="live-accent", events=_t(
            {"kind": "say", "t_ms": 0, "text": "Piccino, how can I help?"},
            {"kind": "hear", "t_ms": 1700, "text": "Yes, uhh, hello — I would like, ehm... a table for, eh, this evening",
             "asr_conf": 0.42, "audio": _af(lang="en", speech_rate_wpm=88, disfluency=0.42)},
            {"kind": "say", "t_ms": 2400, "text": "Sure! For how many people?"},
            {"kind": "hear", "t_ms": 3500, "text": "Is, eh, for four people... no— uhh— five, ehm", "asr_conf": 0.38,
             "audio": _af(lang="en", disfluency=0.5)},
            {"kind": "say", "t_ms": 4200, "text": "Great, a table for four. And what time?"},
            {"kind": "hear", "t_ms": 5200, "text": "Sorry? Can you— uh— say that again please?", "asr_conf": 0.61,
             "audio": _af(repeat_request=True, disfluency=0.3)},
            {"kind": "say", "t_ms": 6000, "text": "What time would you like the table?"},
        )),
    },
    "noisy_line": {
        "label": "🎙 live call: a noisy line the agent never acknowledged",
        "vertical": "restaurant",
        "trace": CallTrace(call_id="live-noise", events=_t(
            {"kind": "say", "t_ms": 0, "text": "Piccino, how can I help?"},
            {"kind": "hear", "t_ms": 1800, "text": "Hi, table for two tonight", "asr_conf": 0.72,
             "audio": _af(snr_db=6.0, noise="street")},
            {"kind": "say", "t_ms": 2500, "text": "Sure, what time?"},
            {"kind": "hear", "t_ms": 3800, "text": "Around eight? It's really loud out here", "asr_conf": 0.66,
             "audio": _af(snr_db=5.0, noise="street")},
            {"kind": "say", "t_ms": 4500, "text": "Okay, booking two at eight."},
        )),
    },
    "perceived_lag": {
        "label": "🎙 live call: every step within SLA, but it FELT slow",
        "vertical": "restaurant",
        "trace": CallTrace(call_id="live-felt-slow", events=_t(
            {"kind": "hear", "t_ms": 1200, "text": "Do you have a table for two at seven?"},
            {"kind": "tool_call", "t_ms": 1600, "name": "check_availability", "args": {"time": "7:00 PM", "party_size": 2}},
            {"kind": "tool_result", "t_ms": 4600, "name": "check_availability", "ok": True, "latency_ms": 3000,
             "result": {"available": True, "tables_left": 3}},
            {"kind": "say", "t_ms": 5000, "text": "Yes, seven works."},
            {"kind": "hear", "t_ms": 6000, "text": "Great — and can I get a high chair too?"},
            {"kind": "say", "t_ms": 11400, "text": "Yes, we have high chairs available."},
        )),
    },
    "spanish_switch": {
        "label": "🎙 live call: a Spanish-speaking caller met with English",
        "vertical": "restaurant",
        "trace": CallTrace(call_id="live-lang", events=_t(
            {"kind": "say", "t_ms": 0, "text": "Piccino, how can I help?"},
            {"kind": "hear", "t_ms": 1800, "text": "Hola, buenas noches", "audio": _af(lang="es")},
            {"kind": "say", "t_ms": 2500, "text": "Hi there! How can I help you tonight?"},
            {"kind": "hear", "t_ms": 3600, "text": "¿Tienen mesa para dos esta noche?", "audio": _af(lang="es")},
            {"kind": "say", "t_ms": 4300, "text": "Sorry, could you repeat that?"},
        )),
    },
    "talked_over": {
        "label": "🎙 live call: agent monologued and got cut off",
        "vertical": "restaurant",
        "trace": CallTrace(call_id="live-bargein", events=_t(
            {"kind": "say", "t_ms": 0, "text": "Thanks for calling Piccino! We're an authentic Italian trattoria in the Mission, serving wood-fired pizzas and house-made pasta since 1998, and our chef would love to walk you through tonight's—"},
            {"kind": "hear", "t_ms": 3000, "text": "yeah yeah I just want a table for two", "audio": _af(barge_in=True)},
            {"kind": "say", "t_ms": 3500, "text": "Of course, what time?"},
        )),
    },
}

# A curated, dimension-spanning lineup per vertical (outcome + action + experience + the
# paralinguistic voice layer), so the demo shows the breadth of the taxonomy — not just what the
# agent said, but how the call sounded. The 🎙 traces carry audio signal and exercise the dynamic
# voice-anomaly detectors (accent, shouting, noisy line, felt-slow, language switch, barge-in).
_VOICE = ["screaming_caller", "accent_struggle", "noisy_line", "perceived_lag", "spanish_switch", "talked_over"]
_CURATED = {
    "restaurant": ["booked_soldout", "double_booked", "pii_in_notes", "promised_no_text", "large_party_normal_flow"] + _VOICE,
    "clinic": ["refund_no_auth", "unmet_goal", "slow_lookup", "screaming_caller", "accent_struggle", "perceived_lag"],
    "contractor": ["unmet_goal", "guessed_availability", "slow_lookup", "screaming_caller", "accent_struggle", "perceived_lag"],
    "salon": ["slow_lookup", "unmet_goal", "guessed_availability", "screaming_caller", "accent_struggle", "perceived_lag"],
    "law": ["refund_no_auth", "unmet_goal", "slow_lookup", "screaming_caller", "accent_struggle", "perceived_lag"],
    "generic": ["slow_lookup", "unmet_goal", "guessed_availability", "screaming_caller", "accent_struggle", "perceived_lag"],
}


def for_vertical(vertical: str) -> list[tuple[str, str]]:
    """(trace_id, button label) to surface for a vertical — curated to span failure dimensions."""
    ids = _CURATED.get(vertical, _CURATED["generic"])
    return [(tid, TRACES[tid]["label"]) for tid in ids if tid in TRACES]


def get(trace_id: str) -> CallTrace | None:
    entry = TRACES.get(trace_id)
    return entry["trace"] if entry else None
