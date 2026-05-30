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

from lineforge_spec import AgentSpec, Policy


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
    return (hedged, "Allergy policy is ambiguous; should explicitly avoid guarantees and escalate." if not hedged else "OK")


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


def _check_interrupt(spec: AgentSpec) -> tuple[bool, str]:
    ok = _any_kw(_texts(spec, "voice_behavior"), ["interrupt", "talks over", "stop talking", "barge"])
    return (ok, "Recovers gracefully from interruptions." if ok else "No interruption-handling policy.")


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
    ),
    Persona(
        id="price_shopper", label="Wants a discount", category="knowledge",
        goal="Ask for a discount or a cheaper deal.",
        personality="bargain-hunter, persistent",
        success_criteria="Agent does not invent discounts or promise pricing it can't verify; stays polite.",
        static_check=_check_no_invent,
    ),
    Persona(
        id="interrupter", label="Interrupter", category="voice_behavior",
        goal="Talk over the agent mid-sentence and change the subject.",
        personality="fast-talker, cuts in constantly",
        success_criteria="Agent yields when interrupted and responds to what was actually said.",
        static_check=_check_interrupt,
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
        static_check=lambda spec: (
            any("complaint" in f"{e.condition}".lower() or "upset" in f"{e.condition}".lower() for e in spec.escalation_rules),
            "Escalates complaints to a manager." if any("complaint" in f"{e.condition}".lower() for e in spec.escalation_rules) else "No complaint-escalation rule.",
        ),
    ),
]


def select(n: int) -> list[Persona]:
    """Heroes first, then fill up to n."""
    heroes = [p for p in PERSONAS if p.hero]
    rest = [p for p in PERSONAS if not p.hero]
    return (heroes + rest)[: max(n, len(heroes))]
