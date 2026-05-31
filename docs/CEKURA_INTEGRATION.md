# Cekura closed-loop integration — readiness spec

**Goal:** the live, closed, production loop — **Cekura detects → Otto heals → Cekura re-confirms.**
Friend is building the deep Cekura testing + functionality; this doc is the contract + wiring plan
so we integrate fast and run it e2e the moment that code lands.

Status today (verified by reading the tree, 2026-05-30): Cekura is wired as a *swappable swarm
backend* behind `SWARM_MODE=cekura`, and observability is an **inert stub**. The heal spine is
complete and backend-agnostic. Closing the loop is ~2 small adds + 1 e2e bring-up, not a rewrite.

---

## The three seams (all already exist in code)

```
                         ┌─────────────────── Seam A: GENERATION / RE-EVAL ───────────────────┐
  live call ──▶ DETECT ──▶ observe_trace() ──▶ taxonomy ──▶ probes ──▶ safe_apply ──▶ run_swarm
   (Seam B)      │            (heal spine, backend-agnostic — Seam C, already done)      │
                 │                                                                       ▼
                 └── Cekura (B1 push observe / B2 webhook) ──┐              cekura.run_suite over Daily
                                                             └──▶ this is "Cekura re-confirms it"
```

### Seam A — Re-eval over Daily (the "Cekura confirms" half) — EXISTS
- `swarm.py:run_swarm` → `cekura.run_suite(session, spec, personas, round)` when
  `SWARM_MODE=cekura` and `cekura_available()`. Falls back `cekura → sim → static` on ANY exception.
- `cekura.run_suite` (`apps/orchestrator/app/cekura.py`): resolves personas→scenario ids,
  **collapses by mutation axis** (30 variations → ~10 real voice calls, one per axis), POSTs
  `/test_framework/v1/scenarios-external/run_scenarios_pipecat/` with `{scenario, pipecat_room_url, pipecat_token}`,
  polls `/test_framework/v1/results/{id}/` until terminal, maps each run → `CallResult(backend="cekura")`.
- Scenario resolution `_resolve_scenarios` (cheapest first): `CEKURA_SCENARIO_MAP[persona.id]` →
  `CEKURA_AXIS_SCENARIO_MAP[persona.axis]` → process cache → `_create_scenario` (`real_world_smart`,
  needs `CEKURA_AGENT_ID`). Axis collapse keyed by `_scenario_key` = `axis:<axis>` or `id:<id>`.

### Seam B — Detection (the "Cekura detects" half)
- **B1 — push, EXISTS but INERT:** `observe.py:observe_call` and `bot.py` can call `cekura.observe()`
  → `POST /observability/v1/observe/`. **Bug:** called with `agent_id=0`, no `recording_url`,
  fire-and-forget, response swallowed → Cekura computes nothing. Must pass real `CEKURA_AGENT_ID`,
  full transcript, recording url.
- **B2 — callback, MISSING:** no `POST /cekura/webhook` in `main.py`. This is the true detection
  trigger: Cekura's production monitor flags a live call against a metric and calls us back. Map the
  payload → `CallTrace` (preferred — reuse our taxonomy) **or** → `FailureInstance[]` directly, then
  call the EXISTING `observe_trace(session_id, trace)`. That's the whole closed loop: **B2 → C → A.**

### Seam C — Heal (backend-agnostic) — DONE, do not touch
- `observe.py:observe_trace(session_id, trace)` is the spine: `failure.evaluate` → optional
  `classify_dynamic` (ANOMALY_LLM) → `cluster`/`summarize` → `_probe_persona` per failure →
  `_variations` (mutators w/ axis) → `run_swarm` (pre) → `heal.safe_apply` (regression-guarded,
  monotonic) → `run_swarm` (post). Emits `failure_report`, `patch`, `regression_check`, `spec`,
  `live_call`. **A production heal is regression-guarded against the pre-launch suite — it can never
  heal the line out of its launch safety.**

---

## Data contracts (what the friend's Cekura module must bridge)

Orchestrator's internal currency:
- **`CallTrace`** (`packages/spec/.../trace.py`): `events: [CallEvent]`. `CallEvent.kind ∈
  hear|say|tool_call|tool_result`; hear carries `asr_conf` + optional `AudioFeatures`
  (snr_db, noise, volume_dbfs, speech_rate_wpm, lang, lang_switch, sentiment, arousal, barge_in,
  repeat_request, disfluency); tool_result carries `ok`, `result{}`, `latency_ms`. `t_ms` drives all
  latency/dead-air math — **real timestamps matter** (see known bug below).
- **`FailureInstance`** (`failure.py`): id, dimension (conversation|action|outcome|experience),
  severity, reason, `heal_policy_id` (root-cause key), `heal_rule`, `fix_space` (policy|code).
- **`AgentSpec`/`Policy`** (`models.py`): heal edits ONLY `policies`; `safe_apply` proves monotonicity.

Two valid bridge strategies for B2:
- **(a) reconstruct a `CallTrace`** from Cekura's transcript/recording → let our 21 detectors classify
  it. Best: reuses everything, keeps one taxonomy. Needs transcript + (ideally) audio signal/recording.
- **(b) map Cekura metric verdicts → `FailureInstance[]`** → skip our taxonomy for that call, go
  straight to `_probe_persona`/heal. Needs a metric→(heal_policy_id, heal_rule) mapping table.

