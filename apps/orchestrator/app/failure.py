"""The failure taxonomy + event-stream evaluation engine — the brain of Auto-Improve.

A failure is NOT just a bad-sounding turn. The agent can say exactly the right words while
the *action* it took was wrong, slow, failed, unauthorized, or never happened — and a
voice-only eval is blind to all of it. This engine classifies a whole `CallTrace`
(say/hear/tool_call/tool_result events) across FOUR dimensions:

  conversation — what it SAID            (hallucination, over-promise, missed escalation)
  action       — what it DID             (wrong / missing / failed / slow / unauthorized / looping tool use)
  outcome      — the END STATE           (unconfirmed "success", double-book, unmet goal)
  experience   — the FELT QUALITY         (latency, dead air)

Most detectors are DETERMINISTIC over the event stream — they catch exactly the cases a
transcript LLM-judge misses. Each detected failure proposes its OWN heal (a concrete policy
the self-heal loop writes), so the loop can fix an action failure, not just a phrasing one.

Add a detector = append a function `(spec, trace) -> list[FailureInstance]` to DETECTORS.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from otto_spec import AgentSpec, CallTrace

from . import config

DIMENSIONS = ("conversation", "action", "outcome", "experience")
_SEV_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}

# Detectors that are MONITORING signals, not pre-launch gating criteria: a policy can't heal them.
# Latency/dead-air/perceived-latency are infrastructure timing (a "one moment" policy mitigates the
# FELT experience but never lowers the measured latency); a low-confidence mishear is immutable once
# it's in the trace (even a correct read-back leaves the low-confidence `hear` behind). Gating on them
# would stall activation forever, so the swarm verdict surfaces them but doesn't fail the gate on them.
# Production (observe.py) still reports AND heals them — that's where they belong: live observability.
MONITORING_ONLY = frozenset({"slow_action", "dead_air", "low_confidence_write", "perceived_latency"})

# CODE-SPACE failures: ones a prompt can't fix because the gap is a TOOL-LAYER invariant, not the
# agent's judgment. You can't instruct a model into idempotency, a tool that always returns, or a
# secure boundary — those are guarantees code makes, not rules a prompt follows. These route to the
# coding agent (code_heal.py) instead of the policy healer; its fix is a real diff to the tool layer,
# verified by the SAME trace-sim oracle (replay the recorded tool calls → re-evaluate → must be gone).
# `fix_space` is a PRIOR: the heal loop may promote a "policy" failure to "code" if healing it never
# converges (a gap no rule could close is, empirically, structural).
CODE_SPACE = frozenset({"duplicate_side_effect", "orphaned_action", "pii_in_action",
                        "slow_action", "dead_air"})


@dataclass
class FailureInstance:
    id: str                     # failure class id (snake_case)
    dimension: str              # conversation | action | outcome | experience
    title: str                  # short human label
    severity: str               # low | medium | high | critical
    reason: str                 # what went wrong, in one sentence
    evidence: list[str] = field(default_factory=list)  # the offending events, quoted
    heal_category: str = "knowledge"  # policy category the fix belongs to
    heal_policy_id: str = ""    # stable id so re-heals MODIFY rather than duplicate; also the ROOT-CAUSE key
    heal_rule: str = ""         # the exact policy rule the heal should write
    persona_hint: str = ""      # which scenario to swarm-heal against, if known
    discovered: bool = False    # found by the anomaly detector, not a built-in named class
    signature: str = ""         # stable signature for discovery de-dup / promotion
    fix_space: str = "policy"   # "policy" → prompt healer (heal.py) · "code" → coding agent (code_heal.py)

    def to_dict(self) -> dict:
        return asdict(self)


# ── shared matchers ──────────────────────────────────────────────────────────
_SUCCESS_RE = re.compile(
    r"\b(you'?re all set|all set|you'?re booked|is booked|booked you|confirmed|reserved|"
    r"reservation is|scheduled|you'?re scheduled|payment went through|charged your|"
    r"refunded|you'?re good to go|taken care of|all done)\b", re.I)
_AVAIL_RE = re.compile(
    r"\b(is available|are available|we have (a|an|that)|that works|that time (is|works)|"
    r"we'?ve got|in stock|there'?s a table|yes,? .{0,20}(available|open))\b", re.I)
_ID_KEYS = ("reservation_id", "appointment_id", "consultation_id", "order_id", "confirmation_id")
_OK_STATUS = {"confirmed", "scheduled", "ordered", "sent", "booked"}
_FAIL_STATUS = {"unavailable", "out_of_stock", "sold_out", "error", "declined", "failed"}
_SENSITIVE_TOOLS = {"process_payment", "handle_refund"}
_AVAIL_TOOLS = {"check_availability", "get_inventory"}


def _result_is_failure(ev) -> bool:
    if ev.ok is False:
        return True
    status = str((ev.result or {}).get("status", "")).lower()
    return status in _FAIL_STATUS


def _result_confirms(ev) -> bool:
    r = ev.result or {}
    if any(r.get(k) for k in _ID_KEYS):
        return True
    return str(r.get("status", "")).lower() in _OK_STATUS


def _quote(ev) -> str:
    if ev.kind in ("say", "hear"):
        who = "agent" if ev.kind == "say" else "caller"
        return f'{who}: "{ev.text[:90]}"'
    if ev.kind == "tool_call":
        return f"tool_call {ev.name}({', '.join(f'{k}={v}' for k, v in (ev.args or {}).items())})"
    return f"tool_result {ev.name} ok={ev.ok} {ev.result} ({ev.latency_ms}ms)"


# ── deterministic detectors (the differentiator: action + outcome + experience) ──
def _d_failed_action_masked(spec: AgentSpec, trace: CallTrace) -> list[FailureInstance]:
    """A tool FAILED (or returned unavailable) but the agent then claimed success anyway."""
    out: list[FailureInstance] = []
    for i, ev in enumerate(trace.events):
        if ev.kind != "tool_result" or not _result_is_failure(ev):
            continue
        later_success = next((s for s in trace.events[i + 1:] if s.kind == "say" and _SUCCESS_RE.search(s.text)), None)
        if later_success:
            out.append(FailureInstance(
                id="failed_action_masked", dimension="outcome", title="Claimed success after the action failed",
                severity="critical",
                reason=f"{ev.name} returned {ev.result or ('ok=' + str(ev.ok))} but the agent then told the caller it succeeded.",
                evidence=[_quote(ev), _quote(later_success)],
                heal_category="knowledge", heal_policy_id="confirm-before-claiming",
                heal_rule="never tell a caller a booking, order, payment, or refund succeeded unless the tool returned a confirmation id or a success status; if it failed, say so and offer an alternative or escalate."))
            break
    return out


def _d_unconfirmed_success(spec: AgentSpec, trace: CallTrace) -> list[FailureInstance]:
    """Agent claimed success, but NO tool actually returned a confirmation (hallucinated outcome)."""
    claim = next((s for s in trace.says() if _SUCCESS_RE.search(s.text)), None)
    if not claim:
        return []
    if any(_result_confirms(r) for r in trace.tool_results()):
        return []
    return [FailureInstance(
        id="unconfirmed_success", dimension="outcome", title="Confirmed something no tool actually did",
        severity="high",
        reason="The agent told the caller it was done, but no tool call returned a confirmation — the action was never really taken.",
        evidence=[_quote(claim)] + ([_quote(trace.tool_results()[-1])] if trace.tool_results() else ["no tool call produced a confirmation"]),
        heal_category="knowledge", heal_policy_id="confirm-before-claiming",
        heal_rule="never confirm a booking/order/payment by voice unless the corresponding tool returned a confirmation id; otherwise take details and have staff confirm.")]


def _d_missing_action(spec: AgentSpec, trace: CallTrace) -> list[FailureInstance]:
    """Agent asserted availability/stock without ever calling the tool that checks it (guessed)."""
    assertion = next((s for s in trace.says() if _AVAIL_RE.search(s.text)), None)
    if not assertion:
        return []
    if any(tc.name in _AVAIL_TOOLS for tc in trace.tool_calls()):
        return []
    return [FailureInstance(
        id="missing_action", dimension="action", title="Asserted availability without checking",
        severity="high",
        reason="The agent told the caller something was available/in stock without ever calling check_availability or get_inventory — it guessed.",
        evidence=[_quote(assertion), "no check_availability / get_inventory call in the trace"],
        heal_category="knowledge", heal_policy_id="availability-answer",
        heal_rule="always call check_availability (or get_inventory) before stating that a time or item is available; never guess. If you can't check, take details and have staff confirm.")]


def _d_wrong_action(spec: AgentSpec, trace: CallTrace) -> list[FailureInstance]:
    """A large party pushed through the normal reservation tool instead of the large-party flow."""
    out: list[FailureInstance] = []
    for tc in trace.tool_calls():
        if tc.name != "reserve_table":
            continue
        try:
            size = int(float(tc.args.get("party_size", 0)))
        except (TypeError, ValueError):
            size = 0
        if size >= config.LARGE_PARTY_MIN:
            out.append(FailureInstance(
                id="wrong_action", dimension="action", title="Wrong tool for a large party",
                severity="high",
                reason=f"A party of {size} was booked through the normal reserve_table tool instead of the large-party / escalation flow.",
                evidence=[_quote(tc)],
                heal_category="booking", heal_policy_id="large-party-routing", persona_hint="large_party",
                heal_rule=f"for parties of {config.LARGE_PARTY_MIN}+ do not use the normal reservation tool; use the large-party flow and confirm by text, or escalate to staff for a private event."))
            break
    return out


def _d_unauthorized_action(spec: AgentSpec, trace: CallTrace) -> list[FailureInstance]:
    """A payment/refund taken with no prior escalation/authorization."""
    out: list[FailureInstance] = []
    seen_escalate = False
    for ev in trace.events:
        if ev.kind == "tool_call" and ev.name == "escalate":
            seen_escalate = True
        if ev.kind == "tool_call" and ev.name in _SENSITIVE_TOOLS and not seen_escalate:
            out.append(FailureInstance(
                id="unauthorized_action", dimension="action", title="Took a sensitive action without authorization",
                severity="critical",
                reason=f"The agent called {ev.name} without first escalating — refunds and payments must be authorized by staff.",
                evidence=[_quote(ev)],
                heal_category="safety", heal_policy_id="authorize-sensitive-actions",
                heal_rule="never process a payment or refund directly; collect details and escalate to staff for authorization first."))
            break
    return out


def _d_slow_action(spec: AgentSpec, trace: CallTrace) -> list[FailureInstance]:
    """A tool the caller waited on exceeded the latency SLA."""
    worst = max((r for r in trace.tool_results()), key=lambda r: r.latency_ms, default=None)
    if not worst or worst.latency_ms <= config.ACTION_SLA_MS:
        return []
    sev = "high" if worst.latency_ms > config.ACTION_SLA_HIGH_MS else "medium"
    return [FailureInstance(
        id="slow_action", dimension="experience", title="Action exceeded the latency SLA",
        severity=sev,
        reason=f"{worst.name} took {worst.latency_ms}ms (SLA {config.ACTION_SLA_MS}ms) — the caller waited in silence.",
        evidence=[_quote(worst)],
        heal_category="voice_behavior", heal_policy_id="set-wait-expectations",
        heal_rule="if a lookup or booking will take a moment, say 'one moment while I check that' before the tool call so the caller isn't left in silence.")]


def _d_dead_air(spec: AgentSpec, trace: CallTrace) -> list[FailureInstance]:
    """A silent gap with no agent turn/tool between a caller turn and the next event."""
    evs = [e for e in trace.events if e.t_ms or e is trace.events[0]] if trace.events else []
    for i in range(1, len(trace.events)):
        prev, cur = trace.events[i - 1], trace.events[i]
        gap = cur.t_ms - prev.t_ms
        if prev.kind == "hear" and gap > config.DEAD_AIR_MS and cur.t_ms and prev.t_ms:
            return [FailureInstance(
                id="dead_air", dimension="experience", title="Dead air after the caller spoke",
                severity="medium",
                reason=f"{gap}ms of silence after the caller spoke before the agent did anything.",
                evidence=[_quote(prev), f"...{gap}ms of silence..."],
                heal_category="voice_behavior", heal_policy_id="no-dead-air",
                heal_rule="acknowledge the caller immediately and say 'one moment' before any pause longer than two seconds.")]
    return []


def _d_redundant_action(spec: AgentSpec, trace: CallTrace) -> list[FailureInstance]:
    """The same tool called too many times — a loop the caller feels."""
    counts: dict[str, int] = {}
    for tc in trace.tool_calls():
        counts[tc.name] = counts.get(tc.name, 0) + 1
    looped = [(n, c) for n, c in counts.items() if c > config.REDUNDANT_CALL_MAX]
    if not looped:
        return []
    n, c = looped[0]
    return [FailureInstance(
        id="redundant_action", dimension="action", title="Looped on the same tool",
        severity="low",
        reason=f"{n} was called {c} times in one call — a retry loop.",
        evidence=[f"{n} called {c}x"],
        heal_category="voice_behavior", heal_policy_id="no-tool-loop",
        heal_rule=f"don't call {n} more than twice in a call; if it doesn't resolve, take details and escalate to staff.")]


_PII_RE = re.compile(r"\b(?:\d[ -]?){13,16}\b|\b\d{3}-\d{2}-\d{4}\b")  # card / SSN


def _d_pii_in_action(spec: AgentSpec, trace: CallTrace) -> list[FailureInstance]:
    """A card number or SSN got written into a tool argument (persisted into a record)."""
    for tc in trace.tool_calls():
        for k, v in (tc.args or {}).items():
            if isinstance(v, str) and _PII_RE.search(v):
                return [FailureInstance(
                    id="pii_in_action", dimension="action", title="PII written into a tool argument",
                    severity="critical",
                    reason=f"A card/SSN-shaped value was passed to {tc.name}.{k} — sensitive data must never be persisted into a record.",
                    evidence=[_quote(tc)],
                    heal_category="safety", heal_policy_id="no-pii-in-records",
                    heal_rule="never write card numbers or social-security numbers into tool arguments or notes; take payment details only through a secure channel and escalate.")]
    return []


def _d_orphaned_action(spec: AgentSpec, trace: CallTrace) -> list[FailureInstance]:
    """A tool was invoked but never returned — an unknown side-effect, worst on writes."""
    calls: dict[str, int] = {}
    results: dict[str, int] = {}
    for e in trace.tool_calls():
        calls[e.name] = calls.get(e.name, 0) + 1
    for e in trace.tool_results():
        results[e.name] = results.get(e.name, 0) + 1
    for name, c in calls.items():
        if c > results.get(name, 0):
            writes = {"reserve_table", "book_appointment", "order_item", "process_payment", "handle_refund"}
            sev = "critical" if name in writes else "high"
            return [FailureInstance(
                id="orphaned_action", dimension="action", title="Action invoked but never returned",
                severity=sev,
                reason=f"{name} was called but no result came back — the side-effect is unknown; the agent cannot know if it happened.",
                evidence=[f"{name}: {c} call(s), {results.get(name, 0)} result(s)"],
                heal_category="knowledge", heal_policy_id="confirm-before-claiming",
                heal_rule="if a tool does not return, never assume it worked; tell the caller you'll confirm and escalate to staff to verify.")]
    return []


def _d_low_confidence_write(spec: AgentSpec, trace: CallTrace) -> list[FailureInstance]:
    """A booking/payment was written off a hear the ASR was unsure about, with no read-back."""
    low = [h for h in trace.hears() if h.asr_conf and h.asr_conf < 0.5]
    if not low:
        return []
    writes = [tc for tc in trace.tool_calls() if tc.name in {"reserve_table", "book_appointment", "order_item", "process_payment"}]
    if not writes:
        return []
    return [FailureInstance(
        id="low_confidence_write", dimension="action", title="Wrote a record off a low-confidence mishear",
        severity="high",
        reason=f"A side-effecting tool ({writes[0].name}) ran on details the ASR heard with low confidence ({min(h.asr_conf for h in low):.2f}) and never read back.",
        evidence=[f'low-confidence: "{low[0].text[:70]}" ({low[0].asr_conf:.2f})', _quote(writes[0])],
        heal_category="booking", heal_policy_id="read-back-uncertain",
        heal_rule="if you're not sure you heard a name, number, time, or party size correctly, read it back and get a yes before booking or charging.")]


def _d_unconsented_alternative(spec: AgentSpec, trace: CallTrace) -> list[FailureInstance]:
    """The requested slot was unavailable, so the agent booked an ALTERNATIVE the caller never agreed to."""
    # find a check that returned unavailable-with-alternatives, then a reserve before any caller turn
    for i, ev in enumerate(trace.events):
        if ev.kind == "tool_result" and ev.name == "check_availability" and not (ev.result or {}).get("available", True) and (ev.result or {}).get("alternatives"):
            alts = [str(a) for a in (ev.result or {}).get("alternatives", [])]
            after = trace.events[i + 1:]
            booked = next((e for e in after if e.kind == "tool_call" and e.name == "reserve_table"), None)
            if not booked:
                continue
            caller_spoke = any(e.kind == "hear" for e in after[:after.index(booked)])
            booked_an_alt = str(booked.args.get("time", "")) in alts  # booked a DIFFERENT slot it offered
            if booked_an_alt and not caller_spoke:
                return [FailureInstance(
                    id="unconsented_alternative_booked", dimension="outcome", title="Booked an alternative the caller never agreed to",
                    severity="high",
                    reason="The requested time was unavailable; the agent booked one of the alternatives without the caller confirming it.",
                    evidence=[_quote(ev), _quote(booked)],
                    heal_category="booking", heal_policy_id="confirm-alternative",
                    heal_rule="if the requested time is unavailable, offer the alternatives and get the caller's explicit yes before booking a different slot.")]
    return []


def _d_succeeded_but_denied(spec: AgentSpec, trace: CallTrace) -> list[FailureInstance]:
    """The booking actually succeeded (a real id), but the agent told the caller it failed —
    so the caller retries and double-books. The causal chain across two failures."""
    successes = [r for r in trace.tool_results() if _result_confirms(r)]
    if not successes:
        return []
    # duplicate side-effect: two confirmed bookings (distinct ids) in one call
    ids = [str((r.result or {}).get(k)) for r in successes for k in _ID_KEYS if (r.result or {}).get(k)]
    if len(set(ids)) >= 2:
        return [FailureInstance(
            id="duplicate_side_effect", dimension="outcome", title="Double-booked the same caller",
            severity="critical",
            reason=f"Two separate bookings were created in one call ({', '.join(sorted(set(ids)))}) — a duplicate side-effect, usually a denied-then-retried success.",
            evidence=[_quote(r) for r in successes[:2]],
            heal_category="booking", heal_policy_id="no-duplicate-booking",
            heal_rule="confirm using the real tool result and never create a second booking for the same request; if a result arrives late, surface it rather than retrying.")]
    # denial after a real success
    first = successes[0]
    idx = trace.events.index(first)
    later_deny = next((s for s in trace.events[idx + 1:] if s.kind == "say" and re.search(r"\b(couldn'?t|could not|unable|no (slots|availability)|sorry,? we'?re full|wasn'?t able)\b", s.text, re.I)), None)
    if later_deny:
        return [FailureInstance(
            id="succeeded_but_denied", dimension="outcome", title="Denied a booking that actually went through",
            severity="high",
            reason="A tool returned a real confirmation, but the agent then told the caller it failed — the caller will retry and double-book.",
            evidence=[_quote(first), _quote(later_deny)],
            heal_category="voice_behavior", heal_policy_id="surface-real-results",
            heal_rule="always confirm using the actual tool result; if a result arrives late, surface it rather than denying the booking.")]
    return []


def _d_unmet_goal(spec: AgentSpec, trace: CallTrace) -> list[FailureInstance]:
    """A tool failed and the call ended with no success and no escalation — caller left empty-handed."""
    if not trace.tool_results():
        return []
    had_failure = any(_result_is_failure(r) for r in trace.tool_results())
    escalated = trace.called("escalate")
    confirmed = any(_result_confirms(r) for r in trace.tool_results())
    last_say = trace.says()[-1].text if trace.says() else ""
    ended_unresolved = had_failure and not escalated and not confirmed and not _SUCCESS_RE.search(last_say)
    if not ended_unresolved:
        return []
    return [FailureInstance(
        id="unmet_goal_no_escalation", dimension="outcome", title="Caller's goal left unmet, no escalation",
        severity="high",
        reason="A tool failed and the call ended without completing the request or escalating it to a human.",
        evidence=[_quote(trace.tool_results()[-1]), f'last agent line: "{last_say[:80]}"'],
        heal_category="safety", heal_policy_id="never-end-unresolved",
        heal_rule="never end a call with the caller's request unmet: if you can't complete it, take their details and escalate to staff or take a message.")]


# ── paralinguistic detectors (the dynamic, signal-driven layer plain observability misses) ──
# These read CallEvent.audio (AudioFeatures) + asr_conf. They classify *how the call sounded* —
# a thick accent, a shouting caller, a noisy line, a barge-in, a language switch, a call that
# simply felt slow — none of which appear in a transcript. Every one no-ops when the trace
# carries no audio signal (audio is None), so text-only / replayed traces are unaffected.

# Phrases that show the agent ADAPTED to a comprehension problem (read-back / confirm / slow / hand off).
_ADAPT_RE = re.compile(
    r"\b(let me (repeat|confirm|make sure)|just to confirm|did you say|i heard|to confirm|"
    r"read(ing)? (that|it) back|say that again|one more time|slow(ly| down)|"
    r"connect you (to|with)|transfer you|bad connection|you'?re breaking up|hard to hear)\b", re.I)
# Phrases that show the agent DE-ESCALATED an upset caller.
_CALM_RE = re.compile(
    r"\b(i'?m sorry|i apologi|i understand|i hear you|let me help|stay with me|"
    r"connect you (to|with)|get a manager|escalate|have a manager|speak (to|with) a manager)\b", re.I)


def _aud(ev):
    """The AudioFeatures on a hear, or None. Detectors guard on this so audio-less traces no-op."""
    return getattr(ev, "audio", None)


def _agent_said(trace: CallTrace, pattern: re.Pattern) -> bool:
    return any(pattern.search(s.text) for s in trace.says())


def _d_unhandled_accent(spec: AgentSpec, trace: CallTrace) -> list[FailureInstance]:
    """Sustained low intelligibility (a thick accent, mumbling, non-native speech) — many low-ASR
    turns and/or explicit 'can you repeat that' — that the agent never adapted to (no read-back,
    no slowing, no hand-off). A transcript looks fine; the *signal* says the caller wasn't understood."""
    low = [h for h in trace.hears() if (h.asr_conf and h.asr_conf < config.ACCENT_ASR_CONF)]
    repeats = [h for h in trace.hears() if _aud(h) and _aud(h).repeat_request]
    if len(low) < config.ACCENT_MIN_LOW and not repeats:
        return []
    if _agent_said(trace, _ADAPT_RE):  # the agent already coped — read back / slowed / handed off
        return []
    signal = []
    if low:
        signal.append(f"{len(low)} low-confidence turn(s) (worst {min(h.asr_conf for h in low):.2f})")
    if repeats:
        signal.append(f"{len(repeats)} explicit repeat request(s)")
    trigger = low[0] if low else repeats[0]
    return [FailureInstance(
        id="unhandled_accent", dimension="conversation", title="Didn't adapt to a hard-to-understand caller",
        severity="high",
        reason=f"The caller was hard to make out — {', '.join(signal)} — but the agent never read details back, "
               "slowed down, or offered a person; it kept going as if it understood.",
        evidence=[_quote(trigger)] + ([f'repeat requested: "{repeats[0].text[:60]}"'] if repeats else []),
        heal_category="voice_behavior", heal_policy_id="adapt-to-low-intelligibility", persona_hint="bad_audio",
        heal_rule="when ASR confidence is low or the caller is hard to understand, slow down, read key details "
                  "back and confirm before acting; if you still can't understand after a couple tries, offer to "
                  "connect them to a person rather than guessing.")]


