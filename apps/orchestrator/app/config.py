"""Environment config. Loads .env from the repo root if present."""

import json
import os
import pathlib

from dotenv import load_dotenv

# repo root is three levels up from this file (app/ -> orchestrator/ -> apps/ -> root)
_ROOT = pathlib.Path(__file__).resolve().parents[3]
load_dotenv(_ROOT / ".env")


def _f(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, default))
    except (TypeError, ValueError):
        return default


def _i(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, default))
    except (TypeError, ValueError):
        return default


ROOT = _ROOT
SPEC_DIR = _ROOT / "packages" / "spec"

# LLM (server-side reasoning)
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "nvidia")  # default: NVIDIA Nemotron via NIM (theme #1, open-weights)
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemma-3-27b-it")  # open-weights Gemma for server-side reasoning ($0 path)
# NVIDIA Nemotron (open-weights) via NIM — OpenAI-compatible, so LLM_PROVIDER=nvidia is a base_url swap.
# This is the hackathon's "NVIDIA-accelerated open-weights" theme + the Daily/NVIDIA voice blueprint LLM.
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
NVIDIA_BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "nvidia/nemotron-3-nano-30b-a3b")  # fast MoE; verified live on build.nvidia.com
# Extraction reads the WHOLE site (≈16k chars) into one structured spec, so it runs on a RICHER
# reasoning model (Nemotron Super 49B) than the swarm: deeper menus/policies/edge-cases, fewer misses.
# It's a single call that takes tens of seconds — the dashboard advertises the time and fills the wait.
EXTRACT_MODEL = os.getenv("EXTRACT_MODEL", "") or ("nvidia/nemotron-3-super" if LLM_PROVIDER == "nvidia" else NVIDIA_MODEL)
EXTRACT_EXPECTED_S = _i("EXTRACT_EXPECTED_S", 50)  # advertised deep-parse time → drives the live ETA/progress
# The swarm fires MANY calls (callers + judge, every persona, every heal round); keep those on the
# configured fast model (NVIDIA_MODEL) so the arena stays live even when EXTRACT_MODEL is a heavier
# reasoner — set SWARM_SIM_MODEL explicitly to pin a different one. The agent-under-test still answers
# on AGENT_NVIDIA_MODEL, and the gating verdict is the DETERMINISTIC failure taxonomy
# (model-independent) — the judge model only adds a conversational second opinion.
SWARM_SIM_MODEL = os.getenv("SWARM_SIM_MODEL", "") or NVIDIA_MODEL
CRAWL_MAX_PAGES = _i("CRAWL_MAX_PAGES", 6)  # pages to crawl when extracting from a website
# LLM resilience — server-side reasoning is latency-tolerant, so retry transient API errors (429/
# timeout/5xx) AND unparseable output with backoff, and cap outbound concurrency so a burst (many
# extractions/heals at once) queues instead of tripping a provider's rate limit. Directly answers
# "can it handle concurrent calling" without each call needing its own backoff.
LLM_MAX_RETRIES = _i("LLM_MAX_RETRIES", 3)         # attempts per call (1 = no retry)
LLM_RETRY_BASE_MS = _i("LLM_RETRY_BASE_MS", 800)   # exponential backoff base (0.8s, 1.6s, 3.2s …) + jitter
LLM_MAX_CONCURRENCY = _i("LLM_MAX_CONCURRENCY", 4) # max simultaneous outbound LLM calls (per event loop)

# Swarm / eval
SWARM_MODE = os.getenv("SWARM_MODE", "local")  # local (LLM sim if keyed, else static) | static (force) | cekura
PASS_GATE = _f("PASS_GATE", 0.85)
SWARM_PERSONAS = _i("SWARM_PERSONAS", 12)
SWARM_CONCURRENCY = _i("SWARM_CONCURRENCY", 6)  # lower to 1-2 for rate-limited free LLM tiers
SWARM_TURNS = _i("SWARM_TURNS", 4)  # caller<->agent exchanges per simulated call (each = 2 LLM calls)
MAX_HEAL_ROUNDS = _i("MAX_HEAL_ROUNDS", 3)
# HEAL_USE_LLM=1 has the LLM (e.g. Nemotron) author the policy fix from failure evidence — a
# nice "watch it reason" beat, but adds a call per heal round. Default off = instant curated
# fixes (snappy demo). The curated fix is always applied as a backstop regardless.
HEAL_USE_LLM = os.getenv("HEAL_USE_LLM", "0").lower() in ("1", "true", "yes")
# production loop: how many synthetic calls to throw at a failure detected on a live call
PRODUCTION_SWARM_VOLUME = _i("PRODUCTION_SWARM_VOLUME", 30)
# REQUIRE_APPROVAL (default ON): once the agent clears the gate, the pipeline does NOT self-deploy —
# it emits `awaiting_deploy` and waits for the owner to press Deploy (POST /api/activate). The human
# putting the line live is the trust beat. OFF = auto-activate the instant the gate clears (tests/smoke).
REQUIRE_APPROVAL = os.getenv("REQUIRE_APPROVAL", "1").lower() in ("1", "true", "yes")
# DEMO_PACING (default ON): small, intentional delays that make the UI legible — a beat after the
# agent is built so the profile lands, and a per-call stagger so the swarm arena fills card-by-card
# (the "watch it attack" process). Tests/smoke set it OFF so the hermetic suite stays fast.
DEMO_PACING = os.getenv("DEMO_PACING", "1").lower() in ("1", "true", "yes")

