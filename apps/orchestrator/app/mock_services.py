"""Stateful in-memory mock backend for business actions (demo only, not persistent).

Makes "query live inventory", "book", and "process payment" *dynamic* instead of canned:
availability decrements as slots fill, sold-out slots are refused with alternatives,
double-booking an appointment slot is rejected, and inventory depletes on orders. The
live Pipecat agent and the dashboard both call this through POST /api/tool/{name}, so
they share one source of truth. Swap these functions for real POS/reservation APIs later.
"""

from __future__ import annotations

from typing import Any

_TABLES_PER_SLOT = 6   # restaurant: tables available per time slot
_APPTS_PER_SLOT = 1    # services: one appointment per slot (so a 2nd booking is a double-book)

_STATE: dict[str, Any] = {}


def reset() -> None:
    _STATE.clear()
    _STATE.update({
        "reservations": [], "appointments": [], "sms": [], "payments": [], "refunds": [], "escalations": [],
        "inventory": {
            "margherita pizza": 12, "carbonara": 8, "tiramisu": 5,
            "house red bottle": 20, "gift card": 999,
        },
    })


reset()


def check_availability(date: str = "", time: str = "", party_size: int = 0, **_) -> dict:
    booked = [r for r in _STATE["reservations"] if r["date"] == date and r["time"] == time]
    left = max(_TABLES_PER_SLOT - len(booked), 0)
    return {"date": date, "time": time, "party_size": party_size, "available": left > 0,
            "tables_left": left, "alternatives": [] if left > 0 else ["7:00 PM", "8:15 PM", "9:00 PM"]}


def reserve_table(name: str = "", phone: str = "", date: str = "", time: str = "",
                  party_size: int = 0, notes: str = "", **_) -> dict:
    avail = check_availability(date=date, time=time, party_size=party_size)
    if not avail["available"]:
        return {"status": "unavailable", "reason": "that slot is fully booked", "alternatives": avail["alternatives"]}
    rid = f"RSV-{1000 + len(_STATE['reservations'])}"
    _STATE["reservations"].append({"id": rid, "name": name, "phone": phone, "date": date,
                                   "time": time, "party_size": party_size, "notes": notes})
    return {"status": "confirmed", "reservation_id": rid, "tables_left": avail["tables_left"] - 1}


def book_appointment(name: str = "", phone: str = "", time: str = "", reason: str = "",
                     service: str = "", matter_type: str = "", **_) -> dict:
    taken = [a for a in _STATE["appointments"] if a["time"] == time]
    if len(taken) >= _APPTS_PER_SLOT:  # double-book guard
        return {"status": "unavailable", "reason": f"{time} is already booked", "alternatives": ["+30 min", "+1 hr"]}
    aid = f"APT-{500 + len(_STATE['appointments'])}"
    _STATE["appointments"].append({"id": aid, "name": name, "phone": phone, "time": time,
                                   "detail": reason or service or matter_type})
    return {"status": "scheduled", "appointment_id": aid}


def get_inventory(item: str = "", **_) -> dict:
    inv = _STATE["inventory"]
    if item:
        qty = inv.get(item.lower().strip())
        return {"item": item, "in_stock": bool(qty and qty > 0), "qty": qty or 0}
    return {"inventory": dict(inv)}


def order_item(item: str = "", qty: int = 1, **_) -> dict:
    inv = _STATE["inventory"]
    key = item.lower().strip()
    have = inv.get(key, 0)
    if have < qty:
        return {"status": "out_of_stock", "item": item, "qty_available": have}
    inv[key] = have - qty
    return {"status": "ordered", "item": item, "qty": qty, "qty_left": inv[key]}


def send_sms(phone: str = "", body: str = "", **_) -> dict:
    _STATE["sms"].append({"phone": phone, "body": body})
    return {"status": "sent", "count": len(_STATE["sms"])}


def process_payment(amount: float = 0, method: str = "", **_) -> dict:
    _STATE["payments"].append({"amount": amount, "method": method})
    return {"status": "mock_only", "note": "no real charge in demo", "amount": amount}


def handle_refund(reason: str = "", **_) -> dict:
    _STATE["refunds"].append({"reason": reason})
    return {"status": "queued_for_review", "reason": reason}


def escalate(reason: str = "", summary: str = "", **_) -> dict:
    _STATE["escalations"].append({"reason": reason, "summary": summary})
    return {"status": "queued_for_staff", "reason": reason}


_DISPATCH = {
    "check_availability": check_availability, "reserve_table": reserve_table,
    "book_appointment": book_appointment, "get_inventory": get_inventory, "order_item": order_item,
    "send_sms": send_sms, "process_payment": process_payment, "handle_refund": handle_refund,
    "escalate": escalate,
}


def call(name: str, args: dict) -> dict:
    fn = _DISPATCH.get(name)
    if not fn:
        return {"error": f"unknown tool {name}"}
    try:
        return fn(**(args or {}))
    except TypeError:
        return fn(**{k: v for k, v in (args or {}).items() if k in fn.__code__.co_varnames})


def snapshot() -> dict:
    """Counts for the dashboard's live-state readout."""
    return {
        "reservations": len(_STATE["reservations"]), "appointments": len(_STATE["appointments"]),
        "sms": len(_STATE["sms"]), "payments": len(_STATE["payments"]),
        "inventory": dict(_STATE["inventory"]),
    }
