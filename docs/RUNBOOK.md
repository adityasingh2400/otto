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

## 2. Go live (the phone call)

```bash
# terminal 1 — orchestrator + dashboard
./scripts/dev.sh

# terminal 2 — the Pipecat phone agent
cd apps/agent && uv sync --python 3.12
LINEFORGE_SESSION=<sid-from-dashboard> uv run --python 3.12 uvicorn twilio_server:app --port 7860

# terminal 3 — expose it
ngrok http 7860          # copy the https URL into PUBLIC_BASE_URL in .env
```

In the Twilio console, set the number's **Voice webhook** to `POST {PUBLIC_BASE_URL}/twiml`.
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
- [ ] **AWS** — `LLM_PROVIDER=bedrock` (Nova), and/or deploy the agent on Bedrock AgentCore.

## 5. Demo-day fallbacks (already wired)

- Extraction flakes → cached `piccino.json` / `contractor.json`.
- Cekura/agent unreachable → local sim/static swarm.
- LLM latency on the call → flip a faster model / NVIDIA NIM; pre-warm.
- Bad wifi → record the dashboard run as a backup video.

See `docs/DAYOF.md` for the competition-day build/commit plan and `docs/TECH.md` for how
each sponsor is used to its fullest.
