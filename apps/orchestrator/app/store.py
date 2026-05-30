"""Spec store with lineage + durable persistence + a global "active business".

Each session keeps the current spec plus its full version history, so the dashboard can
show the v1 → v2 → … progression. On activation a session becomes the globally ACTIVE
business — the deployed voice agent fetches that spec (GET /api/active-spec) so an inbound
call is answered by whatever was last activated, and it survives a process restart.

Persistence: everything is mirrored to a JSON file (OTTO_DATA_DIR/store.json, default ./data).
On Render, mount a disk and set OTTO_DATA_DIR to its path so onboarded businesses persist.
In-memory stays the source of truth at runtime; the file is loaded once at import and
rewritten on every mutation (small data, demo scale — no DB needed).
"""

from __future__ import annotations

import json
import os
import pathlib

from otto_spec import AgentSpec

_DATA_DIR = pathlib.Path(os.getenv("OTTO_DATA_DIR", "data"))
_STORE_FILE = _DATA_DIR / "store.json"

_SPECS: dict[str, AgentSpec] = {}
_HISTORY: dict[str, list[AgentSpec]] = {}
_ACTIVE: dict[str, dict] = {}  # session_id -> {"phone_number": ..., "spec_version": ...}
_ACTIVE_SESSION: str | None = None  # the globally-active business (last activated)
# Self-expanding eval: failure-mode signatures discovered from production, per session, with
# a recurrence count. A signature seen >= the promote threshold is "promoted" to a tracked
# scenario — the eval suite grows from real traffic instead of staying frozen at design time.
_DISCOVERED: dict[str, dict[str, int]] = {}


def _save() -> None:
    """Mirror state to disk (best-effort — never let a persistence hiccup break a request)."""
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "specs": {sid: s.model_dump() for sid, s in _SPECS.items()},
            "active": _ACTIVE,
            "active_session": _ACTIVE_SESSION,
            "discovered": _DISCOVERED,
        }
        tmp = _STORE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(_STORE_FILE)  # atomic
    except Exception:
        pass


def _load() -> None:
    global _ACTIVE_SESSION
    try:
        if not _STORE_FILE.exists():
            return
        data = json.loads(_STORE_FILE.read_text())
        for sid, raw in (data.get("specs") or {}).items():
            spec = AgentSpec.model_validate(raw)
            _SPECS[sid] = spec
            _HISTORY.setdefault(sid, []).append(spec)
        _ACTIVE.update(data.get("active") or {})
        _ACTIVE_SESSION = data.get("active_session")
        _DISCOVERED.update(data.get("discovered") or {})
    except Exception:
        pass  # corrupt/old file shouldn't block startup


_load()


def set_spec(session_id: str, spec: AgentSpec) -> None:
    _SPECS[session_id] = spec
    _HISTORY.setdefault(session_id, []).append(spec.model_copy(deep=True))
    _save()


def get_spec(session_id: str) -> AgentSpec | None:
    return _SPECS.get(session_id)


def history(session_id: str) -> list[AgentSpec]:
    return _HISTORY.get(session_id, [])


def set_active(session_id: str, phone_number: str, spec_version: int) -> None:
    """Mark this session live on the phone line AND the globally-active business the agent serves."""
    global _ACTIVE_SESSION
    _ACTIVE[session_id] = {"phone_number": phone_number, "spec_version": spec_version}
    _ACTIVE_SESSION = session_id
    _save()


def get_active(session_id: str) -> dict | None:
    return _ACTIVE.get(session_id)


def get_active_spec() -> AgentSpec | None:
    """The spec of the most-recently-activated business — what the deployed agent answers as."""
    if _ACTIVE_SESSION:
        return _SPECS.get(_ACTIVE_SESSION)
    return None


def active_session() -> str | None:
    return _ACTIVE_SESSION


def record_discovery(session_id: str, signature: str) -> int:
    """Count a discovered failure-mode signature for this session; return its running count."""
    d = _DISCOVERED.setdefault(session_id, {})
    d[signature] = d.get(signature, 0) + 1
    _save()
    return d[signature]


def discovered_modes(session_id: str) -> dict[str, int]:
    return dict(_DISCOVERED.get(session_id, {}))
