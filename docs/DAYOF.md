# Day-of execution plan (May 30)

Goal: walk in, build the full thing fast and clean, and demo something elite. Speed on
the day is *real* because the hard part (design, research, API knowledge, this reference
repo) is already done. That's a strength — play it as one.

## Read this first: provenance

You said you want "no sign any code was built before 8am May 30," copied over so it looks
fresh. I'm not going to help forge git timestamps or scrub provenance, and you shouldn't
want it:

- **It's likely against the rules.** Most hackathons (and almost certainly a YC-run one)
  require the build to happen at the event, or to disclose prior work. Faking history to
  hide pre-built code is misrepresentation.
- **The downside is asymmetric.** This is judged by YC and the sponsors — the exact people
  whose trust is the prize. A DQ stings; being remembered as the team that faked it is
  worse and follows you. Founder character is something they actively weigh.
- **You don't need it.** Preparing design, research, and a reference implementation is
  normal and allowed. Re-building fast on the day from that prep is legitimate and *more*
  impressive than a suspiciously complete 9am repo.

So: **check the official rules first** (ask an organizer if unclear), disclose prep if
required, and use the plan below to build it live, fast, and clean.

## What's legit to bring (almost always allowed — confirm against the rules)

- This repo as your **reference + spec + notes** (design docs, research, architecture).
- **Accounts, API keys, and credits** set up in advance: Cekura, Twilio number, Daily,
  OpenAI/Bedrock, NVIDIA NIM, ngrok. (Provisioning eats hours — do it before.)
- **API familiarity** — you've already read the Pipecat/Cekura/Twilio docs.
- The **cached specs** (`piccino.json`, `contractor.json`) as demo fixtures.
- A clear **commit plan** (below) so you build in a clean, legible order.

## Build order on the day (the clean commit sequence)

Each line is one focused commit. You understand every piece, so this is a few hours of
real typing/adapting, not invention. Two people can split orchestrator vs. dashboard.

1. `chore: scaffold monorepo + env` — dirs, `.gitignore`, `.env.example`, READMEs.
2. `feat(spec): AgentSpec + compile_prompt + JSON schema` — the contract first.
3. `feat(orchestrator): FastAPI + SSE event bus + health`.
4. `feat(extract): website → AgentSpec (crawl + LLM) + cached fallback`.
5. `feat(swarm): persona suite + LLM-sim + LLM-judge`.
6. `feat(archetypes): vertical-aware persona selection`.
7. `feat(heal): failures → policy patches → versioned spec + diff`.
8. `feat(pipeline): extract→build→swarm→heal→gate→activate`.
9. `feat(web): mission-control dashboard on the live SSE`.
10. `feat(cekura): real test-framework client (SWARM_MODE=cekura)`.
11. `feat(agent): Pipecat bot + Twilio Media Streams + tools`.
12. `feat(observe): production loop — live call → targeted swarm-heal`.
13. `polish: latency, demo script, fallbacks, sponsor swaps on`.

Tag the demo build so you can always roll back to a known-good state before judging.

## What to build live vs. lean on prep

- **Build/verify live:** the Twilio number going live, the Cekura swarm over Daily, the
  live-call console, latency tuning. These are the things judges watch happen.
- **Lean on prep:** the architecture, the persona/archetype design, the dashboard layout,
  the cached specs, the API know-how.

## Risk drills (run these before judging)

- Extraction flakes → cached `piccino.json` fallback (already wired).
- Cekura/agent not reachable → swarm falls back to local sim (already wired).
- LLM latency on the live call → flip to a faster model / NVIDIA NIM; pre-warm the pipeline.
- Bad wifi → have a recorded backup video of the full flow.

## Elite demo + video (you flagged this)

- **Framing line, lead with it:** "Paste your website. Get a working phone line in 30
  seconds — then watch it attack and fix itself before it goes live."
- **The closing move:** the judge calls the number and asks the exact thing the swarm
  caught and patched (the allergy question). Loop closed, live, on stage.
- **Video:** screen-record the dashboard (extract stream → arena going red → patch diff →
  arena going green → ACTIVE banner) intercut with the real phone call on speaker. Keep it
  under 2 minutes. The pass-rate climbing from real runs is the hero shot.
- **UI:** the mission-control dark theme is in `apps/web/`. Polish: animate the arena
  cards flipping red→green on re-run, and make the ACTIVE banner land hard.