---

## Open questions for the friend (answers needed to wire B2 + harden A)

1. **Webhook payload shape:** what does Cekura's production observability callback send? Fields:
   call_id, agent_id, which metric(s) failed, score, explanation, transcript, `recording_url`?
   Is there a signature/HMAC to verify?
2. **Run-result shape:** exact JSON of `results/{id}/` → `runs`. Current code assumes
   `runs` is a dict keyed by runId, each with `scenario`, `scenario_name`, `success`,
   `expected_outcome:{score, explanation[]}`, `error_message`. Confirm — and **expose the per-axis
   `score`** (we currently throw it away, keeping only bool pass + reason).
3. **Daily join path:** is `run_scenarios_pipecat` over `DAILY_ROOM_URL` the confirmed path, or
   websocket/SIP? Who creates the room — `daily_runner.py` (agent) or Cekura? (Resolve the
   `daily_runner.py:10` D3 TODO + DailyTransport params vs pinned Pipecat `>=0.0.96,<1.0`.)
4. **Metric → taxonomy map:** do we reconstruct a trace (strategy a) or do you give verdicts we map
   to our `heal_policy_id`s (strategy b)? If (b), need the mapping (e.g. Cekura "no de-escalation" →
   `caller_distress`/`de-escalate-distress`).
5. **Account/creds:** real `CEKURA_AGENT_ID`, pre-created scenario/personality/metric ids for
   `.env` (`CEKURA_SCENARIO_MAP`, `CEKURA_AXIS_SCENARIO_MAP`, `CEKURA_METRIC_IDS`,
   `CEKURA_PERSONALITY_ID`)? Or rely on `_create_scenario` on-the-fly? Pre-create is the demo-safe path.

---

## e2e wiring checklist (when the code lands)

- [ ] **Creds + health:** `.env` has `CEKURA_API_KEY`, `CEKURA_AGENT_ID`, scenario/metric maps;
      `SWARM_MODE=cekura`; `DAILY_ROOM_URL`(+token). `GET /api/health` → `cekura:on`.
- [ ] **Daily bring-up:** agent joins the room (`daily_runner.py`); one `run_scenarios_pipecat`
      scenario completes and `_poll_results` parses it. Confirm DailyTransport params.
- [ ] **Honest backend:** `run_suite` surfaces the real Cekura `expected_outcome.score` in the
      `call`/`metrics` events (not just bool); **log loudly on silent fallback** so a "real" run can't
      quietly degrade to the local sim mid-demo.
- [ ] **Seam B1:** fix `cekura.observe()` — real `agent_id`, transcript, `recording_url`, don't
      swallow the response.
- [ ] **Seam B2:** add `POST /cekura/webhook` in `main.py` → verify key/signature → map payload
      (strategy a or b) → `observe_trace()` → append to live feed + publish `call_recorded` (mirror
      `observe_route`). Emit a `fact`/`failure_report` so the UI shows "Cekura flagged …".
- [ ] **Loop closes:** post-heal `run_swarm` in `observe_trace` runs over Cekura (axis-collapse) →
      the real "Cekura re-confirms" number.
- [ ] **Dashboard:** ride existing event types — already rendered: `call, spec, stage, metrics,
      patch, fact, swarm_report, regression_check, failure_report, discovery, live_*, call_recorded,
      awaiting_deploy, activated`. New Cekura events render only if added to the JS dispatch
      (`apps/web/app/index.html`); prefer reusing `fact`+`failure_report` for zero UI work.
- [ ] **Tests:** add stubbed `run_suite`/`_poll_results` coverage of the REAL response-shape parsing
      (today only `test_cekura_axis_scenario_reuse` exists, and it bypasses the network); add a
      webhook-payload→`observe_trace` test with a recorded Cekura fixture. `conftest.py` strips
      `CEKURA_API_KEY`, so the default suite stays hermetic — keep it that way.

---

## Live-path trace grounding — DONE (2026-05-30, ROADMAP #3)

`bot.py` used to reconstruct the trace with fabricated `t_ms = i*1500`, so on the real-agent path the
experience detectors (`dead_air`, `slow_action`, `perceived_latency`) ran on made-up timing. Now the
live taps (`_LiveTap`/`_AgentTap`) + tool handler record each finalized hear/say/tool into `rec` with
its **real monotonic `t_ms`** (sourced from the Pipecat frame stream — the agent turn stamped at
`LLMFullResponseStartFrame`, i.e. when the caller stops waiting), and `_report_to_orchestrator` builds
the trace from that real event log — falling back to the `i*1500` reconstruction ONLY if taps captured
nothing (e.g. the s2s pipeline, which has no taps). Verified: a 6.2s silence the old path was blind to
now fires `dead_air` + `perceived_latency`; taps confirmed against pipecat 1.3.0 via `run_test`;
orchestrator suite green (50). Fixture traces (`traces.py`) already carried real `t_ms`, so demo
replays were always fine — this hardens the LIVE path that feeds the production heal loop.

## Files when wiring (Cekura)
`main.py` (+`POST /cekura/webhook`) · `cekura.py` (`observe()` agent_id/recording; surface scores in
`run_suite`) · `observe.py` (B1 call site) · `daily_runner.py` (friend owns — confirm Daily params) ·
`tests/test_loop.py` (API-path + webhook tests) · `.env(.example)` (friend owns Cekura/Daily creds).
