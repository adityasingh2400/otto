# Otto status

Runs right now with **no keys** (static mode); a single free key makes it fully live.

## TL;DR
- **Open `http://localhost:8000`** (landing) → enter → **`/app/`** mission control → click any of 5 verticals
  → extract → swarm goes red → self-heal (policy diff) → re-run goes green → line goes live →
  production-loop replays (voice **and** action-failure) + the printable **eval report** (`/report/?s=…`).
- **15/15 tests · $0 to run.** Design is the Warm Editorial system (`DESIGN.md`) — cream + maroon, Fraunces.
- **One free Gemini key** (`LLM_PROVIDER=gemini`, open-weights Gemma) makes extraction + self-heal + the live call all real, $0.

## This event (corrected): the **Voice Agents Hackathon** (YC SF, May 30)
Co-hosts **Daily + Cekura**; sponsors **NVIDIA / AWS / Twilio**. NOT the separate April "Gemma 4"
hackathon — there is no Gemma/DeepMind track here. The brief is the four stages
**Build & Customize → Deploy at Scale → Simulate & Evaluate → Auto-Improve** ("the best system,
not the best voice"). Otto is all four; the **Auto-Improve** stage is the product.

## Headline: the failure taxonomy + a self-improving SYSTEM — `docs/FAILURE_TAXONOMY.md`
Production calls are classified across **4 dimensions** (conversation / action / outcome / experience)
over the whole **CallTrace** event stream, not just the transcript — **14 detectors, mostly
deterministic** + an anomaly detector. It catches the failures voice-only evals miss: a tool that
*failed* while the agent said "you're all set," availability it *guessed*, a card number *written
into a record*, a *double-booking*, an *unauthorized* refund, a *4.2s dead-air* lookup. Each failure
**authors its own policy fix**; a targeted swarm re-verifies it red→green. Zero-key demo via
`app/traces.py` fixtures → `POST /api/observe {trace_id}`.

What makes it a SYSTEM, not a patcher (all deterministic, all surfaced in UI + the report):
- **Provably monotonic** (`heal.safe_apply`): every patch is regression-checked across the whole
  suite (pre-launch personas + probes); a patch that would regress anything is **rejected** — the
  agent cannot make itself worse. Verified: a patch re-introducing the allergy over-promise is caught.
- **Can't grade its own homework** (`failure.governed`): the verify oracle requires the policy to
  *semantically* close the gap (real rule tokens), so a `rule="lol"` fix is rejected as ineffective —
  which also kills prompt-bloat and vanity version bumps.
- **Root-cause clustering** (`failure.cluster`): N symptoms → M underlying causes → M fixes.
- **Self-expanding eval**: an anomaly detector **discovers failure modes it wasn't programmed for**
  ("promised a text it never sent") and promotes recurring ones — the eval suite compounds from real
  traffic. The report shows "0 regressions shipped · K new modes discovered."

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

## Built — this session (god-tier pass + the Auto-Improve upgrade)
- **Corrected the hackathon framing** repo-wide (was wrongly targeting the April "Gemma 4" event)
  and reframed everything around the real four-stage brief.
- **Failure taxonomy engine** (`app/failure.py`, `packages/spec/.../trace.py`, `app/traces.py`): the
  multi-dimensional event-stream classifier + 14 detectors + failure-authored heals. New tests.
- **Eval report / safety certificate** — printable `/report/?s=…` built only from real run data
  (`app/report.py`); linked from the activation banner.
- **"Learns" version-lineage rail** in the dashboard (v1 → v2 → v3 climb).
- Acted on a **39-finding adversarial review**: fixed the law demo (was activating with **no heal**),
  pinned Pipecat `<1.0` (was a guaranteed fresh-install ImportError), hardened the heal against bad
  LLM output, fixed the SSE double-deliver race, made the dashboard responsive + keyboard-accessible,
  made action buttons spec-driven, killed dead code, and more.

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
`docs/`: ARCHITECTURE · TECH · **FAILURE_TAXONOMY** · RUNBOOK (incl. $0 recipe) · DAYOF · DEMO · STATUS (this) · `DESIGN.md` (root).
`apps/`: orchestrator (loop + failure engine + report + dashboard) · agent (Pipecat, emits CallTrace) · web (dashboard + `/report/`). `packages/spec`: AgentSpec + CallTrace contract + 5 cached verticals.
