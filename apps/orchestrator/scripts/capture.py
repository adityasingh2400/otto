"""Capture everything Otto extracted for the live business, as a readable snapshot to iterate on.

The live agent answers as the orchestrator's ACTIVE business; this pulls that business's full
AgentSpec (knowledge + tools + each tool's call-time execution + policies) plus the finished-call
feed (the issues that have surfaced), and writes a snapshot under live-runs/ that we work against.

Usage (from apps/orchestrator):
    python scripts/capture.py                  # the active business
    python scripts/capture.py <session_id>     # a specific build
    ORCH=http://localhost:8000 python scripts/capture.py
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import urllib.request

ORCH = os.getenv("ORCH", "http://localhost:8000")
OUT_DIR = pathlib.Path(__file__).resolve().parents[1] / "live-runs"


def _get(path: str) -> dict:
    with urllib.request.urlopen(f"{ORCH}{path}", timeout=10) as r:
        return json.loads(r.read().decode())


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "business").lower()).strip("-") or "business"


def main() -> None:
    session = sys.argv[1] if len(sys.argv) > 1 else None
    if not session:
        session = (_get("/api/active-session") or {}).get("session_id")
        if not session:
            print("No active business. Parse a site and hit Deploy first (or pass a session id).")
            return
    spec = _get(f"/api/spec/{session}")
    calls = (_get(f"/api/calls/{session}") or {}).get("calls", [])

    b = spec.get("business", {})
    name = b.get("name", "business")
    OUT_DIR.mkdir(exist_ok=True)
    out = OUT_DIR / f"{_slug(name)}-{session}.json"
    out.write_text(json.dumps({"session": session, "spec": spec, "calls": calls}, indent=2))

    # human summary — what the agent actually knows and can do
    print(f"\n=== {name} ({b.get('type')}) · session {session} ===")
    print(f"url: {b.get('public_url') or '?'}  ·  v{spec.get('meta', {}).get('version')}")
    print(f"\nknowledge ({len(spec.get('knowledge', []))}):")
    for k in spec.get("knowledge", []):
        print(f"  - {k['topic']}: {str(k['content'])[:90]}  (conf {k.get('confidence')})")
    print(f"\ntools ({len(spec.get('tools', []))}):")
    for t in spec.get("tools", []):
        ex = t.get("execution") or {}
        params = ", ".join(p["name"] for p in t.get("params", []))
        tail = ex.get("source_url") or ex.get("action") or ""
        print(f"  - {t['name']}({params}) [{ex.get('kind', '?')}] {tail}")
        print(f"      {str(t.get('description', ''))[:100]}")
    print(f"\npolicies ({len(spec.get('policies', []))}):")
    for p in spec.get("policies", []):
        print(f"  - [{p['category']}] when {str(p['trigger'])[:50]} -> {str(p['rule'])[:80]}")
    print(f"\nfinished calls / issues ({len(calls)}):")
    for c in calls[-12:]:
        flag = "CAUGHT" if c.get("failed") else "clean"
        print(f"  - [{flag}] {c.get('scenario', c.get('call_id'))}  {c.get('failures') or ''}")
    print(f"\nsnapshot -> {out}")


if __name__ == "__main__":
    main()