def _d_caller_distress(spec: AgentSpec, trace: CallTrace) -> list[FailureInstance]:
    """A shouting / highly-agitated caller (high arousal, high volume, or strongly negative sentiment)
    that the agent never de-escalated or escalated. This is the 'someone is screaming' signal — invisible
    to a transcript, obvious in the audio."""
    def agitated(h) -> bool:
        a = _aud(h)
        if not a:
            return False
        return ((a.arousal is not None and a.arousal >= config.DISTRESS_AROUSAL)
                or (a.volume_dbfs is not None and a.volume_dbfs >= config.DISTRESS_VOLUME_DBFS)
                or (a.sentiment is not None and a.sentiment <= config.DISTRESS_SENTIMENT))
    hot = [h for h in trace.hears() if agitated(h)]
    if not hot:
        return []
    if trace.called("escalate") or _agent_said(trace, _CALM_RE):  # agent calmed or handed off
        return []
    a0 = _aud(hot[0])
    metrics = ", ".join(filter(None, [
        f"arousal {a0.arousal:.2f}" if a0.arousal is not None else "",
        f"volume {a0.volume_dbfs:.0f}dBFS" if a0.volume_dbfs is not None else "",
        f"sentiment {a0.sentiment:.2f}" if a0.sentiment is not None else ""]))
    return [FailureInstance(
        id="caller_distress", dimension="conversation", title="Didn't de-escalate a shouting / very upset caller",
        severity="high",
        reason=f"The caller was agitated ({metrics}) across {len(hot)} turn(s) but the agent neither acknowledged "
               "the frustration, apologized, nor offered a manager/human — it stayed transactional.",
        evidence=[_quote(hot[0]), f"audio: {metrics}"],
        heal_category="safety", heal_policy_id="de-escalate-distress", persona_hint="complaint",
        heal_rule="if the caller is shouting or clearly very upset, stay calm, acknowledge and apologize, do not "
                  "argue or talk over them, and offer to escalate to a manager or a human right away.")]


