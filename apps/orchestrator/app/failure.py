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
    return sorted(found.values(), key=lambda f: -_SEV_RANK[f.severity])


def worst(failures: list[FailureInstance]) -> FailureInstance | None:
    return max(failures, key=lambda f: _SEV_RANK[f.severity], default=None)


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
