#!/usr/bin/env python3
"""Probe the LIVE Otto server end-to-end for one URL and print a JSON scorecard.

    python3 scripts/probe_site.py https://example.com      # server must be up on :8000

Runs the real product path a judge would see — POST /api/run (crawl -> Nemotron extract ->
archetype swarm -> self-heal -> activate), then reads back the spec richness and exercises any
synthesized live_query tool. Stdlib only (no uv / deps) so it's fast to fan out across many sites.
"""
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://localhost:8000"


def _req(method: str, path: str, body=None, timeout: float = 90):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.load(resp)


def probe(url: str) -> dict:
    out: dict = {"url": url, "errors": [], "issues": []}
    try:
        sid = _req("POST", "/api/run", {"url": url})["session_id"]
    except Exception as e:
        out["errors"].append(f"run failed: {type(e).__name__}: {e}")
        return out
    out["session_id"] = sid

    rep = None
    deadline = time.time() + 150
    while time.time() < deadline:
        try:
            rep = _req("GET", f"/api/report/{sid}")
            if rep.get("active") or len(rep.get("certification_rounds", [])) >= 2:
                break
        except urllib.error.HTTPError as e:
            if e.code != 404:
                out["errors"].append(f"report {e.code}")
        except Exception:
            pass
        time.sleep(1.5)
    if not rep:
        out["errors"].append("no report — extraction/pipeline stalled or errored")
        return out

    b = rep.get("business", {})
    out["business_name"] = b.get("name")
    out["business_type"] = b.get("type")
    cr = [(c["spec_version"], round(c["pass_rate"] * 100)) for c in rep.get("certification_rounds", [])]
    out["rounds"] = cr
    out["first_pass_rate"] = rep.get("first_pass_rate")
    out["final_pass_rate"] = rep.get("final_pass_rate")
    out["passed_gate"] = rep.get("passed_gate")
    out["active"] = bool(rep.get("active"))
    patches = rep.get("patches") or []
    out["healed_policies"] = sorted({f"{d.get('change','')}:{d.get('policy_id')}"
                                     for p in patches for d in (p.get("diffs") or [])})

    try:
        spec = _req("GET", f"/api/spec/{sid}")
        out["knowledge_count"] = len(spec.get("knowledge", []))
        out["capabilities_count"] = len(spec.get("capabilities", []))
        tools = spec.get("tools", [])
        out["tools"] = [{"name": t.get("name"),
                         "kind": (t.get("execution") or {}).get("kind"),
                         "action": (t.get("execution") or {}).get("action")} for t in tools]
        out["has_live_query"] = any((t.get("execution") or {}).get("kind") == "live_query" for t in tools)
        name = (b.get("name") or "").lower()
        out["right_business"] = name not in ("", "this business", "piccino") and "untitled" not in name

        lq = next((t for t in tools if (t.get("execution") or {}).get("kind") == "live_query"), None)
        if lq:
            param = ((lq.get("params") or [{}])[0] or {}).get("name") or "query"
            try:
                lr = _req("POST", f"/api/tool/{lq['name']}?session={sid}",
                          {param: "what are your hours and what do you offer?"}, timeout=30)
                out["live_query_result"] = {"found": lr.get("found"), "live": lr.get("live"),
                                            "answer": str(lr.get("answer") or lr.get("note")
                                                          or lr.get("page_excerpt") or "")[:160]}
                # a graceful "I couldn't confirm" is CORRECT (honest, no hallucination) — only an
                # exception or a non-live result for a tool tagged live counts as a real problem.
                if lr.get("live") is False:
                    out["issues"].append("live_query tool did not actually fetch the site (live=false)")
            except Exception as e:
                out["live_query_result"] = {"error": f"{type(e).__name__}: {e}"}
                out["issues"].append("live_query tool errored on execution")
    except Exception as e:
        out["errors"].append(f"spec fetch failed: {type(e).__name__}: {e}")

    if out.get("knowledge_count", 0) < 3:
        out["issues"].append(f"thin knowledge ({out.get('knowledge_count')}) — likely JS-only site or weak extract")
    if not out.get("right_business", True):
        out["issues"].append(f"wrong/fallback business name: {out.get('business_name')!r}")
    if not out.get("active"):
        out["issues"].append(f"did NOT activate (final {out.get('final_pass_rate')}, rounds {cr})")
    if not out.get("tools"):
        out["issues"].append("no tools synthesized")
    return out


if __name__ == "__main__":
    print(json.dumps(probe(sys.argv[1]), indent=2))
