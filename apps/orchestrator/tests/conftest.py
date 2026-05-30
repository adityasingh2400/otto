"""Hermetic test environment.

The loop is designed to run deterministically with no keys (static policy-coverage checks +
canonical fixes). But once a developer puts a real LLM key in `.env`, `config` would pick it up
and the swarm/observe paths would hit the network — slow and flaky in tests. This sets the
environment BEFORE `app.config` imports (and `load_dotenv(override=False)` therefore won't
override it), forcing the deterministic path regardless of `.env`.
"""

import os

os.environ["SWARM_MODE"] = "static"      # deterministic policy-coverage checks, no LLM sims
os.environ["LLM_PROVIDER"] = "none"       # llm_available() -> False
os.environ["HEAL_USE_LLM"] = "0"          # canonical fixes, no LLM heal calls
os.environ["REQUIRE_APPROVAL"] = "0"      # auto-activate so tests can assert the `activated` event
# Strip every credential a real `.env` might carry, so tests never hit the network — and, crucially,
# never fire a REAL Twilio SMS to OWNER_PHONE/DEMO_RESERVER_PHONE when mock_services books a slot.
for _k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "NVIDIA_API_KEY", "AWS_ACCESS_KEY_ID",
           "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER", "OWNER_PHONE",
           "DEMO_RESERVER_PHONE", "CEKURA_API_KEY"):
    os.environ[_k] = ""
