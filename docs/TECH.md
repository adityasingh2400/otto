# LineForge — Technology Deep Dive

How we use each technology to the **fullest**, not just the happy path. Every layer is
config-swappable; defaults favor reliability, sponsor swaps claim tracks. Five sponsors
(Daily/Pipecat, Cekura, Twilio, NVIDIA, AWS) covered by one architecture.

Sources are linked at the bottom.

---

## 0. The shape

The agent **brain** is a stateless policy engine compiled from an `AgentSpec`
(`spec.compile_prompt()` + tool schemas). It is reached two ways, both through **Pipecat**:

- **Live phone call:** Twilio → Pipecat → brain.
- **Synthetic swarm:** Cekura simulated callers → Daily WebRTC room → Pipecat → brain.

Same brain, same spec, same tools, two front doors. That's why a patch proven by the
swarm is the exact thing that ships to the phone.

---

## 1. Pipecat (Daily) — the spine · co-host

**What it is:** open-source, vendor-neutral real-time voice-agent framework. A pipeline
of frame processors: transport in → STT → context aggregator → LLM (with tools) → TTS →
transport out, with a parallel VAD/turn path.

**Full capabilities we lean on:**
- **Transports, swappable, same pipeline:** `FastAPIWebsocketTransport` (Twilio Media
  Streams), `DailyTransport` (WebRTC — the room Cekura's sim callers join), local dev.
  This single fact is the whole architecture: the swarm tests the *exact* pipeline that
  answers the phone.
- **Smart Turn v3.1** (`LocalSmartTurnAnalyzerV3`, ~65 ms, 23 languages): semantic
  end-of-turn detection, not just silence. Fewer false interrupts = the agent stops
  talking when it should and doesn't barge in. Big reliability/latency win on a phone.
- **Interruptions / barge-in:** first-class. The caller can cut the agent off and the
  pipeline cancels TTS and re-listens — this is what makes the "interrupter" persona pass
  for real instead of as a script.
- **Function calling:** native tool registration; the LLM calls `check_availability`,
  `reserve_table`, `process_payment`, `escalate`, and Pipecat routes to our handlers
  (`apps/agent/tools.py`). Tools are generated from `AgentSpec.tools` so each business
  gets its own action set.
- **Observers** (`MetricsLogObserver`, `turn_tracking_observer`, custom `FrameProcessor`):
  the hook for **"every interaction gets evaluated."** An observer taps every
  `MetricsFrame`/turn and ships the transcript + signals to Cekura observability after
  each call — zero changes to the conversation path.
- **Metrics:** `enable_metrics` + `enable_usage_metrics` on `PipelineTask` give TTFB,
  per-service latency, and token usage per turn → the live-call console's latency number.
- **OpenTelemetry:** full traces (SigNoz/any OTLP) for debugging the pipeline.
- **Pipecat Cloud:** managed hosting + autoscaling when we don't self-host on AWS.

**Leverage to the max:** one pipeline definition, two transports; Smart Turn for natural
phone cadence; an eval observer on every call. **Use it fully = the swarm and the phone
are the same code, and every call self-reports.**

---

## 2. Cekura — the eval engine · co-host · the differentiator

**What it is:** automated QA + observability for voice/chat agents (YC F24). Pre-prod
simulation + production monitoring. This is the hackathon's theme; we build the loop on it.

**Full capabilities we lean on:**
- **Scenario auto-generation from an agent description:** Cekura generates *thousands* of
  scenarios with diverse personas/accents from a description. **This is our vertical
  archetype engine** — feed the extracted business type + spec, get restaurant scenarios
  for a restaurant and dispatch/quote scenarios for a contractor. No wasted simulations.
  Recommended mix: 20-30 workflow + ~10 KB + 5-10 red-team.
- **Test Profiles / Mock Tools / Personalities / Success Metrics:** caller identity
  (name, account), deterministic tool input→output mappings (so a sim "books" without a
  real POS), behavioral dials (accent, interruption frequency, tone), and pass/fail
  criteria. Maps 1:1 to `apps/orchestrator/app/personas.py`.
- **Red teaming:** 32,617+ prebuilt adversarial scenarios across ~150 categories, 6
  vulnerability classes (social engineering, prompt injection, information extraction,
  …). This is the "huge amount of synthetic calls" we throw at a discovered failure.
- **Metrics:** Expected Outcome, Tool Call Success, Infrastructure Issues, Latency, plus
  **LLM-judge** and **Python** custom metrics. Hierarchical: instruction-following, CSAT,
  interruptions, tool-call accuracy. Our success criteria become LLM-judge metrics.
- **Production monitoring:** every live call scored on gibberish, interruption, latency,
  sentiment, pitch; **drop-off analysis**; replay; and **alerting via Slack/email/webhook
  on quality regressions.** That webhook is the trigger for our production heal loop.
- **CI/CD + API:** `run_scenarios_with_websockets`, results/runs APIs, GitHub-driven runs
  on prompt/model change. Our re-run-after-patch is exactly this.
- **MCP + Claude Code guide** (`docs.cekura.ai/mcp/claude-code-guide`): drive Cekura from
  an agent — useful for the heal loop itself.

**Leverage to the max:** pre-launch = broad auto-generated swarm gated on pass rate;
production = monitor every call, and on a regression webhook, fire a **focused** red-team
swarm at that one failure, patch, re-run, redeploy. Two loops, one engine.

---

## 3. Twilio — the phone · partner

**What it is:** the PSTN edge. Two ways to bridge a call into our brain:

- **Media Streams → Pipecat (default):** Twilio streams raw audio over a websocket;
  Pipecat owns STT/LLM/TTS. **Why default:** the swarm (Cekura over Daily) tests the same
  Pipecat pipeline, so phone == tested path.
- **ConversationRelay (GA, reliability fallback):** Twilio manages STT/TTS/interruptions/
  session/low-latency; we supply only the LLM over a websocket. Attributes we use:
  `interruptible` and `reportInputDuringAgentSpeech` (none/dtmf/speech/any) for barge-in +
  DTMF; `<Language>` elements switchable mid-call (the Spanish-speaker persona); Deepgram
  Flux STT (turn-aware, filler/noise robust). Less to build, very reliable; trade-off is
  the swarm tests the LLM-websocket via Cekura's custom integration rather than the media
  pipeline.

**Also:** number provisioning (the line that goes live), **SMS** (the `send_sms`
confirmation tool + after-booking policy), recording (feed Cekura observe), Voice Insights,
SIP, and Twilio-grade concurrency for scale.

**Leverage to the max:** Media Streams + Pipecat for the demo (tested path), SMS
confirmations as a real action, recordings piped to Cekura. ConversationRelay documented
as the one-flip reliability mode if Pipecat latency misbehaves on the day.

---

## 4. NVIDIA — fast, reliable speech · mentor

**What it is:** Riva speech models served as NIM microservices (build.nvidia.com/speech).

**Full capabilities we lean on:**
- **Parakeet ASR** (`parakeet-tdt-0.6b-v3`): ~6% WER, ~50× realtime, 25 languages. Fast +
  accurate STT = fewer mis-hears = fewer false failures in the swarm and on the phone.
- **Magpie TTS** (v2602, 9 languages, zero-shot voice cloning): natural, low-latency
  voice; clone a warm "house" voice per business.
- **NIM microservices:** GPU-accelerated, low-latency, drop-in as Pipecat STT/TTS services.
- NeMo / Nemotron + other LLMs via NIM if we want NVIDIA-hosted inference.

**Leverage to the max:** flip `STT_PROVIDER=nvidia` / `TTS_PROVIDER=nvidia` to run
Parakeet + Magpie in the Pipecat pipeline — claims the NVIDIA track, improves latency and
multilingual accuracy. Ask NVIDIA mentors for NIM endpoints/credits on-site.

---

## 5. AWS — infinite scale + speech-to-speech · mentor

**What it is:** Bedrock models + AgentCore Runtime hosting.

**Full capabilities we lean on:**
- **Bedrock AgentCore Runtime = "the line that scales infinitely":** 0 → thousands of
  concurrent sessions in seconds, each call in an **isolated microVM**, sessions up to
  8 hours, ARM64 (`linux/arm64`). Pay-per-use: **$0.0895/vCPU-hr + $0.00945/GB-hr, and
  I/O wait is free** — a voice agent spends most of its time waiting on the caller, so
  cost tracks active reasoning, not call duration. This is the scaling story verbatim.
- **Amazon Nova Sonic (speech-to-speech):** bidirectional streaming, understands tone,
  handles interruptions and mid-turn context changes; an alternative to the cascaded
  STT→LLM→TTS path when we want lowest latency. Pricing: ~$0.0034/1k speech-in tokens,
  ~$0.0136/1k speech-out.
- **Bedrock Nova Pro/Lite** as the LLM (`LLM_PROVIDER=bedrock`).
- Pipecat deploys on AgentCore (documented); Nova Sonic + AgentCore websocket sample exists.

**Leverage to the max:** deploy the Pipecat agent on AgentCore for the "scales to thousands
of simultaneous callers, you pay only for active reasoning" narrative; optionally swap the
cascaded pipeline for Nova Sonic speech-to-speech for lowest latency. Claims the AWS track.

---

## 6. The model layer (STT / LLM / TTS) — reliability defaults

| Stage | Default | Why | Sponsor swap |
|---|---|---|---|
| STT | Deepgram Nova / Flux | turn-aware, robust to fillers/noise | NVIDIA Parakeet |
| LLM (voice) | OpenAI gpt-4o (fn-calling) | best-tested tool calling + policy adherence | AWS Nova / Bedrock |
| LLM (reasoning: extract+heal) | gpt-4o (server-side, not latency-critical) | quality over speed | any |
| TTS | Cartesia Sonic | lowest TTFB | NVIDIA Magpie / ElevenLabs |

LLM choice matters most for **policy adherence** (the agent must obey the patched
policies) — that's the eval the swarm measures.

---

## 7. Two loops (the whole product)

```
PRE-LAUNCH  (broad, gate before go-live)
  extract → build → auto-gen swarm (vertical-archetyped) → fail → heal → re-run → gate → activate

PRODUCTION  (perpetual, targeted)
  every live call → Cekura observe → regression/failure detected
     → webhook → FOCUSED high-volume red-team swarm on THAT failure
     → heal that policy → re-run → redeploy   (the line never stops getting safer)
```

Pre-launch proves it's safe to turn on. Production keeps it safe as reality throws new
calls at it. Cekura is the engine for both; Pipecat observers feed it; the AgentCore/
Pipecat-Cloud host lets every one of thousands of simultaneous calls report in.

---

## Sources
- Pipecat: [metrics](https://docs.pipecat.ai/pipecat/fundamentals/metrics) ·
  [smart-turn](https://github.com/pipecat-ai/smart-turn) ·
  [turn observer](https://reference-server.pipecat.ai/en/stable/api/pipecat.observers.turn_tracking_observer.html) ·
  [Twilio websockets](https://docs.pipecat.ai/pipecat/telephony/twilio-websockets) ·
  [Cekura×Pipecat](https://www.cekura.ai/partners/pipecat) · [OTel/SigNoz](https://signoz.io/docs/pipecat-monitoring/)
- Cekura: [red teaming](https://docs.cekura.ai/documentation/red-teaming/overview) ·
  [scenario guide](https://www.cekura.ai/blogs/complete-cekura-scenario-testing-guide) ·
  [API ref](https://docs.cekura.ai/api-reference) · [MCP/Claude Code](https://docs.cekura.ai/mcp/claude-code-guide)
- Twilio: [ConversationRelay TwiML](https://www.twilio.com/docs/voice/twiml/connect/conversationrelay) ·
  [CR product](https://www.twilio.com/en-us/products/conversational-ai/conversationrelay) ·
  [CR + AWS architecture](https://www.twilio.com/en-us/blog/conversation-relay-aws-reference-architecture)
- NVIDIA: [Riva](https://developer.nvidia.com/riva) · [NIM speech](https://build.nvidia.com/explore/speech) ·
  [Parakeet](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3)
- AWS: [AgentCore pricing](https://aws.amazon.com/bedrock/agentcore/pricing/) ·
  [AgentCore FAQs](https://aws.amazon.com/bedrock/agentcore/faqs/) ·
  [Pipecat + AgentCore](https://aws.amazon.com/blogs/machine-learning/deploy-voice-agents-with-pipecat-and-amazon-bedrock-agentcore-runtime-part-1/) ·
  [Nova Sonic + AgentCore](https://aws.amazon.com/blogs/machine-learning/scalable-voice-agent-design-with-amazon-nova-sonic-multi-agent-tools-and-session-segmentation/)
