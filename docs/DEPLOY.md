# Deploying Otto

Two units, wired by one URL:

- **Orchestrator + UI** → **Render** (container, public HTTPS). Serves the landing (paste a URL),
  the dashboard, the API, the tool engine, and the persistent spec store.
- **Voice agent** → **Pipecat Cloud** (Parakeet → Nemotron → Cartesia). Fetches the active
  business's spec from the orchestrator at call time; Twilio routes inbound calls to it.

```
owner pastes URL ─▶ Render: orchestrator + UI ─┐
                                                │ ORCH_BASE_URL (HTTPS)
caller ─PSTN▶ Twilio ─TwiML▶ Pipecat Cloud: agent ─┘  (GET /api/active-spec, POST /api/tool/*)
```

The agent answers as **whatever business the UI last onboarded + activated** (`/api/active-spec`),
and that survives restarts (Render disk → `OTTO_DATA_DIR=/data`). Single active business at a time.

---

## 1. Orchestrator + UI → Render
1. Push the repo to GitHub.
2. Render → **New → Blueprint** → pick this repo. It reads `render.yaml` (Docker build from
   `apps/orchestrator/Dockerfile`, repo-root context, a 1 GB disk at `/data`).
3. In the dashboard, fill the `sync:false` secrets (copy from your `.env`):
   `NVIDIA_API_KEY, DEEPGRAM_API_KEY, CARTESIA_API_KEY, TWILIO_*, OWNER_PHONE,
   DEMO_RESERVER_PHONE, CEKURA_*`.
4. Deploy → note the URL, e.g. `https://otto-orchestrator.onrender.com`. Set `PUBLIC_BASE_URL`
   to that, redeploy.
5. Smoke test: open the URL (landing) → paste a business site → watch the dashboard run the
   swarm/heal/activate. `GET /api/active-spec` should then return that business.

## 2. Voice agent → Pipecat Cloud
```bash
cd apps/agent
pc cloud auth login                                   # your Pipecat Cloud account (one-time)
pc cloud secrets set otto-agent-secrets --file ../../.env   # runtime secrets
# add ORCH_BASE_URL (the Render URL) to that secret set so the agent finds the orchestrator:
pc cloud secrets set otto-agent-secrets ORCH_BASE_URL=https://otto-orchestrator.onrender.com
pc cloud deploy --build-dir ../../ --dockerfile apps/agent/Dockerfile.pcc
```
`pcc-deploy.toml` supplies `agent_name=otto-agent`, the secret set, and `min_agents=1` (warm).
`pc cloud auth whoami` also shows a **Daily API key** — set `DAILY_API_KEY` if you want the
Cekura/Daily swarm path too.

## 3. Twilio → the agent
Create a TwiML Bin (Twilio console) and attach it to your number's Voice webhook:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="wss://api.pipecat.daily.co/ws/twilio">
      <Parameter name="_pipecatCloudServiceHost" value="otto-agent.YOUR_PCC_ORG"/>
    </Stream>
  </Connect>
</Response>
```
(`pc cloud organizations list` → your org name.) Then dial the number — you reach the agent.

## 4. Cekura (eval) against the live agent
Scenarios already exist (agent `18016`, 12 scenarios, see `.env` `CEKURA_SCENARIO_MAP`). Once the
agent is reachable, run a suite via the CLI (`cekura run start --agent-id 18016 --mode pipecat …`)
or flip the orchestrator to `SWARM_MODE=cekura`.

## Known blocker — SMS delivery
Twilio accepts the confirmation/owner texts (`201`) but US carriers drop them with **error 30034
(A2P 10DLC unregistered)**. Register A2P 10DLC (or get a Twilio mentor to expedite / provide a
registered number). Code is correct; this is account/regulatory.
