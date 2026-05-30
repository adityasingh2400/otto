# RUNBOOK — wake up and ship

Everything below the "zero-key demo" already works. Going live is just dropping keys
into `.env`; the code paths are wired and tested.

## 0. Zero-key demo (works right now)

```bash
./scripts/setup.sh        # deps + schema + tests (no keys)
./scripts/dev.sh          # → http://localhost:8000
```

Click the **Piccino** or **Bayview Builders** chip. You'll see extract → swarm (red) →
self-heal (patch diff) → re-run (green) → line live, then the production-loop buttons.
Headless proof of the loop: `cd apps/orchestrator && uv run --python 3.12 python scripts/smoke.py`.

## 1. Keys → where they go

| Sponsor | Get | `.env` vars |
|---|---|---|
| **LLM** (extraction + heal + caller sim/judge) | OpenAI key (or Anthropic) | `OPENAI_API_KEY` (or `LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY`) |
| **Cekura** (real eval engine) | API key + create agent/scenarios in dashboard | `CEKURA_API_KEY`, `CEKURA_AGENT_ID`, `CEKURA_SCENARIO_MAP`, `SWARM_MODE=cekura` |
| **Twilio** (the phone line) | number + account sid/token | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`, `PUBLIC_BASE_URL` |
| **Daily** (WebRTC room for the swarm) | API key | `DAILY_API_KEY`, `DAILY_ROOM_URL` |
| **Deepgram** STT (default) | API key | `DEEPGRAM_API_KEY` |
| **Cartesia** TTS (default) | API key | `CARTESIA_API_KEY` |
| **NVIDIA** (track: Parakeet STT / Magpie TTS) | build.nvidia.com key | `NVIDIA_API_KEY`, `STT_PROVIDER=nvidia`, `TTS_PROVIDER=nvidia` |
| **AWS** (track: Nova LLM / AgentCore host) | access key/secret | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `LLM_PROVIDER=bedrock`, `BEDROCK_MODEL_ID` |

Only an LLM key is needed to upgrade the swarm from static checks to real conversation
sims. Everything else lights up the live phone path + sponsor tracks.

## Cheapest path ($0 / free tiers)

| Piece | Free option |
|---|---|
| LLM (extract/heal) | **Google AI Studio / Gemini** — free key (Gemma/Flash). Set `LLM_PROVIDER=gemini` |
| Swarm | `SWARM_MODE=static` + `SWARM_CONCURRENCY=2` — free + instant, won't hit free rate limits |
| STT | **Deepgram** — $200 free credit, no card, never expires |
| TTS | **Cartesia** free tier (20k/mo), **ElevenLabs** 10k chars/mo, or **NVIDIA NIM** free |
| Eval swarm (real audio) | **Cekura** + **Daily** — ask the co-hosts for hackathon credits on-site |
| Phone | **Twilio** $15 trial (~75 min, 1 number); upgrade ~$20 to drop the trial preamble for a cold judge call |
| AWS | $200 new-account credits; **set a budget alarm** and tear down AgentCore after |

Realistic total: **$0** without the Twilio upgrade, **~$20** for a frictionless live call.

The live phone agent runs on the **same Gemini key** (a fast Flash model via `AGENT_LLM_MODEL`),
so `LLM_PROVIDER=gemini` covers extraction, self-heal, AND the live call — the whole thing is $0.

Traps: (1) don't run a 1000-variation production heal on a paid LLM — each variation is one
call; keep `PRODUCTION_SWARM_VOLUME` ~30 or push volume through Cekura's credits. (2) Gemini
free tier is ~10-15 req/min — keep `SWARM_MODE=static` and use the key only for extraction/heal.

## 2. Go live (the phone call)

```bash
# terminal 1 — orchestrator + dashboard
./scripts/dev.sh

# terminal 2 — expose a port for Twilio
ngrok http 7860          # note the ngrok host (e.g. abc123.ngrok.app)

# terminal 3 — the Pipecat phone agent (the runner serves the webhook + TwiML)
cd apps/agent && uv sync --python 3.12
OTTO_SESSION=<sid-from-dashboard> uv run --python 3.12 bot.py --transport twilio --proxy abc123.ngrok.app
```

In the Twilio console, set the number's **Voice webhook** to your ngrok HTTPS URL (the
runner serves the TwiML). For a custom server instead, use `twilio_server.py` (see its docstring).
Call the number. The dashboard's live-call console + Cekura observability log every call;
a failure triggers the production swarm-heal.

## 3. Real Cekura swarm (track: Cekura)

One-time in the Cekura dashboard (or via API): create an **agent**, a **personality**, an
**LLM-judge metric**, and one **scenario per persona** (use `apps/orchestrator/app/personas.py`
goals + success criteria). Then:

```
SWARM_MODE=cekura
CEKURA_AGENT_ID=<id>
CEKURA_SCENARIO_MAP={"severe_allergy":30,"large_party":31,"guess_availability":32,...}
DAILY_ROOM_URL=<the agent's Daily room>
```

`run_scenarios_pipecat` sends Cekura into the agent's Daily room; results poll back into
the same arena. If anything's missing it falls back to local sim automatically.

## 4. Sponsor-track checklist

- [x] **Daily / Pipecat** — the agent framework (default).
- [ ] **Cekura** — `SWARM_MODE=cekura` + scenario map (§3).
- [ ] **Twilio** — number live + webhook (§2).
- [ ] **NVIDIA** — `STT_PROVIDER=nvidia` `TTS_PROVIDER=nvidia` (Parakeet + Magpie via NIM).
- [ ] **AWS** — `LLM_PROVIDER=bedrock` (Nova), and/or deploy the agent on Bedrock AgentCore:
  `./scripts/deploy-agentcore.sh` (ARM64 image → ECR → AgentCore). **Set a budget alarm first.**

## 5. Demo-day fallbacks (already wired)

- Extraction flakes → cached `piccino.json` / `contractor.json`.
- Cekura/agent unreachable → local sim/static swarm.
- LLM latency on the call → flip a faster model / NVIDIA NIM; pre-warm.
- Bad wifi → record the dashboard run as a backup video.

See `docs/DAYOF.md` for the competition-day build/commit plan and `docs/TECH.md` for how
each sponsor is used to its fullest.
