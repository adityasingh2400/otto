# Otto — submission notes (YC Voice Agents Hackathon)

**Otto turns any business website into a phone agent, then attacks it with a synthetic-caller swarm
until it's safe to go live — and keeps healing itself on every real call.** The auto-improve loop *is*
the product: detect failures across the whole event stream, author a fix, re-test on the same suite,
ship only if it's strictly better.

This file answers the three judging prompts directly: how we used **Cekura**, **Nemotron**, and
**Pipecat**, and exactly how much we improve agent performance — measured rigorously, reproducibly.

---

## 1. Cekura — evaluating & improving agent performance

**What we set out to do:** make "is this agent good enough to put on a real phone line?" a *measured*
gate, not a vibe — and then close the loop so the agent improves itself against that gate.

**How Cekura is wired (`apps/orchestrator/app/cekura.py`):** our live agent exposes a Daily WebRTC
room (`apps/agent/daily_runner.py`); Cekura's simulated callers join it via
`POST /test_framework/v1/scenarios-external/run_scenarios_pipecat/`, and we poll
`/results/{id}/` for per-scenario `success` + `expected_outcome`. Scenarios/metrics are pre-created and
mapped in `.env` (`CEKURA_SCENARIO_MAP`, `CEKURA_AXIS_SCENARIO_MAP`, `CEKURA_AGENT_ID`). The
production swarm-heal mutates one failure into many variations along caller-behaviour **axes** (accent,
noise, anger, language, …); `_resolve_scenarios` collapses those to **one real voice call per axis**,
so a 30-variation heal runs ~10 real calls instead of 30 — honest coverage, watchable runtime.
Completed live calls are logged back to Cekura observability (`/observability/v1/observe/`).

**The eval is rigorous and the improvement is *earned*, not asserted** (`app/swarm.py`,
`app/report.py`):

- **Paired before→after on ONE fixed adversarial suite.** Round 1 is the bare extracted agent; the
  final certification round is after self-heal. *Same scenarios both times.* The headline number is
  `report["improvement"]`: `before`, `after`, `absolute_gain`, `relative_gain`, `failures_eliminated`,
  per-category before/after, scenario count, heal rounds, backend, model.
- **Every verdict is gated by a deterministic, model-independent failure taxonomy** (`app/failure.py`,
  21 detectors across 4 dimensions: conversation / action / outcome / experience) — so the score
  can't drift with judge mood. An LLM judge adds a conversational second opinion on top.
- **Monotonic by construction** (`app/heal.py:safe_apply`): a candidate fix ships only if it regresses
  **zero** currently-passing scenarios. The agent literally cannot make itself worse — `regressed` is
  empty in every shipped version, and rejected patches are recorded with the scenarios they'd have
  broken.

**Backends, auto-selected:** `cekura` (real voice, when keyed + a Daily room is up) → `sim` (real
Nemotron-in-the-loop conversations + real tool calls) → `static` (deterministic policy-coverage, zero
keys). All three feed the *same* before/after machinery, so the report is honest about which ran
(`report["backend"]`).

**Result:** a bare extracted agent enters the swarm well under the 85% safety gate (it over-promises,
guesses availability, mishandles allergies/large-parties/escalation) and **self-heals past the gate
within a few rounds, with regression-proof monotonic improvement** — and the exact before/after for
any run is in its shareable evaluation report (`/report/?s=<session>`). Nothing is hardcoded; the heal
genuinely changes the spec, which genuinely changes the score.

## 2. Nemotron (open weights) — and custom sound detection that beats plain STT

**Open-weights Nemotron, used three ways** (all via NIM, OpenAI-compatible — `app/llm.py`,
`apps/agent/bot.py`):

- **Extraction** runs on **Nemotron Super** (`EXTRACT_MODEL`, `app/config.py`): one deep pass over the
  whole site (~16k chars) → a rich spec (menus, prices, policies, edge cases). It's the slow, careful
  read (~50s); the dashboard advertises the time and fills the wait with a live ETA + a staggered
  stream of what it's doing, so the depth is a feature, not a stall.
- **Swarm** (callers + judge) runs on **fast Nemotron** (`SWARM_SIM_MODEL`) so the arena stays live
  across dozens of calls and heal rounds. Right model for the right job — rich where it counts, fast
  where it's hot.
- **The live agent** answers on Nemotron (tool-calling) inside the Pipecat pipeline.

**Custom sound detection vs a normal STT agent — the rigorous number.** A normal voice agent reasons
over the STT *transcript*: words on a page. It is structurally blind to **how** something was said.
Otto attaches paralinguistic features to every caller turn (`CallEvent.audio`: ASR confidence,
energy/volume, arousal, SNR, language, barge-in, disfluency) and runs five signal-driven detectors on
the same taxonomy: unparseable caller (accent/mumble), shouting/distress, noisy line, talked-over
(barge-in), language switch.

We benchmark this honestly by holding the taxonomy fixed and toggling exactly one thing — whether the
audio features are present (`apps/orchestrator/scripts/paraling_bench.py`, no API keys, runs in CI):

```
                                            plain STT     Otto
  Caller the model can barely parse           MISSED     caught
  Shouting / distressed caller                MISSED     caught
  Noisy line (street / crosstalk)             MISSED     caught
  Caller talked over (barge-in)               MISSED     caught
  Caller switches to Spanish                  MISSED     caught
  [control] phantom confirmation (text)       caught     caught   ← both, by design
```

**A plain STT transcript catches 0 of the voice-quality failures; Otto's Nemotron audio layer catches
them all** — the same deterministic taxonomy, the *only* difference being the paralinguistic features.
The text-visible control proves the baseline isn't simply broken: where the failure *is* in the words,
both catch it. Run it yourself: `uv run python scripts/paraling_bench.py`.

## 3. Pipecat — voice

The live phone agent is a Pipecat pipeline (`apps/agent/bot.py`): transport → **NVIDIA STT** →
live-tap (streams the verbatim transcript, *keeping* the `uhh`s and `ehm`s a normal transcriber
scrubs — that's the paralinguistic signal) → Nemotron LLM (tool-calling) → TTS → transport, over
Twilio (telephony) or Daily (WebRTC, which is also how Cekura's swarm dials in). The trace simulator
drives tools through the *same* OpenAI-format tool schema the live agent registers, so the pre-launch
swarm exercises the agent exactly as production does.

---

## Reproduce

```bash
# rigorous paralinguistic-vs-STT benchmark (deterministic, no keys)
cd apps/orchestrator && uv run python scripts/paraling_bench.py

# full hermetic test suite (failure taxonomy, monotonic heal, regression guard, live path)
cd apps/orchestrator && uv run --with pytest python -m pytest -q

# end-to-end with real Nemotron eval (set SWARM_MODE=local, NVIDIA_API_KEY): the dashboard shows
# the agent built, attacked, healed past the 85% gate, then the before→after in the report.
```
