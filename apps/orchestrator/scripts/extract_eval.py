"""Dogfood the website extraction on a REAL url, end to end:
crawl -> Nemotron extract -> per-site tool synthesis -> a live website query, with a quality report.

    cd apps/orchestrator && set -a; . ../../.env; set +a
    uv run --python 3.12 python scripts/extract_eval.py https://www.example.com/

Prints what Otto parsed (info richness), the tools it synthesized (with execution kinds), and the
result of actually running a live_query against the site — so we can see where extraction is weak
and fix it in a loop.
"""

import asyncio
import sys

sys.path.insert(0, ".")

from app import config, extract, tool_engine  # noqa: E402


async def evaluate(url: str) -> dict:
    print(f"\n=== {url} ===  (llm={config.LLM_PROVIDER} · model={config.NVIDIA_MODEL})")
    text, signals = await extract._crawl(url, config.CRAWL_MAX_PAGES)
    pages = text.count("[page]")
    print(f"crawl: {len(text)} chars across {pages} page(s)", end="")
    if len(text) < 600:
        print("   ⚠ THIN — likely JS-rendered SPA; extraction will be weak")
    else:
        print("   ✓ server-rendered text")

    if signals:
        print(f"signals  : {', '.join(k for k in signals)}")
    spec = await extract._llm_extract(url, text, signals)
    b = spec.business
    print(f"business : {b.name}  |  type: {b.type}  |  loc: {b.location or '—'}")
    print(f"knowledge ({len(spec.knowledge)}):")
    for k in spec.knowledge[:7]:
        print(f"   • {k.topic}: {k.content[:90]}")
    print(f"capabilities: {spec.capabilities}")
    print(f"tools ({len(spec.tools)}):")
    for t in spec.tools:
        ex = t.execution or {}
        tag = ex.get("kind", "?") + (f"/{ex.get('action')}" if ex.get("kind") == "stateful" else "")
        print(f"   • {t.name:22} [{tag}] — {t.description[:64]}")

    # live website query (the middleman read): ask the site a universal question
    lq = next((t for t in spec.tools if (t.execution or {}).get("kind") == "live_query"), None)
    live = None
    if lq:
        param = (lq.params[0].name if lq.params else "query")
        r = await tool_engine.execute(spec, lq.name, {param: "what are your hours and what do you offer?"})
        live = r
        print(f"LIVE QUERY ({lq.name}): found={r.get('found')} live={r.get('live')}")
        print(f"   answer: {str(r.get('answer') or r.get('note') or r.get('page_excerpt',''))[:200]}")
    else:
        print("LIVE QUERY: (no live_query tool synthesized)")

    # scorecard — pass criteria are universal; live_query is BUSINESS-DEPENDENT (a static-info
    # business like a vet/law firm correctly has none) so it's reported as info, not a fail.
    score = {
        "fetched": len(text) >= 600,
        "right_business": b.name.lower() not in ("piccino", "this business"),  # didn't fall back / minimal
        "rich_knowledge": len(spec.knowledge) >= 3,
        "has_tools": len(spec.tools) >= 2,
        "static_in_knowledge": any(t in " ".join(k.topic.lower() for k in spec.knowledge) for t in ("hour", "address", "service", "menu", "price")),
    }
    info = f"live_query={'yes' if lq else 'none (static business — correct)'}" + (f", answered={live.get('found')}" if live else "")
    print("scorecard:", {k: ("✓" if v else "✗") for k, v in score.items()}, "|", info)
    return {"url": url, "spec": spec, "score": score}


async def main() -> None:
    urls = sys.argv[1:] or ["https://www.piccino.com/"]
    for u in urls:
        try:
            await evaluate(u)
        except Exception as e:
            print(f"   ✗ FAILED on {u}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    asyncio.run(main())
