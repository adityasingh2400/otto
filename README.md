# Otto

**Paste a business website. Get a live, self-healing inbound phone line in 30 seconds.**

Before the number goes live, a swarm of synthetic callers attacks the agent, an
eval engine finds every failure mode, the system patches its own policies, re-runs
the swarm, and only activates the phone line once it clears a safety gate.

> We are not selling a voice agent. We are selling **confidence that your business
> can safely let AI answer the phone.**

Built for the **Gemma 4 Voice Agents Hackathon** (YC SF, May 30 2026) — theme:
*Voice AI, open models, and next-generation evals.* Co-hosted by Daily/Pipecat and
Cekura, with NVIDIA, AWS, and Twilio.

---

## What Otto provides

Otto is an **AI front desk for local service businesses**. Connect your website (and,
optionally, your Google Business Profile, booking/POS platform, menus, or a 60-second
owner intake) and Otto stands up a phone line that:

- **Answers every call**, 24/7, in a natural voice — no missed calls, no staff pulled off the floor.
- **Takes the actual action, not just talk** — books the reservation or appointment, checks
  real availability, takes the order, processes a payment, handles a refund, dispatches a job,
  escalates to a human, texts a confirmation. The tool set is configured per business.
- **Is proven safe before it ever goes live** — a swarm of synthetic callers, tailored to your
  business type, attacks the agent; an eval engine scores every call on *did it say the right
  thing* **and** *did it do the right thing*; the system patches its own policies, re-runs the
  swarm, and activates the number only once it clears a safety gate.
- **Keeps healing in production** — every real call is evaluated. A wrong answer or a
  missed/incorrect action triggers a targeted, high-volume swarm-heal on that one thing →
  patch → re-verify → redeploy. The line never stops getting safer.

Two kinds of failure, both caught and healed: **what the agent says** (a hallucinated policy,
a botched edge case) and **what the agent does** (the wrong booking, the action it never took).
Most voice agents only ever test the first.

## Why this niche — local service businesses

We chose local service businesses — **restaurants, home-services contractors, clinics, salons,
law offices** — deliberately. Three reasons they're the right wedge:

1. **The phone is still the cash register.** These businesses run on calls: bookings, orders,
   changes, availability, complaints. A missed or fumbled call is lost revenue *today* — and
   owners miss calls constantly while staff get interrupted mid-service.

2. **High stakes are exactly why AI scares them — and that's the opening.** One bad answer or
   wrong action (an over-promised allergy guarantee, a hallucinated open table, a 14-top shoved
   through the normal flow, a mishandled refund) costs money or creates liability. So owners
   don't trust AI on their phone. That distrust *is* the market: Otto's whole product — tested,
   self-healing, gated — is the proof that earns the trust no generic voice bot can offer.

3. **The domain is bounded enough to actually test to safety.** A restaurant's calls, a
   contractor's calls, a clinic's calls each form a finite, enumerable set of scenarios. That's
   what makes the swarm-and-heal loop *tractable* here: we generate the right synthetic callers
   per vertical, drive coverage high, and clear a real pass gate. The same method is hopeless
   against an open-domain assistant; for a front desk it's achievable. **The niche and the
   method fit each other.**

Net: **high-pain, high-stakes, bounded-domain.** The businesses that most need their phone
answered are the ones most afraid to let AI do it — and the bounded domain is precisely what
lets Otto remove the fear.

---

## The 6-beat demo

1. **Paste** `https://www.piccino.com/` → "Building inbound system…"
2. **Extract** business rules → an `AgentSpec` (greeting, knowledge, tools, policies, escalation, safety).
3. **Swarm** — synthetic callers attack: severe-allergy, 14-person party, "are you open?", price-shopper, interrupter, Spanish speaker, bad audio.
4. **Failures** cluster on a map: *allergy guarantee too strong*, *large party mis-routed*, *guessed availability*.
5. **Self-heal** — the system rewrites the offending policies (before/after diff), re-runs the swarm, pass rate climbs 43% → 91%.
6. **Activate** — Twilio number goes live. The judge calls it, asks the exact thing the swarm caught, and the agent now handles it correctly. Loop closed, on stage.

## Architecture (Pipecat is the spine)

