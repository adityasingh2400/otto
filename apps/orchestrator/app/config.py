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
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemma-4-31b-it")  # Gemma 4 for server-side reasoning

# Swarm / eval
SWARM_MODE = os.getenv("SWARM_MODE", "local")  # local | cekura
PASS_GATE = _f("PASS_GATE", 0.85)
SWARM_PERSONAS = _i("SWARM_PERSONAS", 12)
MAX_HEAL_ROUNDS = _i("MAX_HEAL_ROUNDS", 3)
# production loop: how many synthetic calls to throw at a failure detected on a live call
PRODUCTION_SWARM_VOLUME = _i("PRODUCTION_SWARM_VOLUME", 30)

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
    if LLM_PROVIDER == "bedrock":
        return bool(os.getenv("AWS_ACCESS_KEY_ID"))
    return False


def cekura_available() -> bool:
    return bool(CEKURA_API_KEY)