# Code-heal: route CODE_SPACE failures (tool-layer invariants no prompt can fix) to a coding agent
# that writes a real diff, verified by the trace-sim replay oracle. Sibling of the policy heal loop.
# Default ON when an LLM key is present (same posture as the trace sim); the loop is a strict no-op
# when there are no code-space failures, so it's safe to leave enabled.
CODE_HEAL = os.getenv("CODE_HEAL", "1").lower() in ("1", "true", "yes")
# CODE_HEAL_APPLY=1 writes the verified patch back to the file (and the pipeline can open a PR).
# Default OFF: produce the verified diff + red→green proof, but DON'T mutate the working tree —
# shipping code has a bigger blast radius than hot-swapping one agent's policy, so a human/CI merges.
CODE_HEAL_APPLY = os.getenv("CODE_HEAL_APPLY", "0").lower() in ("1", "true", "yes")
CODE_HEAL_MAX_HOPS = _i("CODE_HEAL_MAX_HOPS", 2)  # author→verify→retry rounds per failure (budget cap)
CODE_HEAL_MODEL = os.getenv("CODE_HEAL_MODEL", "claude-haiku-4-5")  # cheap by default ($50 budget)
# Escalation: if the cheap model can't produce a VERIFIED fix within CODE_HEAL_MAX_HOPS, take one more
# attempt on a stronger model. Set empty to disable escalation. Costs more only when the cheap model fails.
CODE_HEAL_ESCALATE_MODEL = os.getenv("CODE_HEAL_ESCALATE_MODEL", "claude-sonnet-4-6")
# CODE_HEAL_MERGE=1 turns on the LIVE-LINE merge path (production loop): a locally-verified fix is gated
# a SECOND time by the conversational harness (Cekura when keyed, else the local sim) against the full
# pre-launch suite; only if THAT passes is the fix written to the tool layer, hot-reloaded into the
# running line, and committed. If the harness regresses, the fix is rolled back. Default OFF.
CODE_HEAL_MERGE = os.getenv("CODE_HEAL_MERGE", "0").lower() in ("1", "true", "yes")
CODE_HEAL_COMMIT = os.getenv("CODE_HEAL_COMMIT", "1").lower() in ("1", "true", "yes")  # git-commit a merged fix

# failure-taxonomy thresholds (the event-stream eval engine, app/failure.py)
ACTION_SLA_MS = _i("ACTION_SLA_MS", 1500)        # a tool slower than this = a slow_action failure
ACTION_SLA_HIGH_MS = _i("ACTION_SLA_HIGH_MS", 3000)  # ... slower than this = high severity
DEAD_AIR_MS = _i("DEAD_AIR_MS", 4000)            # silent gap (no say/tool) longer than this = dead_air
LARGE_PARTY_MIN = _i("LARGE_PARTY_MIN", 6)       # party_size >= this must not go through the normal booking tool
REDUNDANT_CALL_MAX = _i("REDUNDANT_CALL_MAX", 2) # same tool called more than this = a loop

