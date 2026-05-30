"""Stateful in-memory mock backend for business actions (demo only, not persistent).

Makes "query live inventory", "book", and "process payment" *dynamic* instead of canned:
availability decrements as slots fill, sold-out slots are refused with alternatives,
double-booking an appointment slot is rejected, and inventory depletes on orders. The
live Pipecat agent and the dashboard both call this through POST /api/tool/{name}, so
they share one source of truth. Swap these functions for real POS/reservation APIs later.
"""

from __future__ import annotations

import contextlib
import contextvars
from typing import Any, Callable, Iterator, Optional

import httpx

from . import config

_TABLES_PER_SLOT = 6   # restaurant: tables available per time slot
_APPTS_PER_SLOT = 1    # services: one appointment per slot (so a 2nd booking is a double-book)


def _fresh() -> dict[str, Any]:
    return {
        "reservations": [], "appointments": [], "sms": [], "payments": [], "refunds": [], "escalations": [],
        "notifications": [],
        "inventory": {
            "margherita pizza": 12, "carbonara": 8, "tiramisu": 5,
            "house red bottle": 20, "gift card": 999,
        },
    }


_STATE: dict[str, Any] = _fresh()

# Per-call backend isolation: the trace simulator runs many concurrent synthetic calls, each of
# which must mutate its OWN inventory/bookings — sharing one global _STATE would let one call's
# booking fill a slot another call is testing. A ContextVar is per-asyncio-task (copied at task
# creation), so a sim that sets it inside its task is isolated; everything else (the live agent,
# the dashboard) leaves it unset and shares the real _STATE — one source of truth, as before.
_state_var: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar("mock_state", default=None)


def _state() -> dict[str, Any]:
    s = _state_var.get()
    return s if s is not None else _STATE


def reset() -> None:
    _STATE.clear()
    _STATE.update(_fresh())


@contextlib.contextmanager
def isolated_state(seed: Optional[Callable[[dict], None]] = None) -> Iterator[dict]:
    """Run a block against a FRESH, private backend (the trace sim wraps each call in this). `seed`
    may pre-stage the backend so an action detector is reachable — e.g. fill the 8pm slot so a
    booking returns 'unavailable' and we can test whether the agent masks the failure."""
    st = _fresh()
    if seed:
        seed(st)
    token = _state_var.set(st)
    try:
        yield st
    finally:
        _state_var.reset(token)


# ── seed helpers (used by personas' `setup` hooks to stage a backend precondition) ──
def fill_reservation_slot(date: str = "tonight", time: str = "8:00 PM") -> Callable[[dict], None]:
    """Seed: mark the backend fully booked so check_availability/reserve_table return 'unavailable'
    regardless of how the caller/LLM phrases the date/time (a real model says '2024-12-19'/'20:00',
    not 'tonight'/'8:00 PM', so exact-string seeding would silently miss). Stages the sold-out
    precondition the masking detectors need."""
    def seed(st: dict) -> None:
        st["force_unavailable"] = True
    return seed


def deplete_inventory(item: str) -> Callable[[dict], None]:
    """Seed: zero out an item's stock so order_item returns 'out_of_stock'."""
    def seed(st: dict) -> None:
        st["inventory"][item.lower().strip()] = 0
    return seed


def _send_owner_sms(body: str) -> dict:
    """Real Twilio SMS to the business owner when keyed; otherwise records a mock."""
    sid, tok, frm, to = config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN, config.TWILIO_PHONE_NUMBER, config.OWNER_PHONE
    if not (sid and tok and frm and to):
        return {"sent": False, "reason": "mock (set TWILIO_* + OWNER_PHONE for real SMS)"}
    try:
        with httpx.Client(timeout=8.0) as c:
            r = c.post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                       data={"To": to, "From": frm, "Body": body}, auth=(sid, tok))
        return {"sent": r.status_code in (200, 201), "status": r.status_code}
    except Exception as e:  # never let a notification break the action
        return {"sent": False, "error": str(e)[:120]}


def _notify(kind: str, message: str) -> dict:
    """Owner-facing event: record it and (when keyed) text the owner."""
    rec = {"kind": kind, "message": message, "sms": _send_owner_sms(message)}
    _state()["notifications"].append(rec)
    return rec