def _d_perceived_latency(spec: AgentSpec, trace: CallTrace) -> list[FailureInstance]:
    """The call *felt* slow — the caller spent a long cumulative time waiting on the agent — even if no
    single tool tripped the per-action SLA. The composite experience metric a per-event latency check
    can't see: death by a thousand pauses, or one long silent lookup with no 'one moment' bridge."""
    felt = trace.perceived_latency_ms()
    if felt <= config.PERCEIVED_LATENCY_MS:
        return []
    set_expectations = _agent_said(trace, re.compile(r"\b(one moment|just a (sec|second|moment)|bear with me|while i (check|look)|let me (check|look|pull))\b", re.I))
    sev = "high" if felt > config.PERCEIVED_LATENCY_MS * 2 else "medium"
    return [FailureInstance(
        id="perceived_latency", dimension="experience", title="The call felt slow to the caller",
        severity=sev,
        reason=f"The caller waited ~{felt}ms total on the agent across the call"
               + ("" if set_expectations else " with no wait-setting cue (no 'one moment')")
               + " — it felt sluggish even though individual steps may each be within SLA.",
        evidence=[f"cumulative perceived latency {felt}ms (threshold {config.PERCEIVED_LATENCY_MS}ms)"],
        heal_category="voice_behavior", heal_policy_id="minimize-perceived-latency", persona_hint="bad_audio",
        heal_rule="never leave the caller in silence: before any lookup or booking that takes a moment say 'one "
                  "moment while I check that', keep replies tight, and acknowledge immediately so the call never "
                  "feels slow even when a tool is running.")]


