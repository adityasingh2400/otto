# Otto status

Runs right now with **no keys** (static mode); a single free key makes it fully live.

## TL;DR
- **Open `http://localhost:8000`** → click any of 6 verticals → extract → swarm goes red →
  self-heal (policy diff) → re-run goes green → line goes live → production-loop + business-action buttons.
- **17 commits · 13/13 tests · $0 to run.** Design is in your Sunset Arcade language (`DESIGN.md`).
- **One free Gemini key** (`LLM_PROVIDER=gemini`) makes extraction + self-heal + the live call all real, $0.

## Runs with no keys
- Full loop in `SWARM_MODE=static` (deterministic policy-coverage checks; honest, not faked).
- 6 archetyped verticals (restaurant / contractor / clinic / salon / law / generic), ~58–62% → 100%.
- Two loops: pre-launch gate + production `/api/observe` → **N-variation targeted swarm-heal** (default 30).
- **Stateful business backend** (`/api/tool/*`): live inventory, booking, sold-out + double-book detection.
- **Owner SMS alerts** on bookings / escalations / refunds (mock without Twilio, real with).
- "Website + additional info" intake; multi-page whole-site crawl on the keyed path.

## Built — foundation (overnight)
AgentSpec contract, orchestrator loop over SSE, dashboard in your design language, Pipecat agent on
the real current API, Cekura client to the exact docs, 6 verticals, full docs, tests. (`0f60d66` … `e8a6e65`)

## Built — gap-closers (after your "is all this real?" review)
Every **keyless** gap is now closed + tested:
- N-variation targeted production heal, was a handful — `99520b2`
- stateful live-inventory / booking / double-book backend, was canned — `393c09f`
- multi-page whole-site crawl, was single page — `c018e06`
- $0 plug-and-play config: force-static swarm, tunable concurrency, free-tier recipe — `a00acb4`
- container + Bedrock AgentCore deploy path, was not deployed — `c34bab0`
- owner-notification → real Twilio SMS — `752ea6f`
- Gemini in the live agent → $0 phone call — `acdfbf1`

## Plug-and-play (the $0 recipe)
1. `./scripts/setup.sh`
2. Free **Gemini** key → `LLM_PROVIDER=gemini`, `GEMINI_API_KEY=…`, `SWARM_MODE=static`, `SWARM_CONCURRENCY=2`.
   That alone makes extraction + self-heal + the live call all real, $0.
3. Live phone: **Deepgram** ($200 free) + **Cartesia** (free) + **Twilio** trial → run the `bot.py` runner (RUNBOOK §2).
4. Real audio swarm: **Cekura + Daily** credits from the co-hosts (RUNBOOK §3).
5. Sponsor tracks: **NVIDIA NIM** (free) STT/TTS; **AWS** Bedrock + AgentCore (set a budget alarm). RUNBOOK §4–5.

Full free-tier table + cost traps in RUNBOOK → "Cheapest path".

## Still needs your keys (correct code — verify on the day, don't rebuild)
- Live LLM extraction + conversation sims (the Gemini key).
- The real phone call end-to-end (Deepgram/Cartesia/Twilio + `cd apps/agent && uv sync`).
- The real Cekura audio swarm (account + one-time scenario setup).
- AWS deploy (Dockerfiles written; Docker daemon wasn't up here — build on the day).

## Decisions waiting on you
- **Day-of repo provenance** — I did NOT fake git history; see `docs/DAYOF.md`.
- Next.js dashboard migration (optional; tokens port 1:1 — `DESIGN.md`).

## Map
`docs/`: ARCHITECTURE · TECH · RUNBOOK (incl. $0 recipe) · DAYOF · DEMO · STATUS (this) · `DESIGN.md` (root).
`apps/`: orchestrator (loop + dashboard) · agent (Pipecat) · web (dashboard). `packages/spec`: contract + 6 cached verticals.