def get_notifications() -> list:
    return list(_state()["notifications"])[-20:]


def check_availability(date: str = "", time: str = "", party_size: int = 0, **_) -> dict:
    if _state().get("force_unavailable"):  # seeded sold-out: honest 'full', with alternatives
        return {"date": date, "time": time, "party_size": party_size, "available": False,
                "tables_left": 0, "alternatives": ["7:00 PM", "8:15 PM", "9:00 PM"]}
    booked = [r for r in _state()["reservations"] if r["date"] == date and r["time"] == time]
    left = max(_TABLES_PER_SLOT - len(booked), 0)
    return {"date": date, "time": time, "party_size": party_size, "available": left > 0,
            "tables_left": left, "alternatives": [] if left > 0 else ["7:00 PM", "8:15 PM", "9:00 PM"]}


def reserve_table(name: str = "", phone: str = "", date: str = "", time: str = "",
                  party_size: int = 0, notes: str = "", **_) -> dict:
    avail = check_availability(date=date, time=time, party_size=party_size)
    if not avail["available"]:
        return {"status": "unavailable", "reason": "that slot is fully booked", "alternatives": avail["alternatives"]}
    rid = f"RSV-{1000 + len(_state()['reservations'])}"
    _state()["reservations"].append({"id": rid, "name": name, "phone": phone, "date": date,
                                   "time": time, "party_size": party_size, "notes": notes})
    _notify("reservation", f"New reservation: {name or 'guest'}, party of {party_size}, {date} {time} ({rid})")
    return {"status": "confirmed", "reservation_id": rid, "tables_left": avail["tables_left"] - 1}


def book_appointment(name: str = "", phone: str = "", time: str = "", reason: str = "",
                     service: str = "", matter_type: str = "", **_) -> dict:
    taken = [a for a in _state()["appointments"] if a["time"] == time]
    if len(taken) >= _APPTS_PER_SLOT:  # double-book guard
        return {"status": "unavailable", "reason": f"{time} is already booked", "alternatives": ["+30 min", "+1 hr"]}
    aid = f"APT-{500 + len(_state()['appointments'])}"
    _state()["appointments"].append({"id": aid, "name": name, "phone": phone, "time": time,
                                   "detail": reason or service or matter_type})
    _notify("appointment", f"New appointment: {name or 'caller'} at {time} ({aid})")
    return {"status": "scheduled", "appointment_id": aid}


def get_inventory(item: str = "", **_) -> dict:
    inv = _state()["inventory"]
    if item:
        qty = inv.get(item.lower().strip())
        return {"item": item, "in_stock": bool(qty and qty > 0), "qty": qty or 0}
    return {"inventory": dict(inv)}


def order_item(item: str = "", qty: int = 1, **_) -> dict:
    inv = _state()["inventory"]
    key = item.lower().strip()
    have = inv.get(key, 0)
    if have < qty:
        return {"status": "out_of_stock", "item": item, "qty_available": have}
    inv[key] = have - qty
    return {"status": "ordered", "item": item, "qty": qty, "qty_left": inv[key]}


def _e164(phone: str) -> str:
    """Best-effort US E.164: '(407) 921-4601' / '407-921-4601' -> '+14079214601'."""
    p = phone.strip()
    if p.startswith("+"):
        return "+" + "".join(ch for ch in p[1:] if ch.isdigit())
    digits = "".join(ch for ch in p if ch.isdigit())
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return "+" + digits if digits else ""


def _twilio_send(to: str, body: str) -> dict:
    """Real Twilio SMS to `to` when keyed; otherwise a labeled mock. Never raises —
    a telephony failure must not break the booking/notification it confirms."""
    sid, tok, frm = config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN, config.TWILIO_PHONE_NUMBER
    to = _e164(to)
    if not (sid and tok and frm and to):
        return {"sent": False, "reason": "mock (need TWILIO_* + a destination number)"}
    try:
        with httpx.Client(timeout=8.0) as c:
            r = c.post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                       data={"To": to, "From": frm, "Body": body}, auth=(sid, tok))
        ok = r.status_code in (200, 201)
        # Trial accounts reject unverified destinations (21608) / 10DLC-unregistered (30034) —
        # surface it so the caller-facing flow degrades visibly instead of pretending it sent.
        out = {"sent": ok, "to": to, "status": r.status_code}
        if not ok:
            try:
                out["error"] = r.json().get("message", "")[:160]
            except Exception:
                out["error"] = r.text[:160]
        return out
    except Exception as e:  # never let a telephony hiccup break the action
        return {"sent": False, "to": to, "error": str(e)[:120]}