# paralinguistic / voice-anomaly thresholds (the signal-driven detectors plain observability misses).
# All operate on CallEvent.audio (AudioFeatures) + asr_conf — absent → the detector no-ops.
ACCENT_ASR_CONF = _f("ACCENT_ASR_CONF", 0.6)        # a hear below this = low intelligibility (accent/mumble/non-native)
ACCENT_MIN_LOW = _i("ACCENT_MIN_LOW", 2)            # this many STRUGGLING hears (low-conf / disfluent / repeat) = a sustained gap
DISFLUENCY_MIN = _f("DISFLUENCY_MIN", 0.18)         # audio.disfluency >= this, OR >=2 verbatim fillers, = a disfluent (hesitant/struggling) turn
DISTRESS_AROUSAL = _f("DISTRESS_AROUSAL", 0.7)      # arousal >= this = agitated / shouting
DISTRESS_VOLUME_DBFS = _f("DISTRESS_VOLUME_DBFS", -6.0)  # louder than this (closer to 0) = shouting
DISTRESS_SENTIMENT = _f("DISTRESS_SENTIMENT", -0.4) # sentiment <= this = upset
NOISE_SNR_DB = _f("NOISE_SNR_DB", 12.0)             # SNR at/below this = a noisy line
NOISE_MIN_TURNS = _i("NOISE_MIN_TURNS", 2)          # this many noisy/low-SNR turns = a sustained noise problem
PERCEIVED_LATENCY_MS = _i("PERCEIVED_LATENCY_MS", 5000)  # cumulative caller wait that *feels* slow across the call
BARGE_IN_LONG_SAY = _i("BARGE_IN_LONG_SAY", 140)    # agent say longer than this (chars) right before a barge-in = rambling
# ANOMALY_LLM=1 adds an LLM pass that classifies novel failure modes the named detectors miss
# (truly dynamic discovery). Default off so the deterministic engine stays snappy + key-free; the
# proposed fix still goes through the governance oracle + regression guard, so it can't ship unsafe.
ANOMALY_LLM = os.getenv("ANOMALY_LLM", "0").lower() in ("1", "true", "yes")

# Cekura
CEKURA_API_KEY = os.getenv("CEKURA_API_KEY", "")
CEKURA_BASE_URL = os.getenv("CEKURA_BASE_URL", "https://api.cekura.ai")
CEKURA_AGENT_ID = os.getenv("CEKURA_AGENT_ID", "")
CEKURA_PERSONALITY_ID = os.getenv("CEKURA_PERSONALITY_ID", "")
CEKURA_METRIC_IDS = [int(x) for x in os.getenv("CEKURA_METRIC_IDS", "").split(",") if x.strip().isdigit()]
try:
    # {"severe_allergy": 30, "large_party": 31, ...} — persona id -> Cekura scenario id
    CEKURA_SCENARIO_MAP = json.loads(os.getenv("CEKURA_SCENARIO_MAP", "") or "{}")
except json.JSONDecodeError:
    CEKURA_SCENARIO_MAP = {}
try:
    # {"accent": 40, "noise": 41, "anger": 42, ...} — mutation AXIS -> a reusable Cekura scenario.
    # The production swarm-heal mutates one failure into many variations along these axes; mapping
    # by axis lets the Cekura path reuse a handful of scenarios instead of creating one per variation.
    CEKURA_AXIS_SCENARIO_MAP = json.loads(os.getenv("CEKURA_AXIS_SCENARIO_MAP", "") or "{}")
except json.JSONDecodeError:
    CEKURA_AXIS_SCENARIO_MAP = {}

# Telephony
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")
OWNER_PHONE = os.getenv("OWNER_PHONE", "")  # business owner's number for event SMS alerts
# Demo fallback: when a caller's number is missing/garbled (ASR mis-hears digits), text this
# instead so a live test call always lands a real confirmation SMS. Empty in prod.
DEMO_RESERVER_PHONE = os.getenv("DEMO_RESERVER_PHONE", "")

# Daily (Cekura WebRTC swarm joins this room; also the agent's WebRTC transport)
DAILY_API_KEY = os.getenv("DAILY_API_KEY", "")
DAILY_ROOM_URL = os.getenv("DAILY_ROOM_URL", "")
DAILY_ROOM_TOKEN = os.getenv("DAILY_ROOM_TOKEN", "")


def llm_available() -> bool:
    if LLM_PROVIDER == "openai":
        return bool(OPENAI_API_KEY)
    if LLM_PROVIDER == "anthropic":
        return bool(ANTHROPIC_API_KEY)
    if LLM_PROVIDER == "gemini":
        return bool(GEMINI_API_KEY)
    if LLM_PROVIDER == "nvidia":
        return bool(NVIDIA_API_KEY)
    if LLM_PROVIDER == "bedrock":
        return bool(os.getenv("AWS_ACCESS_KEY_ID"))
    return False


def cekura_available() -> bool:
    return bool(CEKURA_API_KEY)
