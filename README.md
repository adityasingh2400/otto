# LineForge

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
packages/spec/      AgentSpec (Pydantic source of truth + JSON Schema + TS types) + cached piccino.json & contractor.json
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
- vertical-archetyped swarm routes correctly (restaurant / contractor / clinic / generic).
- production loop (`POST /api/observe`): live failure → targeted swarm-heal → re-verify.
- dashboard streams the real pipeline over SSE at `http://localhost:8000`.

Honest stubs marked `# TODO(team)`: Pipecat live pipeline (`apps/agent`, D2), real Cekura
run over Daily (`app/cekura.py`, D3), Twilio webhook provisioning (D2), Next.js dashboard
polish (D4). See `docs/ARCHITECTURE.md` and `docs/DAYOF.md`.