def send_sms(phone: str = "", body: str = "", **_) -> dict:
    """Text the CALLER (e.g. a reservation confirmation) — real Twilio when keyed. Falls back to
    DEMO_RESERVER_PHONE when the caller's number is missing/garbled (ASR mis-hears digits) so a
    live test still texts a real phone; empty in prod, so real callers get their own number."""
    to = phone if len(_e164(phone)) == 12 else (config.DEMO_RESERVER_PHONE or phone)
    delivery = _twilio_send(to, body)
    _state()["sms"].append({"phone": phone, "body": body, "delivery": delivery})
    return {"status": "sent" if delivery.get("sent") else "queued",
            "delivered": delivery.get("sent", False), "to": delivery.get("to", phone),
            "count": len(_state()["sms"]), **({"note": delivery["error"]} if delivery.get("error") else {})}


def process_payment(amount: float = 0, method: str = "", **_) -> dict:
    _state()["payments"].append({"amount": amount, "method": method})
    return {"status": "mock_only", "note": "no real charge in demo", "amount": amount}


def handle_refund(reason: str = "", **_) -> dict:
    _state()["refunds"].append({"reason": reason})
    _notify("refund", f"Refund request: {reason}")
    return {"status": "queued_for_review", "reason": reason}


def escalate(reason: str = "", summary: str = "", **_) -> dict:
    _state()["escalations"].append({"reason": reason, "summary": summary})
    _notify("escalation", f"Escalation ({reason}): {summary}")
    return {"status": "queued_for_staff", "reason": reason}


_DISPATCH = {
    "check_availability": check_availability, "reserve_table": reserve_table,
    "book_appointment": book_appointment, "get_inventory": get_inventory, "order_item": order_item,
    "send_sms": send_sms, "process_payment": process_payment, "handle_refund": handle_refund,
    "escalate": escalate,
}


# LLMs (and the dashboard) often send numerics as strings ("4", "2"); coerce so a
# comparison like `have < qty` can't raise deep in a handler.
_INT_ARGS = {"party_size", "qty"}
_FLOAT_ARGS = {"amount"}


def _coerce(args: dict) -> dict:
    out = dict(args or {})
    for k in list(out):
        try:
            if k in _INT_ARGS and out[k] is not None:
                out[k] = int(float(out[k]))
            elif k in _FLOAT_ARGS and out[k] is not None:
                out[k] = float(out[k])
        except (TypeError, ValueError):
            pass  # leave as-is; the handler tolerates **_
    return out


def _generic_action(name: str, args: dict) -> dict:
    """A synthesized tool with no curated backend op (e.g. a per-website action). Record it and
    return an HONEST 'queued for staff' — never a fake confirmation (so the agent can't claim a
    booking that didn't really happen). Swap for the business's real API at the adapter seam."""
    _state().setdefault("actions", []).append({"tool": name, "args": dict(args or {})})
    _notify("action", f"{name} requested: {args}")
    return {"status": "queued_for_staff", "tool": name, "note": "recorded for staff to action (demo sandbox)"}


def call(name: str, args: dict) -> dict:
    fn = _DISPATCH.get(name)
    if not fn:
        return _generic_action(name, args)
    try:
        return fn(**_coerce(args))
    except Exception as e:  # an action must never 500 the route
        return {"error": f"{name} failed: {e}"}


def snapshot() -> dict:
    """Counts for the dashboard's live-state readout."""
    return {
        "reservations": len(_state()["reservations"]), "appointments": len(_state()["appointments"]),
        "sms": len(_state()["sms"]), "payments": len(_state()["payments"]),
        "notifications": len(_state()["notifications"]),
        "inventory": dict(_state()["inventory"]),
    }
