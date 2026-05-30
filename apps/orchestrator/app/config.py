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
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemma-3-27b-it")  # open-weights Gemma for server-side reasoning ($0 path)
# NVIDIA Nemotron (open-weights) via NIM — OpenAI-compatible, so LLM_PROVIDER=nvidia is a base_url swap.
# This is the hackathon's "NVIDIA-accelerated open-weights" theme + the Daily/NVIDIA voice blueprint LLM.
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
NVIDIA_BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "nvidia/llama-3.3-nemotron-super-49b-v1")  # confirm exact id on build.nvidia.com
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

# failure-taxonomy thresholds (the event-stream eval engine, app/failure.py)
ACTION_SLA_MS = _i("ACTION_SLA_MS", 1500)        # a tool slower than this = a slow_action failure
ACTION_SLA_HIGH_MS = _i("ACTION_SLA_HIGH_MS", 3000)  # ... slower than this = high severity
DEAD_AIR_MS = _i("DEAD_AIR_MS", 4000)            # silent gap (no say/tool) longer than this = dead_air
LARGE_PARTY_MIN = _i("LARGE_PARTY_MIN", 6)       # party_size >= this must not go through the normal booking tool
REDUNDANT_CALL_MAX = _i("REDUNDANT_CALL_MAX", 2) # same tool called more than this = a loop

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

# Telephony
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")
OWNER_PHONE = os.getenv("OWNER_PHONE", "")  # business owner's number for event SMS alerts

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
