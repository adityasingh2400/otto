"""In-memory spec store with lineage (good enough for a hackathon demo).

Each session keeps the current spec plus the full version history, so the dashboard
can show the v1 → v2 → … progression and downstream activation reads the latest.
"""

from __future__ import annotations

from otto_spec import AgentSpec

_SPECS: dict[str, AgentSpec] = {}
_HISTORY: dict[str, list[AgentSpec]] = {}
_ACTIVE: dict[str, dict] = {}  # session_id -> {"phone_number": ..., "spec_version": ...}
# Self-expanding eval: failure-mode signatures discovered from production, per session, with
# a recurrence count. A signature seen >= the promote threshold is "promoted" to a tracked
# scenario — the eval suite grows from real traffic instead of staying frozen at design time.
_DISCOVERED: dict[str, dict[str, int]] = {}


def set_spec(session_id: str, spec: AgentSpec) -> None:
    _SPECS[session_id] = spec
    _HISTORY.setdefault(session_id, []).append(spec.model_copy(deep=True))


def get_spec(session_id: str) -> AgentSpec | None:
    return _SPECS.get(session_id)


def history(session_id: str) -> list[AgentSpec]:
    return _HISTORY.get(session_id, [])


def set_active(session_id: str, phone_number: str, spec_version: int) -> None:
    _ACTIVE[session_id] = {"phone_number": phone_number, "spec_version": spec_version}


def get_active(session_id: str) -> dict | None:
    return _ACTIVE.get(session_id)


def record_discovery(session_id: str, signature: str) -> int:
    """Count a discovered failure-mode signature for this session; return its running count."""
    d = _DISCOVERED.setdefault(session_id, {})
    d[signature] = d.get(signature, 0) + 1
    return d[signature]


def discovered_modes(session_id: str) -> dict[str, int]:
    return dict(_DISCOVERED.get(session_id, {}))
