# Otto — the failure taxonomy (the brain of Auto-Improve)

> "We aren't looking for the best-sounding voice; we're looking for the best **system**."

Most voice-agent evals judge one thing: *did the agent say the right words?* That misses the
failures that actually cost a business money. The agent can sound flawless while the **action**
it took was wrong, slow, failed, unauthorized, or never happened — and the caller hangs up
thinking they have a reservation they don't.

Otto evaluates the **whole call event stream**, not the transcript. A `CallTrace`
(`packages/spec/src/otto_spec/trace.py`) is an ordered stream of four event kinds:

```
hear        caller turn        { t_ms, text, asr_conf }
say         agent turn         { t_ms, text }
tool_call   agent invoked X    { t_ms, name, args }
tool_result the tool returned  { t_ms, name, ok, result, latency_ms, error }
```

The engine (`apps/orchestrator/app/failure.py`) runs every detector over the trace and
classifies failures across **four dimensions**. Most detectors are **deterministic** — they
catch exactly what a transcript LLM-judge is structurally blind to.

| dimension | the question | examples |
|---|---|---|
| **conversation** | what it *said* | hallucinated a fact, over-promised, missed an escalation |
| **action** | what it *did* | wrong / missing / failed / slow / unauthorized / looping tool use |
| **outcome** | the *end state* | confirmed a booking that never happened, double-booked, goal left unmet |
| **experience** | how it *felt* | latency the caller waited on, dead air |

## The classes (14 detectors, severity-routed)

Each detected failure **authors its own fix**: a concrete policy the self-heal loop writes,
then re-verifies with a targeted swarm (red → green). `[D]` deterministic · `[J]` LLM-judge.

### outcome — said-vs-did reconciliation (the cases voice evals miss)
| class | sev | fires when | heals to |
|---|---|---|---|
| `failed_action_masked` `[D]` | critical | a tool returned unavailable/error, then the agent told the caller it succeeded | `confirm-before-claiming` |
| `unconfirmed_success` `[D]` | high | agent claimed "booked/confirmed" but **no** tool returned a confirmation id | `confirm-before-claiming` |
| `succeeded_but_denied` `[D]` | high | a booking really succeeded, but the agent said it failed → caller retries | `surface-real-results` |
| `duplicate_side_effect` `[D]` | critical | two confirmed bookings (distinct ids) for one caller intent | `no-duplicate-booking` |
| `unconsented_alternative_booked` `[D]` | high | requested slot unavailable → agent booked an offered alternative with no "yes" | `confirm-alternative` |
| `unmet_goal_no_escalation` `[D]` | high | a tool failed and the call ended with no success **and** no escalation | `never-end-unresolved` |

### action — tool-execution mechanics
| class | sev | fires when | heals to |
|---|---|---|---|
| `missing_action` `[D]` | high | agent asserted availability/stock without calling the tool that checks it (guessed) | `availability-answer` |
| `wrong_action` `[D]` | high | a large party (≥6) booked through the normal reservation tool, not the large-party flow | `large-party-routing` |
| `unauthorized_action` `[D]` | critical | a payment/refund taken with no prior escalation/authorization | `authorize-sensitive-actions` |
| `pii_in_action` `[D]` | critical | a card number / SSN written into a tool argument (persisted into a record) | `no-pii-in-records` |
| `orphaned_action` `[D]` | high→crit | a tool was invoked but never returned — unknown side-effect (worst on writes) | `confirm-before-claiming` |
| `low_confidence_write` `[D]` | high | a booking/charge written off a low-ASR-confidence mishear, never read back | `read-back-uncertain` |
| `redundant_action` `[D]` | low | the same tool called > N times in one call (a retry loop) | `no-tool-loop` |

### experience — call rhythm
| class | sev | fires when | heals to |
|---|---|---|---|
| `slow_action` `[D]` | med→high | a tool the caller waited on exceeded the latency SLA (`ACTION_SLA_MS`) | `set-wait-expectations` |
| `dead_air` `[D]` | medium | a long silence after the caller spoke with no acknowledgement | `no-dead-air` |

