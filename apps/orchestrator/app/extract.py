"""Website → AgentSpec.

Reliability-first: the Piccino demo always uses the curated naive-v1 spec
(packages/spec/piccino.json) so the on-stage path can't flake. Any other URL runs a
real crawl + LLM extraction, falling back to the cached spec on any error. Extraction
captures only what the site supports — the edge-case safety/policy gaps are exactly
what the swarm discovers and the healer fixes.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urljoin, urlparse

import httpx
from otto_spec import (AgentSpec, Business, EscalationRule, KnowledgeItem, Meta, Policy,
                       ToolParam, ToolSpec, Voice)

from . import config, llm

_POLICY_CATS = {"safety", "booking", "knowledge", "voice_behavior"}
_SEVERITIES = {"low", "medium", "high", "critical"}
from .events import bus

_PICCINO = config.SPEC_DIR / "piccino.json"
_FACT_DELAY = 0.2  # seconds between streamed facts (timeline cadence)
# A realistic browser UA — many real sites 403 a non-browser agent (verified on real SF sites).
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# pages worth pulling when crawling a business site (ranked above everything else)
_RELEVANT = ["menu", "about", "service", "contact", "hour", "reserv", "order", "book",
             "appointment", "faq", "pricing", "price", "gift", "location", "catering"]


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
        signals: dict = {}
        try:
            text, signals = await _crawl(url, config.CRAWL_MAX_PAGES, session_id)
            if config.llm_available() and text.strip():
                _m = config.EXTRACT_MODEL.split("/")[-1]
                await bus.publish(session_id, {"type": "fact", "topic": "crawl",
                                               "content": f"understanding the business · {_m} · ~{config.EXTRACT_EXPECTED_S}s deep parse"})
                spec = await _llm_extract(url, text, signals)
            else:
                # no LLM key (or empty crawl): build from deterministic site signals — the right
                # business with real facts where the page exposes them, never a different business.
                spec = _fallback_spec(url, signals)
        except Exception as e:
            # real URL must never become a DIFFERENT cached business; degrade using whatever signals we got
            await bus.publish(session_id, {"type": "fact", "topic": "note",
                                           "content": f"extraction degraded ({type(e).__name__})"})
            spec = _fallback_spec(url, signals)
        _ensure_default_tools(spec)  # functional even on the fallback/fast path

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


def _rank_links(base_url: str, hrefs: list[str]) -> list[str]:
    """Same-host internal links, deduped, ranked so relevant pages (menu/services/…) come first."""
    host = urlparse(base_url).hostname or ""
    seen = {base_url.split("#")[0].rstrip("/")}
    ranked: list[tuple[int, str]] = []
    for href in hrefs:
        if not href:
            continue
        u = urljoin(base_url, href).split("#")[0]
        p = urlparse(u)
        if p.scheme not in ("http", "https") or (p.hostname or "") != host:
            continue
        key = u.rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        ranked.append((sum(1 for kw in _RELEVANT if kw in u.lower()), key))
    ranked.sort(key=lambda x: -x[0])
    return [u for _, u in ranked]


def _iter_ld(data):
    """Flatten JSON-LD (a dict, a list, or a {@graph:[…]}) into its object nodes."""
    if isinstance(data, list):
        for d in data:
            yield from _iter_ld(d)
    elif isinstance(data, dict):
        if isinstance(data.get("@graph"), list):
            yield from _iter_ld(data["@graph"])
        else:
            yield data


_ORG_LD_TYPES = ("localbusiness", "restaurant", "store", "foodestablishment", "bakery", "cafe",
                 "medicalbusiness", "medicalclinic", "veterinarycare", "legalservice", "professionalservice",
                 "healthandbeautybusiness", "beautysalon", "hairsalon", "dayspa", "organization", "corporation")


def _page_signals(soup) -> dict:
    """Deterministic structured signal from the HTML head — survives even when a JS-rendered page
    has almost no body text. BeautifulSoup decodes HTML entities (&amp;, &#39;) for free."""
    sig: dict = {}

    def meta(attr: str, val: str) -> str:
        m = soup.find("meta", attrs={attr: val})
        return (m.get("content") or "").strip() if m and m.get("content") else ""

    if soup.title and soup.title.string:
        sig["title"] = soup.title.string.strip()
    desc = meta("name", "description") or meta("property", "og:description")
    if desc:
        sig["description"] = desc
    for prop in ("og:site_name", "og:title"):
        v = meta("property", prop)
        if v:
            sig.setdefault("site_name", v)

    import json as _json
    for s in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = _json.loads(s.string or s.get_text() or "")
        except Exception:
            continue
        for obj in _iter_ld(data):
            if not isinstance(obj, dict):
                continue
            t = obj.get("@type")
            t = (t[0] if isinstance(t, list) and t else t) or ""
            t = str(t)
            if obj.get("name") and "ld_name" not in sig and t.lower() in _ORG_LD_TYPES:
                sig["ld_name"], sig["ld_type"] = str(obj["name"]).strip(), t
            addr = obj.get("address")
            if isinstance(addr, dict) and "address" not in sig:
                parts = [str(addr.get(k, "")) for k in ("streetAddress", "addressLocality", "addressRegion", "postalCode")]
                joined = ", ".join(p for p in parts if p)
                if joined:
                    sig["address"] = joined
            elif isinstance(addr, str) and addr and "address" not in sig:
                sig["address"] = addr
            if obj.get("telephone") and "phone" not in sig:
                sig["phone"] = str(obj["telephone"])
            oh = obj.get("openingHours") or obj.get("openingHoursSpecification")
            if oh and "hours" not in sig:
                if isinstance(oh, list):
                    oh = "; ".join(str(x) for x in oh if isinstance(x, str)) or _spec_hours(oh)
                sig["hours"] = str(oh)[:300]
            for k in ("servesCuisine", "priceRange"):
                if obj.get(k) and k not in sig:
                    sig[k] = str(obj[k])
    return sig


def _spec_hours(specs: list) -> str:
    out = []
    for s in specs:
        if isinstance(s, dict):
            day = s.get("dayOfWeek", "")
            day = ", ".join(d.split("/")[-1] for d in day) if isinstance(day, list) else str(day).split("/")[-1]
            opens, closes = s.get("opens", ""), s.get("closes", "")
            if day or opens:
                out.append(f"{day} {opens}-{closes}".strip())
    return "; ".join(out)


async def _fetch_page(c: httpx.AsyncClient, url: str) -> tuple[str, list[str], dict]:
    r = await c.get(url)
    r.raise_for_status()
    html = r.text
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        links = [a.get("href") for a in soup.find_all("a", href=True)]
        signals = _page_signals(soup)
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        text = " ".join(soup.get_text(" ").split())
    except Exception:
        text, links, signals = html, [], {}
    return text[:6000], links, signals


async def _crawl(base_url: str, max_pages: int, session_id: str | None = None) -> tuple[str, dict]:
    """Fetch the homepage + the most relevant internal pages, narrating each fetch LIVE so the
    dashboard can show what it's reading in real time (the crawl is the slow part). Returns
    (concatenated text, signals) where signals are the deterministic head/JSON-LD facts."""
    async def say(content: str) -> None:
        if session_id:
            await bus.publish(session_id, {"type": "fact", "topic": "crawl", "content": content})

    parts: list[str] = []
    host = urlparse(base_url).hostname or base_url
    async with httpx.AsyncClient(follow_redirects=True, timeout=15.0,
                                 headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"}) as c:
        await say(f"opening {host}")
        text, links, signals = await _fetch_page(c, base_url)
        name = signals.get("ld_name") or signals.get("site_name") or signals.get("title")
        await say(f"homepage read — {name}" if name else "homepage read")
        parts.append(f"[page] {base_url}\n{_signal_header(signals)}{text}")
        ranked = _rank_links(base_url, links)[: max(0, max_pages - 1)]
        if ranked:
            await say(f"found {len(ranked)} more page(s) to read")
        for u in ranked:
            path = (urlparse(u).path or "/").rstrip("/") or "/"
            await say(f"reading {path}")
            try:
                t, _, _ = await _fetch_page(c, u)
                parts.append(f"[page] {u}\n{t}")
            except Exception:
                await say(f"skipped {path} (unreadable)")
                continue
        await say("done reading the site")
    return "\n\n".join(parts)[:16000], signals


def _signal_header(sig: dict) -> str:
    """A labeled one-liner of the structured signals, prepended to the LLM's page text so even a
    near-empty SPA body still carries the business name/type/hours/address."""
    if not sig:
        return ""
    order = [("name", sig.get("ld_name") or sig.get("site_name")), ("type", sig.get("ld_type")),
             ("description", sig.get("description")), ("hours", sig.get("hours")),
             ("address", sig.get("address")), ("phone", sig.get("phone")),
             ("cuisine", sig.get("servesCuisine")), ("price_range", sig.get("priceRange"))]
    line = " · ".join(f"{k}: {v}" for k, v in order if v)
    return f"[site metadata] {line}\n" if line else ""


# backend ops the stateful sandbox knows how to run; anything else becomes a generic recorded action.
_KNOWN_ACTIONS = {"check_availability", "reserve_table", "book_appointment", "get_inventory",
                  "order_item", "send_sms", "process_payment", "handle_refund", "escalate"}
_KINDS = {"live_query", "stateful", "escalate"}


async def _llm_extract(url: str, text: str, signals: dict | None = None) -> AgentSpec:
    signals = signals or {}
    sys = (
        "You convert a business website into a voice-agent spec. The agent acts as a MIDDLEMAN that "
        "answers the phone and performs the website's actions for the caller. Output JSON only. "
        "Capture ONLY what the site actually supports; never invent facts. Keep policies minimal "
        "(edge-case safety policies are discovered later by testing)."
    )
    user = (
        f"URL: {url}\n\nWebsite text (truncated):\n{text}\n\n"
        "Return a JSON object with these keys:\n"
        "business{name,type,location,timezone,public_url,disclaimer}, voice{persona,greeting}, "
        "knowledge[{topic,content,source_url,confidence}]  ← BAKE IN every STATIC fact so the agent "
        "answers instantly with NO lookup: hours, address, phone, services/menu ITEMS + prices, "
        "offerings, parking, policies, insurance/brands accepted — capture them richly and specifically, "
        "capabilities[], "
        "tools[{name,description,params[{name,type,description,required}],execution}], "
        "policies[{id,category,trigger,rule,severity,source}], "
        "escalation_rules[{id,condition,action}], safety_rules[], out_of_scope[].\n\n"
        "TOOLS — the rule: STATIC info goes in knowledge (above), NOT a tool. Only synthesize a tool "
        "for things that (a) change in real time or (b) are an ACTION. Each tool gets an `execution`:\n"
        "  • DYNAMIC live lookup — ONLY for data that genuinely changes: is a specific item IN STOCK "
        "right now, today's specials, the current seasonal/rotating menu, real-time wait — → "
        '   execution {"kind":"live_query","source_url":"<a page on THIS site>"} (re-fetches the live '
        "site mid-call). Do NOT make a live_query for hours / standard menu / prices — those are static knowledge.\n"
        "  • a BOOKING/ORDER/WRITE (reserve a table, book an appointment, place a pickup order) → "
        '   execution {"kind":"stateful","action":"<one of: check_availability, reserve_table, '
        "book_appointment, get_inventory, order_item, send_sms, process_payment, handle_refund>\"}.\n"
        "  • HAND TO STAFF → execution {\"kind\":\"escalate\"}.\n"
        "Fit the business: a restaurant → reserve_table + (live_query ONLY if the menu/specials rotate); "
        "a shop → order_item + an in-stock live_query; a salon/clinic → book_appointment. Many sites need "
        "NO live_query at all (everything static) — that's fine. Always include escalate; add send_sms if it confirms by text. "
        "The disclaimer must note this is a sandbox demo built from public info, not the official "
        "line, and that bookings are mocked. category is one of safety|booking|knowledge|voice_behavior."
    )
    try:
        data = await llm.complete_json(sys, user, model=config.EXTRACT_MODEL)  # rich reasoner → deep spec
    except Exception:
        # LLM/network down even after retries — build from the deterministic site signals
        # (title/meta/JSON-LD) so we keep the RIGHT business with real facts, never an empty agent.
        return _fallback_spec(url, signals)
    spec = _coerce_to_spec(data, url)  # robust: keep the right business even if sub-fields are loose
    _enrich_from_signals(spec, url, signals)  # backfill name/type/knowledge the LLM missed
    _normalize_tools(spec, url)        # platform authors the execution half (the LLM only proposes it)
    _ensure_default_tools(spec)        # guarantee the vertical's core tools so a FAST model still works
    return spec


def _host_name(url: str) -> str:
    host = (urlparse(url).hostname or "this business").replace("www.", "")
    return host.split(".")[0].replace("-", " ").title() or "This business"


# Map any text signal (domain, page title, JSON-LD @type) to a DESCRIPTIVE type string that
# archetypes.vertical_for() routes to the correct red-team pack. The damage of a wrong type is
# real: a plumber typed "business" routes to the generic pack and NEVER gets the after-hours
# emergency test; a salon never gets the dye-allergy patch-test. First match wins (specific first).
_TYPE_SIGNALS: list[tuple[str, tuple[str, ...]]] = [
    ("plumbing contractor", ("plumb",)),
    ("electrical contractor", ("electric",)),
    ("hvac contractor", ("hvac", "heating", "air conditioning")),
    ("roofing contractor", ("roof",)),
    ("general contractor", ("contractor", "construction", "remodel", "renovation", "handyman", "builder")),
    ("veterinary clinic", ("veterinar", "animal hospital", "animalhospital", "pet hospital",
                           "pethospital", "pet clinic", "petclinic", "spca", " vet ", "petcare")),
    ("dental clinic", ("dental", "dentist", "orthodont", "endodont")),
    ("medical clinic", ("clinic", "medical", "urgent care", "primary care", "physician", "healthcare", "health care")),
    ("law firm", ("law firm", "attorney", "lawyer", " legal", "litigation", "counsel")),
    ("hair salon", ("salon", "hair", "barber", "stylist", "blowout")),
    ("day spa", ("spa", "massage", "wellness", "nails", "waxing", "bathhouse")),
    ("bakery", ("bakery", "patisserie", "patisserie", "boulang", "pastr", "cake")),
    ("cafe", ("coffee", "espresso", "roaster", "cafe", "café", "tea house")),
    ("restaurant", ("restaurant", "pizzeria", "pizza", "trattoria", "bistro", "eatery", "taqueria",
                    "diner", "grill", "kitchen", "osteria", "ramen", "sushi", "bar & grill")),
    ("grocery store", ("grocery", "supermarket", "co-op", "cooperative", "market", "deli")),
    ("florist", ("florist", "flower", "floral", "bloom")),
    ("bike shop", ("bike", "bicycle", "cyclery", "cycling")),
    ("retail shop", ("boutique", "outfitter", "sporting goods", "store", "shop")),
]


def _infer_type(*texts: str) -> str:
    hay = " ".join(t.lower() for t in texts if t)
    hay = f" {hay} "
    for typ, kws in _TYPE_SIGNALS:
        if any(k in hay for k in kws):
            return typ
    return ""


def _route_type(current: str, *hints: str) -> str:
    """The business type that decides which red-team pack runs. Keep `current` if it already routes
    to a specific vertical; otherwise infer from the hints. Critical for schema.org JSON-LD, which
    often types a business as the generic umbrella 'Organization'/'LocalBusiness' — non-empty, so the
    old empty-only inference skipped it, silently routing a plumber/law-firm/spa to the GENERIC pack
    (no emergency-dispatch / conflict-check / patch-test test ever ran)."""
    from . import archetypes
    current = (current or "").strip()
    if current and archetypes.vertical_for(current) != "generic":
        return current
    inferred = _infer_type(*hints)
    if inferred and archetypes.vertical_for(inferred) != "generic":
        return inferred
    return current or inferred or "business"


def _clean_name(raw: str) -> str:
    """A page title is often 'Business Name | Tagline' or 'Home - Business' — keep the meatiest part."""
    raw = (raw or "").strip()
    for sep in ("|", " - ", "—", "–", "::", "·"):
        if sep in raw:
            parts = [p.strip() for p in raw.split(sep) if p.strip()]
            parts = [p for p in parts if p.lower() not in ("home", "welcome", "official site", "official website")]
            if parts:
                raw = max(parts, key=len)
            break
    return raw[:120]


def _signal_knowledge(signals: dict, url: str) -> list[KnowledgeItem]:
    facts: list[tuple[str, str]] = []
    if signals.get("description"):
        facts.append(("about", signals["description"]))
    if signals.get("hours"):
        facts.append(("hours", signals["hours"]))
    if signals.get("address"):
        facts.append(("location", signals["address"]))
    if signals.get("phone"):
        facts.append(("phone", signals["phone"]))
    if signals.get("servesCuisine"):
        facts.append(("cuisine", signals["servesCuisine"]))
    if signals.get("priceRange"):
        facts.append(("price range", signals["priceRange"]))
    return [KnowledgeItem(topic=t, content=str(c)[:600], source_url=url, confidence=0.9) for t, c in facts]


def _best_name(signals: dict, url: str) -> str:
    return (signals.get("ld_name") or signals.get("site_name") or _clean_name(signals.get("title", ""))
            or _host_name(url))


def _enrich_from_signals(spec: AgentSpec, url: str, signals: dict) -> None:
    """Backfill what the LLM left thin using the deterministic signals: a host-slug/empty name, a
    generically-typed business (so the RIGHT red-team pack runs), and missing static facts."""
    if signals:
        name = spec.business.name.strip()
        if not name or name.lower() in ("business", "this business") or name == _host_name(url):
            spec.business.name = _best_name(signals, url)
        if not spec.business.location and signals.get("address"):
            spec.business.location = signals["address"][:160]
    # route by inference whenever the type doesn't already map to a specific vertical (covers empty,
    # 'business', AND generic JSON-LD umbrellas like 'Organization'/'LocalBusiness')
    spec.business.type = _route_type(spec.business.type, spec.business.name, signals.get("title", ""),
                                     signals.get("description", ""), signals.get("ld_type", ""), url)
    if signals and len(spec.knowledge) < 3:
        have = {k.topic.lower() for k in spec.knowledge}
        for k in _signal_knowledge(signals, url):
            if k.topic.lower() not in have:
                spec.knowledge.append(k)


def _fallback_spec(url: str, signals: dict | None = None) -> AgentSpec:
    """Used when the LLM extraction is unavailable. With site signals (title/meta/JSON-LD) this is a
    real, useful spec — correct name, a routable type, and the facts the page exposed — not an empty
    shell. With no signals it degrades to the host-named minimal spec (never a different business)."""
    signals = signals or {}
    if not signals:
        return _minimal_spec(url)
    name = _best_name(signals, url)
    btype = _route_type(signals.get("ld_type", ""), name, signals.get("title", ""),
                        signals.get("description", ""), url)
    return AgentSpec(
        business=Business(name=name, type=btype, location=signals.get("address", "")[:160], public_url=url,
                          disclaimer="Sandbox AI assistant built from public website info for a demo; bookings are mocked."),
        voice=Voice(greeting=f"Thanks for calling {name}, this is the AI assistant. How can I help?"),
        knowledge=_signal_knowledge(signals, url),
        escalation_rules=[EscalationRule(id="esc-staff", condition="the caller needs something you can't handle",
                                         action="take a message and connect them to staff")],
        meta=Meta(version=1, notes=f"signal-based spec for {url} (LLM extraction unavailable)"),
    )


def _minimal_spec(url: str) -> AgentSpec:
    name = _host_name(url)
    return AgentSpec(
        business=Business(name=name, type=_infer_type(url) or "business", public_url=url),
        voice=Voice(greeting=f"Thanks for calling {name}, this is the AI assistant. How can I help?"),
        escalation_rules=[EscalationRule(id="esc-staff", condition="the caller needs something you can't handle",
                                         action="take a message and connect them to staff")],
        meta=Meta(version=1, notes=f"minimal spec for {url} (extraction unavailable)"),
    )


def _coerce_to_spec(data: dict, url: str) -> AgentSpec:
    """Build a valid AgentSpec from loose LLM JSON — fix/drop bad sub-fields rather than failing the
    whole extraction (and NEVER substituting a different cached business). The model reliably gets
    the business + knowledge + tools; strict policy/severity Literals are what used to trip it."""
    data = data if isinstance(data, dict) else {}
    biz = data.get("business") or {}
    name = str(biz.get("name") or _host_name(url))[:120]
    business = Business(
        name=name, type=str(biz.get("type") or "business")[:60],
        location=str(biz.get("location") or "")[:160], public_url=url,
        disclaimer=str(biz.get("disclaimer") or
                       "Sandbox AI assistant built from public website info for a demo; bookings are mocked."))
    v = data.get("voice") or {}
    voice = Voice(persona=str(v.get("persona") or "warm, professional, helpful")[:120],
                  greeting=str(v.get("greeting") or f"Thanks for calling {name}, how can I help?")[:300])

    knowledge = []
    for k in (data.get("knowledge") or []):
        if isinstance(k, dict) and k.get("content"):
            try:
                knowledge.append(KnowledgeItem(topic=str(k.get("topic", "info"))[:80], content=str(k["content"])[:600],
                                               source_url=str(k.get("source_url") or url),
                                               confidence=float(k.get("confidence", 0.8) or 0.8)))
            except (TypeError, ValueError):
                continue
    tools = []
    for t in (data.get("tools") or []):
        if not (isinstance(t, dict) and t.get("name")):
            continue
        params = [ToolParam(name=str(p["name"]), type=str(p.get("type", "string")),
                            description=str(p.get("description", "")), required=bool(p.get("required", True)))
                  for p in (t.get("params") or []) if isinstance(p, dict) and p.get("name")]
        tools.append(ToolSpec(name=str(t["name"])[:60], description=str(t.get("description", ""))[:200],
                              params=params, execution=t.get("execution") if isinstance(t.get("execution"), dict) else {}))
    policies = []
    for p in (data.get("policies") or []):
        if isinstance(p, dict) and p.get("id") and p.get("rule"):
            cat = p.get("category") if p.get("category") in _POLICY_CATS else "knowledge"
            sev = p.get("severity") if p.get("severity") in _SEVERITIES else "medium"
            policies.append(Policy(id=str(p["id"])[:60], category=cat, trigger=str(p.get("trigger", ""))[:200],
                                   rule=str(p["rule"])[:400], severity=sev, source="extracted"))
    escalation = []
    for i, e in enumerate(data.get("escalation_rules") or []):
        if isinstance(e, dict) and e.get("condition"):
            escalation.append(EscalationRule(id=str(e.get("id") or f"esc-{i}"), condition=str(e["condition"])[:200],
                                             action=str(e.get("action") or "offer to connect to staff or take a message")[:200]))
    caps = [str(c)[:60] for c in (data.get("capabilities") or []) if isinstance(c, str)]
    safety = [str(s)[:200] for s in (data.get("safety_rules") or []) if isinstance(s, str)]
    oos = [str(s)[:120] for s in (data.get("out_of_scope") or []) if isinstance(s, str)]
    return AgentSpec(business=business, voice=voice, knowledge=knowledge, capabilities=caps, tools=tools,
                     policies=policies, escalation_rules=escalation, safety_rules=safety, out_of_scope=oos,
                     meta=Meta(version=1, notes=f"extracted from {url}"))


def _normalize_tools(spec: AgentSpec, url: str) -> None:
    """Sanitize every tool's `execution` — the LLM proposes it, but the PLATFORM authors the risky
    half: clamp `kind`, pin a live_query's URL to the business's OWN host (SSRF guard at synthesis
    time too), and confine a stateful `action` to a known backend op (else a generic recorded one).
    A poisoned page therefore can't make a tool fetch off-site or invent a dangerous write."""
    base = url or spec.business.public_url or ""
    base_host = urlparse(base).hostname or ""
    for t in spec.tools:
        ex = dict(t.execution or {})
        kind = ex.get("kind")
        if kind not in _KINDS:  # infer from the tool's name/description
            n = f"{t.name} {t.description}".lower()
            # live_query is ONLY for genuinely DYNAMIC data (re-fetch the site mid-call). Hours/menu/
            # price are STATIC knowledge, never a live_query. Availability/booking is a stateful backend
            # action, not a site re-fetch — so it falls through to stateful below.
            kind = ("escalate" if any(k in n for k in ("escalate", "transfer", "manager", "human"))
                    else "live_query" if any(k in n for k in ("stock", "inventory", "in-stock", "sold out",
                        "special", "seasonal", "rotating", "current", "today", "real-time", "realtime",
                        "wait time", "waitlist"))
                    else "stateful")
        if kind == "live_query":
            src = ex.get("source_url") or base
            if (urlparse(src).hostname or "") != base_host:  # never fetch off the business's own host
                src = base
            t.execution = {"kind": "live_query", "source_url": src}
        elif kind == "escalate":
            t.execution = {"kind": "escalate"}
        else:
            action = ex.get("action")
            if action not in _KNOWN_ACTIONS:
                ln = t.name.lower()
                action = ("reserve_table" if "reserv" in ln or "table" in ln
                          else "book_appointment" if "book" in ln or "appoint" in ln or "schedul" in ln
                          else "order_item" if "order" in ln
                          else "send_sms" if "sms" in ln or "text" in ln
                          else t.name)  # unknown → generic recorded action (honest "queued for staff")
            t.execution = {"kind": "stateful", "action": action}


def _default_tools(business_type: str) -> list[ToolSpec]:
    """The standard tools for a vertical, wired by the PLATFORM (not the LLM) — so a fast/thin
    extraction still yields a FUNCTIONAL agent. The model proposes business facts + policies; the
    platform guarantees the agent can actually act (book, order, text, escalate)."""
    from . import archetypes
    v = archetypes.vertical_for(business_type)

    def P(n: str, d: str) -> ToolParam:
        return ToolParam(name=n, type="string", description=d, required=True)
    sms = ToolSpec(name="send_sms", description="Text the caller a confirmation or details.",
                   params=[P("phone", "the caller's phone number"), P("body", "the message")],
                   execution={"kind": "stateful", "action": "send_sms"})
    esc = ToolSpec(name="escalate", description="Hand the call off to a human staff member.",
                   params=[P("reason", "why you're escalating")], execution={"kind": "escalate"})
    if v == "restaurant":
        return [
            ToolSpec(name="check_availability", description="Check table availability for a date, time, and party size.",
                     params=[P("date", "date"), P("time", "time"), P("party_size", "number of guests")],
                     execution={"kind": "stateful", "action": "check_availability"}),
            ToolSpec(name="reserve_table", description="Book a table once availability is confirmed.",
                     params=[P("name", "guest name"), P("date", "date"), P("time", "time"), P("party_size", "guests")],
                     execution={"kind": "stateful", "action": "reserve_table"}),
            ToolSpec(name="order_item", description="Place a pickup or takeout order.",
                     params=[P("item", "menu item"), P("qty", "quantity")],
                     execution={"kind": "stateful", "action": "order_item"}),
            sms, esc]
    return [
        ToolSpec(name="book_appointment", description="Book or schedule an appointment.",
                 params=[P("name", "the caller's name"), P("time", "preferred date/time")],
                 execution={"kind": "stateful", "action": "book_appointment"}),
        sms, esc]


def _ensure_default_tools(spec: AgentSpec) -> None:
    """Guarantee the agent has its vertical's core tools even if the LLM proposed few/none — so a
    fast-model extraction is still a working phone agent that can book, order, text, and escalate."""
    have = {t.name for t in spec.tools}
    for t in _default_tools(spec.business.type):
        if t.name not in have:
            spec.tools.append(t)


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