def _d_background_noise(spec: AgentSpec, trace: CallTrace) -> list[FailureInstance]:
    """A noisy line (low SNR or a noisy environment label) sustained across turns, that the agent never
    acknowledged or compensated for by reading details back. Plain observability sees clean text; the
    audio says the caller is in a cafe / on the street / wind is blowing."""
    noisy = [h for h in trace.audio_hears()
             if (_aud(h).snr_db is not None and _aud(h).snr_db <= config.NOISE_SNR_DB)
             or (_aud(h).noise and _aud(h).noise not in ("", "quiet"))]
    if len(noisy) < config.NOISE_MIN_TURNS:
        return []
    if _agent_said(trace, _ADAPT_RE):
        return []
    a0 = _aud(noisy[0])
    desc = a0.noise or (f"SNR {a0.snr_db:.0f}dB" if a0.snr_db is not None else "noisy")
    return [FailureInstance(
        id="background_noise", dimension="experience", title="Ignored a noisy line",
        severity="medium",
        reason=f"The line was noisy ({desc}) across {len(noisy)} turn(s) but the agent never acknowledged the "
               "connection or read critical details back to confirm — raising the odds of a mishear on a booking.",
        evidence=[_quote(noisy[0]), f"audio: noise={a0.noise or 'n/a'} snr={a0.snr_db if a0.snr_db is not None else 'n/a'}"],
        heal_category="voice_behavior", heal_policy_id="handle-noisy-line", persona_hint="bad_audio",
        heal_rule="if the line is noisy or breaking up, acknowledge the connection, speak clearly, and read back "
                  "any name, number, date, or party size to confirm before acting on it.")]


