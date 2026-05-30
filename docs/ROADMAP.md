# Otto — roadmap / backlog

**Ordering principle: FUNCTIONAL FIRST.** Get the live demo end-to-end and rock-solid (the phone
call on the Nemotron stack, then the real Cekura swarm) before any of the deeper integrations
below. These are "win-harder" upgrades to pick up once the core demo is green — not blockers.

## Now (functional — in progress)
- [ ] Live phone call: Twilio → Pipecat → **Nemotron** → Deepgram/Cartesia (reliable), then upgrade speech to NVIDIA Parakeet/Magpie. (Need: Twilio Account SID + number, ngrok.)
- [ ] Real **Cekura** swarm + production webhook → the targeted heal loop (day-of, co-host credits). Highest-leverage for the *grand* prize since Cekura co-hosts/judges; code already written to their API.
- [ ] **Headless render for pure-JS SPAs.** The crawl is httpx+BeautifulSoup (no JS). We harvest `<title>`/meta/OG + **JSON-LD schema.org** (rescues most "SPAs" — Shopify/Squarespace inject it; verified on Farmgirl/Rainbow), infer the vertical from the domain so the right red-team still runs, and report an honest `extraction.confidence` when a page yields nothing. The irreducible ~20% (a site with NO server HTML, NO JSON-LD, AND a non-descriptive domain — e.g. a custom-React salon under a person's name) needs a real render. Add a Playwright fetch path behind a flag (`CRAWL_JS=1`), used only when the static crawl comes back thin. Deferred because it's a heavy dep and day-of extraction is one-URL-at-a-time on businesses that mostly server-render.

---

## Deferred — high-leverage sponsor integrations (build after functional is green)

### 1. ★ NVIDIA safety models → a failure-taxonomy dimension + (optional) runtime gate
**The single highest-leverage NVIDIA integration we don't yet have.** Today we use Nemotron as a
*generic* LLM. NVIDIA ships purpose-built **safety classifiers** on the **same OpenAI-compatible NIM
endpoint** we already use — verified present on our key's 118-model list:
- `nvidia/llama-3.1-nemotron-safety-guard-8b-v3` — content-safety classifier (Aegis-style: labels a
  message safe/unsafe across harm categories).
- `nvidia/nemotron-3-content-safety`, `nvidia/nemotron-content-safety-reasoning-4b` — content-safety models.

**Why it wins:** Otto's whole thesis is *safety* ("safe to let AI answer your phone"). Running the
agent through NVIDIA's *differentiated* safety models (not just Nemotron-as-an-LLM):
- makes the NVIDIA usage **deep, not cosmetic** → far stronger NVIDIA judges'-prize story;
- **directly amplifies the grand-prize safety thesis** (the eval now includes NVIDIA's safety classifier);
- is **low-risk** — same `https://integrate.api.nvidia.com/v1`, same `nvapi-` key, OpenAI-compatible
  (NO gRPC, no Colang/`annoy`/C++ — this is the safe realization of the "NeMo Guardrails" idea).

**Two integration points (both ride the existing OpenAI-compatible `llm` path):**

**A) A safety detector in the taxonomy — `apps/orchestrator/app/failure.py` (do this first).**
Add an LLM-judge detector `_d_nvidia_safety(spec, trace)` that classifies each agent `say` (and the
caller turn) with the safety-guard model; flag any turn labeled unsafe (gave medical/legal advice,
harmful content, etc.) as a **conversation-dimension** `FailureInstance` (id `unsafe_response`,
severity high/critical), with a heal that adds the matching `safety` policy.
- Touch points: a `NVIDIA_SAFETY_MODEL` config; a small `llm`-style call to the safety model (it
  returns a safe/unsafe label + categories — parse that, not freeform); register it in `DETECTORS`
  guarded on `config.llm_available()` (it's a judge detector, like `_judge_live`); add a fixture +
  test. Surface it as a new badge/row in the dashboard taxonomy panel ("NVIDIA safety: 0 unsafe").
- Effort ~1–2 hrs · risk **low** (server-side, off the live voice path).

**B) Runtime safety gate on the live agent — `apps/agent/bot.py` (optional, flagged).**
Post-LLM, before TTS, classify the drafted response with the safety model; if unsafe, swap for a safe
fallback / escalate. This is "the policy physically blocks the failure," not just a prompt.
- **Risk: medium** — it adds a per-turn round-trip → live-call latency. So gate it behind a flag
  (`SAFETY_GATE=1`) and prefer **log-and-score, don't block** on the live path; demo a *blocking*
  version as a short offline clip. Never put a blocking gate on the critical voice path for the live demo.
- Effort ~2–3 hrs + latency tuning.

**Build order:** A (taxonomy dimension — pure win) first, then B as an optional flagged gate.
**Verified:** model ids on the team's key; reuses the live `nvapi-` key + endpoint. No new account.

---

> **The unifying insight (gap-analysis verdict):** Otto's eval engine is 15 hand-rolled
> regex/heuristic detectors with **no external oracle** and **no content-safety dimension** — it
> can't see jailbreak / hate / self-harm / fraud-coaching, and a judge can say it "grades its own
> homework." The two highest-leverage adds below both fix exactly that: replace Otto's self-authored
> oracle with an **external** one. Same move, two sponsors (NVIDIA safety classifier; Cekura webhook).
> #1 above (NVIDIA safety-guard as a 5th dimension) is the build-first item.

### 2. Cekura webhook → the heal loop (external oracle for failure AND confirmation)
Today the failure oracle and the verification oracle are *both* Otto's own swarm (`observe.py`) — the
obvious judge critique. Add `POST /cekura/webhook` in `main.py` mapping a Cekura production-metric
failure → Otto's taxonomy → the EXISTING `observe_trace()` heal pipeline; and fix the inert stub
(`observe.py` calls `cekura.observe(agent_id=0)` fire-and-forget, no recording URL → Cekura computes
nothing). Payoff — the grand-prize sentence: **"Cekura detects it, Otto heals it, Cekura's re-eval
confirms it,"** plus real voice metrics (latency P90, barge-in, gibberish) the text sim can't produce.
Leverage HIGH · effort LOW-MED · risk = gated on day-of Cekura credits (tasks #13/#15), not code. Add
red-team scenarios so Cekura's vuln score (not grep) grades the safety dimension — ties into #1.

### 3. Observer-sourced CallTrace (Pipecat) + Smart Turn  [also fixes a real bug]
`bot.py::_report_to_orchestrator` reconstructs the trace with **fabricated timestamps**
(`t_ms = i * 1500`), so on the LIVE-agent path the *experience* detectors (`_d_dead_air`,
`_d_slow_action`) and any latency claim run on made-up timing. Ground CallTrace in Pipecat's
`BaseObserver` / turn+latency observers (already half-on via `enable_metrics=True`) → the experience
dimension becomes TRUE, and it's the strongest "built natively on the co-host's framework" signal.
(Fixture traces already carry real `t_ms`, so the demo replays are fine — this hardens the live path.)
Also **Smart Turn v3** sensitivity = a non-LLM behavioral knob the heal loop can tune ("we tune
behavior, not just words"), ~20 lines, CPU-only.

### Explicitly SKIP / traps (decided, not oversights)
- **AWS AgentCore** — heavy ARM64/microVM path, no clean mapping for continuous bidirectional Twilio
  audio = a single point of failure for a live call. **Pitch narrative only**, never the demo path.
- **Bedrock Guardrails** — same idea as #1 on a second cloud; redundant once #1 ships. Keep as a
  "multi-cloud editable policy / defense-in-depth" talking point.
- **Twilio ConversationRelay** — would HIDE the STT/TTS/turn surfaces Otto exists to heal. Reject on
  purpose; Media Streams was the right architectural call.
- **NeMo Guardrails dependency · Cekura MCP · SMS · Voice Insights · Riva-as-safety · ACE ·
  Nova-as-brain** — framing or skip.

**Files when building:** `failure.py` (+`dimension="safety"` detector, extend `DIMENSIONS`) ·
`llm.py` (safety classifier alongside `_nvidia`) · `main.py` (`POST /cekura/webhook`) ·
`cekura.py`+`observe.py` (fix `agent_id=0`/recording stub) · `bot.py` (observer-sourced trace + Smart Turn).