The conversation dimension is also covered by the LLM-judge path (`observe.py::_judge_live`)
and, with no key, the per-persona policy-coverage checks in `personas.py` / `archetypes.py`.

## How a failure becomes a fix

```
live call → CallTrace ─▶ failure.evaluate(spec, trace) ─▶ [FailureInstance, …]
                                   │                          (dimension, severity, evidence, heal_rule)
            root-cause cluster ◀───┤   many symptoms → few underlying causes
                                   ▼
   each failure authors a Policy ─▶ safe_apply: REGRESSION-GUARD across the whole suite
                                   ▶ keep a patch only if it regresses nothing AND governs the gap
                                   ▶ new spec version → targeted probes re-verify (RED → GREEN) → redeploy
```

Nothing is hardcoded; the numbers come from real runs (verified: each fixture 0% → 100%).
Three properties make this a *system*, not a patcher:

### 1. It cannot make itself worse (the safety guarantee) — `heal.safe_apply`
A self-modifying agent is only trustworthy if it's **provably unable to regress**. Every candidate
patch is applied to a trial spec and validated against the **entire scenario suite** (the
pre-launch personas **and** the production probes). A patch that would break *any* currently-passing
scenario is **rejected and rolled back**, with the exact scenario it would have harmed. So the spec
that ships always passes a **superset** of what the old one did — healing is monotonic. A production
fix is checked against the pre-launch suite too, so the line can never heal itself out of its launch
safety. (Test: a patch re-introducing the allergy over-promise is rejected because `severe_allergy`
would regress.)

### 2. The verification oracle can't be gamed — `failure.governed`
The probe doesn't just check that a policy *id* exists (that would let the healer grade its own
homework with a `rule="lol"`). It checks the policy is **present, non-empty, and contains the
semantic tokens that genuinely close the gap** (`_GOVERN_KW`). A thin or no-op patch governs nothing,
fails the probe, and is **rejected as ineffective** — which also stops prompt-bloat and vanity
version bumps (the version only increments when a real fix ships).

### 3. The eval suite compounds from real traffic — `_d_anomaly` + the discovery registry
An anomaly detector flags off-pattern calls the 14 named detectors don't cover ("promised a text it
never sent," "hung up on an unanswered question"). A recurring signature is **promoted** into a
tracked failure mode and counted, so the suite **grows from production** instead of staying frozen at
design time — that's "next-generation evals," literally: the evaluator learns too.

### Root-cause clustering — `failure.cluster`
Many symptoms usually share one underlying gap: `failed_action_masked` + `unconfirmed_success` +
`orphaned_action` all trace back to *"claims success it can't back up."* The loop clusters by the
policy that resolves them and ships **one high-leverage fix per root cause**, not N band-aids.

## Thresholds (tunable, `.env` / `config.py`)
`ACTION_SLA_MS` (1500) · `ACTION_SLA_HIGH_MS` (3000) · `DEAD_AIR_MS` (4000) ·
`LARGE_PARTY_MIN` (6) · `REDUNDANT_CALL_MAX` (2) · `PRODUCTION_SWARM_VOLUME` (30).

## Hardening for production (known, by design)
The demo accepts a raw `trace` on `POST /api/observe` (and the curated `trace_id` fixtures) so the
loop is showable with zero keys. In production the raw-trace path should be **authenticated/signed**
(only the trusted runtime emits traces) and rate-limited (each call spins `PRODUCTION_SWARM_VOLUME`
swarms), and a discovered mode should require **provenance diversity** (distinct real callers, not N
replays) before promotion — otherwise a forged trace is a supply-chain vector into the agent's own
policies. The regression guard already bounds the blast radius: a forged trace still cannot ship a
patch that regresses the pre-launch suite.

## Try it (zero keys)
`apps/orchestrator/app/traces.py` ships realistic action-failure call traces. In the dashboard,
build any vertical, then under **Production loop** hit a "live call where the voice was fine but
the action went wrong" button — watch it classify across the four dimensions and heal the exact
policy. Or headless: `POST /api/observe/{sid} {"trace_id":"booked_soldout"}`.