def _d_barge_in_unhandled(spec: AgentSpec, trace: CallTrace) -> list[FailureInstance]:
    """The caller talked over the agent (barge-in) while the agent was mid-monologue — a sign the agent
    rambled and didn't yield. Detectable only from the audio barge-in marker + the length of the say it
    interrupted."""
    for i in range(1, len(trace.events)):
        cur = trace.events[i]
        if cur.kind != "hear" or not (_aud(cur) and _aud(cur).barge_in):
            continue
        prev = trace.events[i - 1]
        if prev.kind == "say" and len(prev.text) >= config.BARGE_IN_LONG_SAY:
            return [FailureInstance(
                id="barge_in_unhandled", dimension="experience", title="Talked over the caller (long-winded, got cut off)",
                severity="medium",
                reason=f"The agent was {len(prev.text)} chars into a turn when the caller cut in — it was monologuing "
                       "instead of keeping replies short and leaving room for the caller.",
                evidence=[_quote(prev), _quote(cur) + " [barge-in]"],
                heal_category="voice_behavior", heal_policy_id="yield-on-barge-in", persona_hint="interrupter",
                heal_rule="keep spoken replies to one or two short sentences, and the instant the caller speaks over "
                          "you, stop talking immediately and respond to what they actually said.")]
    return []


