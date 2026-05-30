# Good morning — LineForge status

Built overnight, autonomously, no keys required. Everything below **runs right now**.

## TL;DR

- **Open `http://localhost:8000`** (server's been left running). Click **Piccino**,
  **Bayview Builders**, or **Dogpatch Family Health**. Watch: extract → swarm goes red →
  self-heal (patch diff) → re-run goes green → line goes live → production-loop buttons.
- Zero keys needed for the full demo. Tests: **7/7 green**. Design is in **your** language
  (cracked Sunset Arcade + ReRoute/swarma), see `DESIGN.md`.
- To go live (real phone + real Cekura), just drop keys into `.env` — paths are wired and
  tested. Follow `docs/RUNBOOK.md`.

## What runs with no keys

- The whole loop in `SWARM_MODE=local` (static policy-coverage checks; honest, not faked).
- 6 verticals, archetyped swarms: **restaurant / contractor / clinic / salon / law**
  (each ~58–62% → 100% after self-heal) + a generic fallback.
- Both loops: pre-launch gate + production `/api/observe` (replay a hero = "already
  hardened"; replay an uncovered edge case = real targeted heal).
- The "website + extra info" field on the launch screen (folds an owner note into the agent).

## What I built (the night, in order)

1. Spine: `AgentSpec` contract, orchestrator (extract/swarm/heal/observe/activate) over SSE, dashboard, agent skeleton, docs, 6 tests. (`0f60d66`)
2. Dashboard rebuilt in your Sunset Arcade design language + `DESIGN.md`. (`6e54bec`)
3. Cekura client to the **exact** documented API (Daily-room `run_scenarios_pipecat`, real result polling) + `setup.sh` + `RUNBOOK.md`. (`716d210`)
4. Pipecat agent to the **real current API** (runner pattern, correct imports) + end-of-call → production loop. (`640ed06`)
5. Clinic vertical (keyless) + `docs/DEMO.md` 90-sec video script. (`9339d0e`)
6. Verified NVIDIA/AWS Pipecat service paths + "additional info" intake + this handoff.

## Tomorrow's plug-and-play (in priority order)

1. `./scripts/setup.sh` (deps + tests, no keys).
2. Add `OPENAI_API_KEY` → swarm upgrades from static checks to real LLM conversation sims.
3. Twilio number + `ngrok` → go live: `cd apps/agent && uv run --python 3.12 bot.py --transport twilio --proxy <ngrok-host>` (RUNBOOK §2).
4. Cekura: create agent+scenarios in dashboard, set `CEKURA_AGENT_ID` + `CEKURA_SCENARIO_MAP`, `SWARM_MODE=cekura` (RUNBOOK §3).
5. Sponsor tracks: `STT_PROVIDER=nvidia`/`TTS_PROVIDER=nvidia`, `LLM_PROVIDER=bedrock` (RUNBOOK §4).

## Honest stubs (need keys/infra you'll add)

- Live Pipecat voice pipeline — correct + current code, but unrun here (no pipecat install / keys).
- Real Cekura run — needs a Cekura account + one-time scenario setup (RUNBOOK §3).
- Twilio webhook provisioning — manual one-liner in the console (RUNBOOK §2).

## Decisions waiting on you

- **Day-of repo provenance**: I did NOT fake git history (see `docs/DAYOF.md` + the
  reasoning there). Decide how you want to handle the fresh-repo build on the 30th.
- Whether to migrate the dashboard to Next.js (currently a single polished file; tokens
  port 1:1 — `DESIGN.md`).

## Map

`docs/`: ARCHITECTURE (design + vision + two loops) · TECH (per-sponsor deep dive) ·
RUNBOOK (keys→go-live) · DAYOF (competition-day plan) · DEMO (video script) · STATUS (this).
`DESIGN.md` (root): the visual system. `packages/spec/`: contract + cached verticals.
`apps/`: orchestrator (the loop + dashboard) · agent (Pipecat) · web (dashboard).
