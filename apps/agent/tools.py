"""Mock tool handlers for the demo.

Each returns the deterministic `mock_behavior` declared on the tool in the AgentSpec
(so behavior is curated per business and matches what Cekura mock-tools expect). Swap
these for real integrations (POS, reservation system, Twilio SMS) post-hackathon.
"""

from __future__ import annotations

from typing import Any

from lineforge_spec import AgentSpec


def _mock(spec: AgentSpec, tool_name: str, fallback: dict) -> dict:
    for t in spec.tools:
        if t.name == tool_name:
            return dict(t.mock_behavior) or fallback
    return fallback


async def check_availability(spec: AgentSpec, args: dict[str, Any]) -> dict:
    res = _mock(spec, "check_availability", {"available": True, "alternatives": ["7:00 PM", "8:15 PM"]})
    res["requested"] = {k: args.get(k) for k in ("date", "time", "party_size")}
    return res


async def reserve_table(spec: AgentSpec, args: dict[str, Any]) -> dict:
    res = _mock(spec, "reserve_table", {"reservation_id": "RSV-MOCK-1842", "status": "confirmed"})
    res["party_size"] = args.get("party_size")
    return res


async def send_sms(spec: AgentSpec, args: dict[str, Any]) -> dict:
    return _mock(spec, "send_sms", {"status": "sent"})


async def escalate(spec: AgentSpec, args: dict[str, Any]) -> dict:
    res = _mock(spec, "escalate", {"status": "queued_for_staff"})
    res["reason"] = args.get("reason")
    return res


DISPATCH = {
    "check_availability": check_availability,
    "reserve_table": reserve_table,
    "send_sms": send_sms,
    "escalate": escalate,
}


async def dispatch(spec: AgentSpec, name: str, args: dict[str, Any]) -> dict:
    fn = DISPATCH.get(name)
    if not fn:
        return {"error": f"unknown tool {name}"}
    return await fn(spec, args)