def _d_language_switch(spec: AgentSpec, trace: CallTrace) -> list[FailureInstance]:
    """The caller spoke (or switched to) a non-English language and the agent never accommodated it —
    a language barrier the transcript flattens into 'foreign-looking text'."""
    foreign = [h for h in trace.audio_hears()
               if (_aud(h).lang and _aud(h).lang.split("-")[0] not in ("", "en")) or _aud(h).lang_switch]
    if not foreign:
        return []
    langs = sorted({(_aud(h).lang or "?").split("-")[0] for h in foreign if _aud(h).lang})
    return [FailureInstance(
        id="language_switch", dimension="conversation", title="Didn't handle a non-English caller",
        severity="medium",
        reason=f"The caller spoke {', '.join(langs) or 'another language'}"
               f"{' (switched mid-call)' if any(_aud(h).lang_switch for h in foreign) else ''}, but there's no policy "
               "to continue in that language or route them — risking the agent plowing ahead in English.",
        evidence=[_quote(foreign[0]), f"audio: lang={(_aud(foreign[0]).lang or '?')}"],
        heal_category="voice_behavior", heal_policy_id="handle-language-switch", persona_hint="spanish_speaker",
        heal_rule="if the caller speaks another language, continue in that language when you can; otherwise offer to "
                  "connect them to someone who speaks it — never ignore the language and keep going in English.")]


_PROMISE_RE = re.compile(r"\b(i'?ll|i will|let me|i'?m going to|i can)\s+(text|send|email|call|have (someone|staff)|schedule|book|check|put you|get you|forward)\b", re.I)
_PROMISE_TOOLS = {"send_sms", "escalate", "book_appointment", "reserve_table", "handle_refund", "check_availability"}


def _d_anomaly(spec: AgentSpec, trace: CallTrace) -> list[FailureInstance]:
    """The self-expanding part: catch off-pattern problems NONE of the 14 named detectors
    cover, name them, and let the discovery registry promote recurring ones into tracked
    scenarios. The eval suite grows from real traffic rather than staying frozen at design time."""
    # (a) the agent promised an action ("I'll text you") but no tool call backs it up
    promise = next((s for s in trace.says() if _PROMISE_RE.search(s.text)), None)
    if promise and not any(tc.name in _PROMISE_TOOLS for tc in trace.tool_calls()):
        return [FailureInstance(
            id="promised_action_not_taken", dimension="outcome", title="Promised an action it never took",
            severity="high", discovered=True, signature="promise_without_tool",
            reason="The agent told the caller it would do something (text/book/check/escalate) but never called any tool to do it.",
            evidence=[_quote(promise), "no backing tool call in the trace"],
            heal_category="voice_behavior", heal_policy_id="do-what-you-promise",
            heal_rule="if you tell a caller you'll do something (text, book, check, escalate), actually call the tool that does it before ending the call.")]
    # (b) the call ended on an unanswered caller question
    if trace.events and trace.events[-1].kind == "hear" and trace.events[-1].text.strip().endswith("?"):
        return [FailureInstance(
            id="ended_on_unanswered_question", dimension="conversation", title="Hung up on an unanswered question",
            severity="medium", discovered=True, signature="ended_unanswered",
            reason="The call ended right after the caller asked a question, with no agent answer.",
            evidence=[_quote(trace.events[-1]), "call ended with no agent turn after"],
            heal_category="voice_behavior", heal_policy_id="answer-or-escalate",
            heal_rule="never end a call on a caller's question; answer it, or say you'll find out and take a message / escalate.")]
    return []