```
                          ┌─────────────────────────────┐
   Judge's phone ──PSTN──▶│  Twilio (Media Streams)     │
                          └──────────────┬──────────────┘
                                         │ ws audio
                                         ▼
 Cekura swarm ──WebRTC──▶  ┌──────────────────────────┐      ┌──────────────────┐
 (sim callers join a       │   Pipecat pipeline        │◀────▶│  Agent brain      │
  Daily room)              │   STT → LLM → TTS         │ tools│  (compiled from   │
                           └──────────────────────────┘      │   AgentSpec)      │
                                                              └──────────────────┘
        ▲                                                              ▲
        │ run scenarios / get pass-fail                                │ compile prompt
        │                                                              │
 ┌──────┴───────────────────────────────────────────────────────────┴──────┐
 │  Orchestrator (FastAPI)                                                    │
 │  extract(url) → build(spec) → swarm.run(spec) → heal(failures) → activate  │
 │  live events ──SSE──▶ Mission-Control dashboard (Next.js)                  │
 └────────────────────────────────────────────────────────────────────────────┘
```

The **AgentSpec** (`packages/spec/`) is the single contract every component reads
and writes. The self-heal loop only edits `policies[]`; the live prompt is
recompiled from the spec, so a patch is a legible before/after diff.

## Sponsor surface (one architecture, five sponsors)

| Layer | Default | Sponsor swap | Sponsor |
|---|---|---|---|
| Framework | **Pipecat** | — | Daily (co-host) |
| Eval / self-heal | **Cekura** | — | Cekura (co-host) |
| Telephony | **Twilio Media Streams** | Twilio ConversationRelay | Twilio (partner) |
| STT | Deepgram | **NVIDIA Parakeet (NIM)** | NVIDIA (mentor) |
| TTS | Cartesia | **NVIDIA Magpie (NIM)** | NVIDIA (mentor) |
| LLM | OpenAI `gpt-4o` | **AWS Bedrock Nova** | AWS (mentor) |
| Hosting | local / ngrok | **AWS Bedrock AgentCore** | AWS (mentor) |

Every layer is config-swappable via `.env`. Default = reliability. Flip on the
sponsor option with on-site mentor help to claim the track without risking the demo.

## Quickstart

```bash
cp .env.example .env          # works with zero keys; one LLM key upgrades to live sim
./scripts/dev.sh              # → http://localhost:8000 (dashboard + API, served together)

# prove the whole loop with no keys at all:
cd apps/orchestrator && uv run --python 3.12 python scripts/smoke.py
```

The self-heal loop runs **end-to-end with no keys** in `SWARM_MODE=local`:
- with an LLM key, an LLM plays each caller and an LLM-judge scores each call against the
  spec's success criteria;
- with no key, a deterministic policy-coverage check stands in.

Either way the loop is **real** — the heal genuinely flips failing checks, nothing is
hardcoded (verified: restaurant 58% → 100%, contractor 62% → 100%). Set `CEKURA_API_KEY`
and `SWARM_MODE=cekura` for the real eval engine.

## Layout

```
packages/spec/      AgentSpec (Pydantic source of truth + JSON Schema + TS types) + cached specs for 6 verticals (piccino, contractor, clinic, salon, law)
apps/orchestrator/  FastAPI: extract · swarm · archetypes (vertical-aware) · heal · observe (production loop) · activate · SSE · serves the dashboard
apps/agent/         Pipecat bot: serves an AgentSpec over Twilio (phone) + Daily (Cekura swarm)
apps/web/           Mission-control dashboard (single-file v0 on the live SSE; Next.js migration later)
docs/ARCHITECTURE.md  design doc — reframe, refined vision, the two loops, scope, risks, plan
docs/TECH.md          per-technology deep dive — how we leverage each sponsor fully
docs/DAYOF.md         competition-day execution + clean commit plan
```

## Two loops

- **Pre-launch (broad, gated):** extract → build → vertical-archetyped swarm → heal →
  re-run → activate only past `PASS_GATE`.
- **Production (targeted, perpetual):** every live call is evaluated (`POST /api/observe`);
  a detected failure triggers a focused, high-volume swarm-heal on that one thing →
  patch → re-verify → redeploy.

## Status

Spine is real and tested end-to-end with **zero keys** (`SWARM_MODE=local`, static checks):
- restaurant **58% → 100%** in one heal round; contractor **62% → 100%** on its own failure modes.
- vertical-archetyped swarm routes correctly across 6 archetypes (restaurant / contractor / clinic / salon / law / generic).
- production loop (`POST /api/observe`): live failure → targeted swarm-heal → re-verify.
- dashboard streams the real pipeline over SSE at `http://localhost:8000`.

Honest stubs marked `# TODO(team)`: Pipecat live pipeline (`apps/agent`, D2), real Cekura
run over Daily (`app/cekura.py`, D3), Twilio webhook provisioning (D2), Next.js dashboard
polish (D4). See `docs/ARCHITECTURE.md` and `docs/DAYOF.md`.
