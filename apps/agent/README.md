# LineForge agent (Pipecat)

Serves an `AgentSpec` as a live voice agent over two transports — Twilio (phone) and
Daily (the Cekura swarm) — using the **same** pipeline, so the swarm tests exactly what
answers the phone.

```
bot.py            pipeline builder: STT → LLM(+tools) → TTS, compiled from the spec
tools.py          mock tool handlers (check_availability, reserve_table, send_sms, …)
twilio_server.py  Twilio Media Streams → Pipecat (the live call)
daily_runner.py   Daily WebRTC room → Pipecat (Cekura sim callers join here)
```

## Setup (pinned to Python 3.12 for wheel coverage)

```bash
cd apps/agent
uv sync --python 3.12          # installs pipecat-ai + plugins
cp ../../.env.example ../../.env   # fill STT/LLM/TTS + Twilio + Daily keys
```

## Phone path (D2)

```bash
ngrok http 7860                       # note the ngrok host
# the runner serves the webhook + TwiML + serializer for you:
LINEFORGE_SESSION=<sid> uv run --python 3.12 bot.py --transport twilio --proxy <ngrok-host>
# Twilio console: set the number's Voice webhook → your ngrok HTTPS URL
```

`twilio_server.py` is the manual alternative if you need custom FastAPI routes.

## Swarm path (D3)

`daily_runner.py` joins a Daily room; `app/cekura.py` (orchestrator) gives Cekura that
room so its simulated callers can attack the agent. Set `SWARM_MODE=cekura`.

## Provider swaps (claim sponsor tracks, `.env`)

- `STT_PROVIDER=nvidia` / `TTS_PROVIDER=nvidia` → Parakeet + Magpie (NVIDIA).
- `LLM_PROVIDER=bedrock` → Nova (AWS); deploy on Bedrock AgentCore for infinite scale.

Defaults (Deepgram / gpt-4o / Cartesia) favor reliability. See `docs/TECH.md`.

## TODO(team)

- Confirm Pipecat import paths against the pinned version + `pipecat-quickstart-phone-bot`.
- Parse Twilio `start` frames for `streamSid`/`callSid` before building the serializer.
- Implement the eval observer in `bot.py` → POST finished transcripts to the
  orchestrator's `/api/observe` (production loop) and Cekura `observe()`.