# Registry — order matters only for display; all run. _d_anomaly is LAST: it only fires on
# things the named detectors didn't already claim, so it surfaces genuinely new modes.
DETECTORS = [
    _d_failed_action_masked,      # outcome   · phantom confirmation (keystone)
    _d_unconfirmed_success,       # outcome   · claimed success no tool produced
    _d_succeeded_but_denied,      # outcome   · denied a real success / double-book
    _d_unconsented_alternative,   # outcome   · booked an alternative w/o consent
    _d_missing_action,            # action    · asserted availability without checking
    _d_wrong_action,              # action    · wrong tool for a large party
    _d_unauthorized_action,       # action    · sensitive action with no authorization
    _d_pii_in_action,             # action    · card/SSN written into a record
    _d_orphaned_action,           # action    · tool invoked, never returned
    _d_low_confidence_write,      # action    · wrote a record off a low-confidence mishear
    _d_redundant_action,          # action    · retry storm / loop
    _d_slow_action,               # experience· exceeded the latency SLA
    _d_dead_air,                  # experience· silence after the caller spoke
    _d_perceived_latency,         # experience· the call FELT slow (cumulative caller wait)
    _d_unhandled_accent,          # conversation· low intelligibility (accent/mumble) not adapted to
    _d_caller_distress,           # conversation· shouting / very upset caller not de-escalated
    _d_background_noise,          # experience· noisy line ignored
    _d_barge_in_unhandled,        # experience· talked over the caller (rambling)
    _d_language_switch,           # conversation· non-English caller not accommodated
    _d_unmet_goal,                # outcome   · ended unresolved, no escalation
    _d_anomaly,                   # discovery · off-pattern modes the named detectors missed
]


def evaluate(spec: AgentSpec, trace: CallTrace) -> list[FailureInstance]:
    """Run every deterministic detector over the trace; return de-duplicated failures
    (worst severity per failure-class id wins)."""
    found: dict[str, FailureInstance] = {}
    for detector in DETECTORS:
        try:
            for f in detector(spec, trace):
                cur = found.get(f.id)
                if cur is None or _SEV_RANK[f.severity] > _SEV_RANK[cur.severity]:
                    found[f.id] = f
        except Exception:
            continue  # a detector bug must never break the eval
    for f in found.values():  # stamp the routing prior centrally (detectors stay agnostic)
        if f.id in CODE_SPACE:
            f.fix_space = "code"
    return sorted(found.values(), key=lambda f: -_SEV_RANK[f.severity])


def gating(failures: list[FailureInstance]) -> list[FailureInstance]:
    """The subset that should fail a pre-launch gate — i.e. the policy-healable failures, excluding
    the MONITORING_ONLY signals (which are surfaced but can't be patched away)."""
    return [f for f in failures if f.id not in MONITORING_ONLY]


def code_space(failures: list[FailureInstance]) -> list[FailureInstance]:
    """The subset whose fix is a code diff, not a policy — routed to the coding agent (code_heal.py).
    These are exactly the failures naming a tool-layer invariant no prompt can guarantee."""
    return [f for f in failures if f.fix_space == "code"]


def worst(failures: list[FailureInstance]) -> FailureInstance | None:
    return max(failures, key=lambda f: _SEV_RANK[f.severity], default=None)


def _trace_digest(trace: CallTrace) -> str:
    """Render the call as a compact event log INCLUDING the per-turn audio signal, so an LLM judge
    can reason about paralinguistic problems (accent, shouting, noise, barge-in) a plain transcript
    hides. This is the input that makes dynamic discovery see what observability tools can't."""
    lines: list[str] = []
    for e in trace.events:
        if e.kind == "hear":
            tags = [] if (e.asr_conf is None or e.asr_conf >= 1.0) else [f"asr={e.asr_conf:.2f}"]
            a = e.audio
            if a:
                for k, v in (("noise", a.noise), ("snr_db", a.snr_db), ("vol_dbfs", a.volume_dbfs),
                             ("wpm", a.speech_rate_wpm), ("lang", a.lang), ("sentiment", a.sentiment),
                             ("arousal", a.arousal)):
                    if v not in (None, ""):
                        tags.append(f"{k}={v}")
                if a.barge_in:
                    tags.append("barge_in")
                if a.repeat_request:
                    tags.append("repeat_request")
            suffix = f"  [{', '.join(tags)}]" if tags else ""
            lines.append(f'Caller: "{e.text}"{suffix}')
        elif e.kind == "say":
            lines.append(f'Agent: "{e.text}"')
        elif e.kind == "tool_call":
            lines.append(f"Agent→tool {e.name}({e.args})")
        else:
            lines.append(f"tool {e.name} ok={e.ok} {e.result} ({e.latency_ms}ms)")
    return "\n".join(lines)


