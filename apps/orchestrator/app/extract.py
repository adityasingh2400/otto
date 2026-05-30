"""Website → AgentSpec.

Reliability-first: the Piccino demo always uses the curated naive-v1 spec
(packages/spec/piccino.json) so the on-stage path can't flake. Any other URL runs a
real crawl + LLM extraction, falling back to the cached spec on any error. Extraction
captures only what the site supports — the edge-case safety/policy gaps are exactly
what the swarm discovers and the healer fixes.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlparse

import httpx
from lineforge_spec import AgentSpec, KnowledgeItem

from . import config, llm
from .events import bus

_PICCINO = config.SPEC_DIR / "piccino.json"
_FACT_DELAY = 0.2  # seconds between streamed facts (timeline cadence)


async def extract(session_id: str, url: str | None, use_cached: bool, cached: str | None = None,
                  extra_info: str | None = None) -> AgentSpec:
    await bus.publish(session_id, {"type": "stage", "stage": "extract", "status": "start",
                                   "detail": (f"cached: {cached}" if cached else url) or "cached Piccino spec"})
    host = (urlparse(url).hostname or "") if url else ""

    if cached:  # explicit demo fixture (piccino | contractor | …)
        spec = _load_cached(cached)
        if url:
            spec.business.public_url = url
    elif use_cached or not url or "piccino" in host:
        spec = _load_cached("piccino")
        if url:
            spec.business.public_url = url
    else:
        try:
            text = await _fetch_text(url)
            spec = await _llm_extract(url, text) if config.llm_available() else _load_cached("piccino")
        except Exception as e:
            await bus.publish(session_id, {"type": "fact", "topic": "note",
                                           "content": f"extraction fell back to demo spec ({e})"})
            spec = _load_cached("piccino")

    # "Website + relevant additional info": fold an owner-provided note into the spec's
    # knowledge as high-confidence (it enters the compiled prompt the agent runs on).
    if extra_info and extra_info.strip():
        note = extra_info.strip()[:600]
        spec.knowledge.append(KnowledgeItem(topic="owner-note", content=note, source_url="(owner-provided)", confidence=0.95))
        await bus.publish(session_id, {"type": "fact", "topic": "owner-note", "content": note[:120]})

    await _stream_facts(session_id, spec)
    await bus.publish(session_id, {"type": "stage", "stage": "extract", "status": "done",
                                   "detail": f"{spec.business.name}: {len(spec.capabilities)} capabilities, {len(spec.tools)} tools"})
    return spec


def _load_cached(name: str = "piccino") -> AgentSpec:
    path = config.SPEC_DIR / f"{name}.json"
    if not path.exists():
        path = _PICCINO
    return AgentSpec.model_validate_json(path.read_text())


async def _fetch_text(url: str) -> str:
    async with httpx.AsyncClient(follow_redirects=True, timeout=15.0,
                                 headers={"User-Agent": "LineForge/0.1 (+demo)"}) as c:
        r = await c.get(url)
        r.raise_for_status()
        html = r.text
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        text = " ".join(soup.get_text(" ").split())
    except Exception:
        text = html
    return text[:12000]


async def _llm_extract(url: str, text: str) -> AgentSpec:
    sys = (
        "You convert a business website into a baseline voice-agent spec. Output JSON "
        "only. Capture ONLY what the site actually supports. Do NOT invent edge-case "
        "safety policies — those are discovered later by testing. Keep policies minimal."
    )
    user = (
        f"URL: {url}\n\nWebsite text (truncated):\n{text}\n\n"
        "Return a JSON object with these keys:\n"
        "business{name,type,location,timezone,public_url,disclaimer}, "
        "voice{persona,greeting}, knowledge[{topic,content,source_url,confidence}], "
        "capabilities[], tools[{name,description,params[{name,type,description,required}],mock_behavior}], "
        "policies[{id,category,trigger,rule,severity,source}], "
        "escalation_rules[{id,condition,action}], safety_rules[], out_of_scope[].\n"
        "If it's a place that takes bookings/orders, include tools check_availability, "
        "reserve_table, send_sms, escalate. The disclaimer must note this is a sandbox "
        "demo built from public info, not the official line. category is one of "
        "safety|booking|knowledge|voice_behavior."
    )
    data = await llm.complete_json(sys, user)
    if isinstance(data, dict):
        data.setdefault("meta", {"version": 1, "notes": f"extracted from {url}"})
    try:
        return AgentSpec.model_validate(data)
    except Exception:
        return _load_cached()


async def _stream_facts(session_id: str, spec: AgentSpec) -> None:
    facts: list[tuple[str, str]] = [("type", f"{spec.business.type}: {spec.business.name}")]
    if spec.business.location:
        facts.append(("location", spec.business.location))
    facts += [("capability", c) for c in spec.capabilities]
    facts += [("knowledge", k.topic) for k in spec.knowledge]
    # the risk zones the swarm will probe (legible to non-technical judges)
    facts += [("risk", r) for r in ("allergies", "large parties", "availability accuracy", "pricing")]
    for topic, content in facts:
        await bus.publish(session_id, {"type": "fact", "topic": topic, "content": content})
        await asyncio.sleep(_FACT_DELAY)
