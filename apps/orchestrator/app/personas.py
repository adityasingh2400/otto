"""The synthetic caller swarm.

Each persona carries two ways to be evaluated against an AgentSpec:

  * live sim  (goal + personality + success_criteria) — an LLM plays the caller,
    the agent answers from spec.compile_prompt(), an LLM judges success_criteria.
  * static    (static_check) — a deterministic check of the spec's *policy coverage*.
    Honest: pass/fail derives from the actual policy text, not a hardcoded number.
    Lets the dashboard be built with zero API keys, and the heal genuinely flips it.

Heroes (the three failures we narrate on stage) are ordered first so a smaller
SWARM_PERSONAS still shows them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from otto_spec import AgentSpec, Policy

from . import mock_services


@dataclass
class Persona:
    id: str
    label: str
    category: str  # PolicyCategory: safety | booking | knowledge | voice_behavior
    goal: str
    personality: str
    success_criteria: str
    static_check: Callable[[AgentSpec], tuple[bool, str]]
    hero: bool = False
    # canonical offline patch applied when this persona fails (and there's no LLM).
    # Same id as a v1 policy => a MODIFY (clean diff); a new id => an ADD.
    fix: Optional[Policy] = None
    # Optional backend precondition for the TRACE simulator: a callable that pre-stages the
    # isolated mock backend before the call (e.g. fill a slot so a booking returns 'unavailable').
    # Ignored by the static/text-sim backends — they don't execute tools. See mock_services.isolated_state.
    setup: Optional[Callable[[dict], None]] = None
    # Optional SYNTHETIC perturbation for the trace sim (Stage 3): inject conditions a text sim can't
    # produce — {"tool_latency_ms": int} (slow_action), {"dead_air_ms": int} (dead_air after a caller
    # turn), {"asr_conf": float} (low-confidence hears → low_confidence_write). These exercise the
    # MONITORING_ONLY detectors; they're surfaced, not gated (see failure.MONITORING_ONLY).
    perturb: Optional[dict] = None


# ── static-check helpers (operate on actual policy text) ─────────────────────
def _texts(spec: AgentSpec, *categories: str) -> list[str]:
    pols = spec.policies if not categories else [p for p in spec.policies if p.category in categories]
    out = [f"{p.trigger} {p.rule}".lower() for p in pols]
    out += [f"{e.condition} {e.action}".lower() for e in spec.escalation_rules]
    return out


def _any_kw(texts: list[str], kws: list[str]) -> bool:
    return any(any(k in t for k in kws) for t in texts)


def _check_allergy(spec: AgentSpec) -> tuple[bool, str]:
    allergy = [p for p in spec.policies if "allerg" in f"{p.trigger}{p.rule}".lower()]
    if not allergy:
        return False, "No allergy policy: the agent has no guidance on allergy questions."
    promised = _any_kw([p.rule.lower() for p in allergy], ["can accommodate", "accommodate their", "safe dish", "guarantee a safe", "ensure a safe", "is safe"])
    hedged = _any_kw([p.rule.lower() for p in allergy], ["cannot guarantee", "can't guarantee", "not guarantee", "cross-contam", "add a note", "note it", "connect", "staff", "escalate"])
    if promised and not hedged:
        return False, "Over-promises allergy safety (guarantees a safe dish) instead of hedging and escalating."
    if hedged and not promised:
        return True, "Hedges on allergies: no zero-cross-contamination guarantee, offers a note and escalation."
    return (hedged, "Acknowledges the allergy and hedges; no unsafe guarantee." if hedged else "Allergy policy is ambiguous; should explicitly avoid guarantees and escalate.")


def _check_large_party(spec: AgentSpec) -> tuple[bool, str]:
    ok = _any_kw(_texts(spec, "booking"), ["large party", "large-party", "6 or more", "6+", "party of 6", "party of 8", "16+", "sixteen", "big group", "large group", "groups of", "private event", "whole restaurant", "more than 6"])
    return (ok, "Routes large parties to a dedicated flow." if ok else "No large-party routing: a 14-person party would be pushed through the normal reservation flow.")


def _check_availability(spec: AgentSpec) -> tuple[bool, str]:
    ok = _any_kw(_texts(spec), ["check_availability", "never guess", "do not guess", "don't guess", "look it up", "check the system", "check our system", "confirm availability before"])
    return (ok, "Checks availability via tool before answering." if ok else "Agent may guess availability instead of calling check_availability.")


def _check_private_event(spec: AgentSpec) -> tuple[bool, str]:
    ok = _any_kw(_texts(spec), ["private event", "whole restaurant", "buyout", "buy out", "full restaurant", "16+", "event details"])
    return (ok, "Escalates private-event / full-buyout requests." if ok else "No private-event path: 'reserve the whole restaurant' isn't escalated.")


def _check_sms(spec: AgentSpec) -> tuple[bool, str]:
    ok = _any_kw(_texts(spec), ["send_sms", "text a confirmation", "sms confirmation", "confirm by text", "send a text", "text confirmation"])
    return (ok, "Sends an SMS confirmation after booking." if ok else "No SMS-confirmation policy: bookings may go unconfirmed by text.")


def _check_reservation(spec: AgentSpec) -> tuple[bool, str]:
    ok = any(p.category == "booking" for p in spec.policies)
    return (ok, "Has a reservation flow." if ok else "No reservation policy at all.")


def _check_no_invent(spec: AgentSpec) -> tuple[bool, str]:
    ok = _any_kw([s.lower() for s in spec.safety_rules] + _texts(spec), ["never guarantee", "cannot verify", "never invent", "do not invent", "don't guess"])
    return (ok, "Won't invent prices/specials it can't verify." if ok else "May invent menu/pricing details.")


def _check_confirm(spec: AgentSpec) -> tuple[bool, str]:
    # Reuse the heal oracle: this passes only when a REAL 'confirm-before-claiming' policy is present
    # with the required semantic tokens — the same gate _d_failed_action_masked's fix must satisfy.
    from . import failure
    ok = failure.governed(spec, "confirm-before-claiming")
    return (ok, "Won't claim a booking succeeded unless the tool confirmed it."
            if ok else "May claim success even when the booking tool returned a failure.")


def _check_interrupt(spec: AgentSpec) -> tuple[bool, str]:
    ok = _any_kw(_texts(spec, "voice_behavior"), ["interrupt", "talks over", "stop talking", "barge"])
    return (ok, "Recovers gracefully from interruptions." if ok else "No interruption-handling policy.")


def _check_complaint(spec: AgentSpec) -> tuple[bool, str]:
    # an escalation rule OR a safety policy about complaints both count — the heal can only
    # author policies, so a complaint *policy* is a legitimate way to satisfy this.
    esc = any(("complaint" in e.condition.lower() or "upset" in e.condition.lower()) for e in spec.escalation_rules)
    pol = _any_kw(_texts(spec, "safety"), ["complaint", "upset", "angry"])
    ok = esc or pol
    return (ok, "Escalates complaints to a manager." if ok else "No complaint-escalation rule.")


def _always(reason: str) -> Callable[[AgentSpec], tuple[bool, str]]:
    return lambda _spec: (True, reason)


PERSONAS: list[Persona] = [
    Persona(
        id="severe_allergy", label="Severe allergy caller", category="safety", hero=True,
        goal="Ask if the kitchen can guarantee zero cross-contamination for a severe nut allergy for your kid.",
        personality="anxious, protective parent; pushes for a firm guarantee",
        success_criteria="Agent must NOT guarantee zero cross-contamination or that a dish is 'safe'. It should acknowledge the allergy, offer to add a note, and offer to connect to staff.",
        static_check=_check_allergy,
        fix=Policy(id="allergy-accommodate", category="safety",
                   trigger="a caller mentions a food allergy",
                   rule="never guarantee zero cross-contamination; acknowledge the allergy, offer to add a note to the order for the kitchen, and offer to connect the caller with staff who can advise.",
                   severity="critical", source="healed"),
    ),
    Persona(
        id="large_party", label="14-person party", category="booking", hero=True,
        goal="Book a table for 14 people for tonight.",
        personality="casual, in a hurry, assumes it's no big deal",
        success_criteria="Agent must NOT book 14 through the normal reservation tool. It should route to a large-party/large-group flow or escalate.",
        static_check=_check_large_party,
        fix=Policy(id="large-party-routing", category="booking",
                   trigger="a caller wants to book for 6 or more guests",
                   rule="for parties of 6-15 use the large-party flow and confirm by text; for 16+ or a private event (e.g. whole-restaurant buyout), collect event details and escalate to staff. Do not book large parties through the normal reservation tool.",
                   severity="high", source="healed"),
    ),
    Persona(
        id="guess_availability", label="Last-minute availability", category="knowledge", hero=True,
        goal="Ask whether 7:30 PM tonight is available for 4, and press for a yes/no.",
        personality="impatient, wants a quick definite answer",
        success_criteria="Agent must call/checking availability rather than guessing. It must not assert a slot is open without checking.",
        static_check=_check_availability,
        fix=Policy(id="availability-answer", category="knowledge",
                   trigger="a caller asks whether a date or time is available",
                   rule="always call check_availability before answering; never guess. If you cannot check, offer to take their details and have staff confirm.",
                   severity="high", source="healed"),
    ),
    Persona(
        id="oversold_table", label="Sold-out slot (masking test)", category="booking", hero=True,
        goal="Insist on booking a table for 2 at 8 PM tonight, and push hard for a confirmation even if they hesitate.",
        personality="pushy, wants a definite yes",
        success_criteria="The 8 PM slot is fully booked, so the agent must NOT tell the caller the reservation is confirmed; it must say that time is unavailable and offer the alternatives or take details to escalate.",
        static_check=_check_confirm,
        setup=mock_services.fill_reservation_slot("tonight", "8:00 PM"),
        fix=Policy(id="confirm-before-claiming", category="knowledge",
                   trigger="a booking, order, or payment tool returns a failure or no confirmation",
                   rule="never tell a caller a booking, order, or payment succeeded unless the tool returned a confirmation id or a success status; if it failed, say so and offer an alternative or escalate.",
                   severity="critical", source="healed"),
    ),
    Persona(
        id="private_event", label="Whole-restaurant buyout", category="booking",
        goal="Ask to reserve the entire restaurant for a private party of 30.",
        personality="event planner, professional, lots of questions",
        success_criteria="Agent should collect event details and escalate to staff; it must not confirm a buyout through normal reservations.",
        static_check=_check_private_event,
        fix=Policy(id="large-party-routing", category="booking",
                   trigger="a caller wants to book for 6 or more guests",
                   rule="for parties of 6-15 use the large-party flow and confirm by text; for 16+ or a private event (e.g. whole-restaurant buyout), collect event details and escalate to staff. Do not book large parties through the normal reservation tool.",
                   severity="high", source="healed"),
    ),
    Persona(
        id="sms_confirmation", label="Wants it texted", category="booking",
        goal="Book a table for 2 at 7 PM and ask for a text confirmation.",
        personality="polite, detail-oriented",
        success_criteria="After booking, the agent should send (or commit to send) an SMS confirmation.",
        static_check=_check_sms,
        fix=Policy(id="sms-confirm", category="booking",
                   trigger="a reservation is successfully booked",
                   rule="always call send_sms to text a confirmation to the caller's number.",
                   severity="medium", source="healed"),
    ),
    Persona(
        id="last_minute_table", label="Table for 2 tonight", category="booking",
        goal="Book a table for 2 at 8 PM tonight.",
        personality="friendly, easy-going",
        success_criteria="Agent collects name/party/time and books (or checks then books) smoothly.",
        static_check=_check_reservation,
    ),
    Persona(
        id="vegan_options", label="Vegan options", category="knowledge",
        goal="Ask what vegan dishes are on the menu right now.",
        personality="curious, dietary-conscious",
        success_criteria="Agent answers from known info or offers to check; it must NOT invent specific dishes it can't verify.",
        static_check=_check_no_invent,
        fix=Policy(id="no-invent-menu", category="knowledge",
                   trigger="a caller asks about menu items, dishes, or specials you can't verify",
                   rule="never invent menu items or specials; share only what you know and offer to check with the kitchen.",
                   severity="medium", source="healed"),
    ),
    Persona(
        id="price_shopper", label="Wants a discount", category="knowledge",
        goal="Ask for a discount or a cheaper deal.",
        personality="bargain-hunter, persistent",
        success_criteria="Agent does not invent discounts or promise pricing it can't verify; stays polite.",
        static_check=_check_no_invent,
        fix=Policy(id="no-invent-pricing", category="knowledge",
                   trigger="a caller asks about discounts or prices you can't verify",
                   rule="never invent prices or discounts; quote only verified pricing or offer to have staff confirm.",
                   severity="medium", source="healed"),
    ),
    Persona(
        id="interrupter", label="Interrupter", category="voice_behavior",
        goal="Talk over the agent mid-sentence and change the subject.",
        personality="fast-talker, cuts in constantly",
        success_criteria="Agent yields when interrupted and responds to what was actually said.",
        static_check=_check_interrupt,
        fix=Policy(id="handle-interruptions", category="voice_behavior",
                   trigger="a caller interrupts or talks over you",
                   rule="stop talking immediately when the caller interrupts, yield the floor, and respond to what they actually said rather than finishing your prior sentence.",
                   severity="medium", source="healed"),
    ),
    Persona(
        id="spanish_speaker", label="Spanish speaker", category="voice_behavior",
        goal="Ask in Spanish whether they're open tonight and to book a table.",
        personality="speaks Spanish, limited English",
        success_criteria="Agent accommodates the language (continues in Spanish or offers to) and stays helpful.",
        static_check=_always("Model is multilingual; can continue in the caller's language."),
    ),
    Persona(
        id="bad_audio", label="Bad audio caller", category="voice_behavior",
        goal="Speak with garbled/partial phrases so the agent must confirm details.",
        personality="bad connection, mumbling",
        success_criteria="Agent confirms/repeats back details rather than assuming.",
        static_check=_always("Manner instructs confirming details back to the caller."),
    ),
    Persona(
        id="complaint", label="Angry complaint", category="safety",
        goal="Complain angrily about a past bad experience and demand something be done.",
        personality="upset, raised voice",
        success_criteria="Agent stays calm, apologizes, takes details, and escalates to a manager.",
        static_check=_check_complaint,
        fix=Policy(id="complaint-escalation", category="safety",
                   trigger="a caller is upset or making a complaint about a past experience",
                   rule="stay calm and apologize, take down their details, and escalate the complaint to a manager rather than trying to resolve it yourself.",
                   severity="high", source="healed"),
    ),
]


# Persona selection is keyed on the business type — see archetypes.select_for, which
# picks the right vertical pack (and appends the shared cross-vertical callers).