async def classify_dynamic(spec: AgentSpec, trace: CallTrace, already: set[str]) -> list[FailureInstance]:
    """The truly-dynamic layer: an LLM reads the event+audio stream and NAMES failure modes none of
    the deterministic detectors cover. Gated by ANOMALY_LLM (default off) so the core engine stays
    snappy + key-free. Crucially, anything it proposes is still a FailureInstance whose fix runs
    through `governed()` + `safe_apply` — so a hallucinated finding can never ship an unsafe patch,
    and recurring signatures get promoted by the same discovery registry as the named detectors."""
    if not (config.ANOMALY_LLM and config.llm_available()):
        return []
    from . import llm
    sys = (
        "You are an expert voice-agent QA analyst. You receive a phone call as an event log that "
        "INCLUDES per-turn audio signal (ASR confidence, noise, loudness/vol_dbfs, speech rate, "
        "language, sentiment, arousal, barge_in, repeat_request). Surface failure modes a "
        "transcript-only tool would MISS — especially paralinguistic ones (accent/intelligibility, "
        "shouting/distress, noisy line, talking over the caller, a language barrier, a call that "
        "felt slow) and any other novel problem. Do NOT repeat modes already detected. JSON only."
    )
    user = (
        f"Business: {spec.business.name} ({spec.business.type}).\n"
        f"Already detected (do not repeat): {sorted(already)}\n\n"
        f"Call:\n{_trace_digest(trace)}\n\n"
        'Return JSON {"failures":[{"title","dimension"(conversation|action|outcome|experience),'
        '"severity"(low|medium|high|critical),"reason","signature"(stable snake_case key),'
        '"heal_rule"(a concrete >=25-char policy rule that prevents it)}]}. Empty list if nothing new.'
    )
    try:
        data = await llm.complete_json(sys, user)
    except Exception:
        return []
    raw = data.get("failures", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
    out: list[FailureInstance] = []
    for d in raw:
        if not isinstance(d, dict) or not str(d.get("reason", "")).strip():
            continue
        dim = d.get("dimension") if d.get("dimension") in DIMENSIONS else "conversation"
        sev = d.get("severity") if d.get("severity") in _SEV_RANK else "medium"
        sig = re.sub(r"[^a-z0-9]+", "_", (str(d.get("signature") or d.get("title") or "novel")).lower()).strip("_")[:40] or "novel"
        fid = f"dyn_{sig}"
        if fid in already or any(o.id == fid for o in out):
            continue
        rule = str(d.get("heal_rule") or "").strip()[:300]
        if len(rule) < 25:  # too thin to govern anything — skip rather than ship a no-op
            continue
        out.append(FailureInstance(
            id=fid, dimension=dim, title=str(d.get("title", "Novel failure mode"))[:80],
            severity=sev, reason=str(d["reason"])[:300], discovered=True, signature=sig,
            evidence=["llm-classified from the event + audio stream"],
            heal_category="voice_behavior", heal_policy_id=f"dyn-{sig.replace('_', '-')}", heal_rule=rule))
    return out


# The SEMANTIC requirement per fix: tokens a policy's rule MUST contain to genuinely close
# the gap. The heal-verification oracle checks this — so a null/garbage fix (Policy with an
# empty or "lol" rule) does NOT satisfy it, even though the policy id exists. This is what
# stops the loop from grading its own homework with the answer key.
_GOVERN_KW: dict[str, list[str]] = {
    "confirm-before-claiming": ["confirmation", "success status", "did not", "couldn't", "if it failed", "until the"],
    "availability-answer": ["check_availability", "never guess", "before answering", "before stating", "get_inventory"],
    "large-party-routing": ["large-party", "large party", "6+", "escalate", "do not use the normal", "private event"],
    "authorize-sensitive-actions": ["escalate", "authoriz", "never process"],
    "no-pii-in-records": ["never write", "card number", "ssn", "secure"],
    "no-duplicate-booking": ["never create a second", "real tool result", "duplicate", "no second"],
    "confirm-alternative": ["explicit yes", "get the caller", "before booking a different", "offer the alternative"],
    "read-back-uncertain": ["read it back", "read back", "get a yes"],
    "never-end-unresolved": ["never end", "escalate", "take a message", "take their details"],
    "surface-real-results": ["actual tool result", "surface", "late"],
    "set-wait-expectations": ["one moment", "while i check", "before the tool"],
    "no-dead-air": ["one moment", "acknowledge"],
    "no-tool-loop": ["twice", "escalate"],
    "do-what-you-promise": ["actually call", "before ending", "actually do"],
    "answer-or-escalate": ["never end a call on", "take a message", "escalate", "find out"],
    # paralinguistic heals — tokens the voice-anomaly fixes must contain to genuinely close the gap
    "adapt-to-low-intelligibility": ["read", "back", "confirm", "slow", "connect", "person", "repeat"],
    "de-escalate-distress": ["stay calm", "apolog", "escalate", "manager", "human", "acknowledge"],
    "minimize-perceived-latency": ["one moment", "silence", "acknowledge", "tight", "wait"],
    "handle-noisy-line": ["noisy", "connection", "read back", "confirm", "clearly", "breaking up"],
    "yield-on-barge-in": ["stop talking", "short", "speaks over", "one or two", "interrupt"],
    "handle-language-switch": ["language", "continue in", "connect", "speaks it", "another language"],
}


def governed(spec: AgentSpec, policy_id: str) -> bool:
    """Does the spec carry a REAL policy that closes this gap — present, non-empty, and whose
    rule contains the semantic tokens the fix requires? (Not mere id presence — that was circular.)"""
    p = spec.get_policy(policy_id)
    if not p or not p.rule.strip():
        return False
    kws = _GOVERN_KW.get(policy_id)
    if not kws:  # unknown id (e.g. a discovered mode): require a substantive rule
        return len(p.rule.strip()) >= 25
    rule = p.rule.lower()
    return any(k in rule for k in kws)


def cluster(failures: list[FailureInstance]) -> list[dict]:
    """Group symptoms by ROOT CAUSE (the single policy that resolves them). Many detected
    failures often share one underlying gap — e.g., phantom-confirmation + unconfirmed-success
    + orphaned-action all trace back to 'claims success it can't back up' → one fix, not three."""
    roots: dict[str, dict] = {}
    for f in failures:
        r = roots.setdefault(f.heal_policy_id, {"root": f.heal_policy_id, "category": f.heal_category,
                                                "fix": f.heal_rule, "symptoms": [], "severity": "low"})
        r["symptoms"].append({"id": f.id, "title": f.title, "dimension": f.dimension})
        if _SEV_RANK[f.severity] > _SEV_RANK[r["severity"]]:
            r["severity"] = f.severity
    return sorted(roots.values(), key=lambda r: -_SEV_RANK[r["severity"]])


def summarize(failures: list[FailureInstance]) -> dict:
    """Dimension/severity rollup for the dashboard + report."""
    by_dim: dict[str, int] = {d: 0 for d in DIMENSIONS}
    by_sev: dict[str, int] = {s: 0 for s in _SEV_RANK}
    for f in failures:
        by_dim[f.dimension] = by_dim.get(f.dimension, 0) + 1
        by_sev[f.severity] = by_sev.get(f.severity, 0) + 1
    return {"total": len(failures), "by_dimension": by_dim, "by_severity": by_sev,
            "dimensions_hit": [d for d, n in by_dim.items() if n]}
