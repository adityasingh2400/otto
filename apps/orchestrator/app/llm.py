"""Provider-agnostic LLM wrapper for server-side reasoning (extraction, healing,
caller simulation, judging). Defaults to OpenAI; Anthropic supported. Bedrock/Gemini
are marked TODO(team) — flip them on for the AWS track. Imports are lazy + guarded so
a missing SDK or key never crashes the orchestrator; callers fall back to static mode.
"""

from __future__ import annotations

import json
from typing import Any

from . import config


class LLMUnavailable(RuntimeError):
    pass


async def complete(system: str, user: str, *, json_mode: bool = False, temperature: float = 0.4) -> str:
    provider = config.LLM_PROVIDER
    if provider == "openai":
        return await _openai(system, user, json_mode, temperature)
    if provider == "anthropic":
        return await _anthropic(system, user, json_mode, temperature)
    # TODO(team): bedrock (AWS track) + gemini. For now fall back to OpenAI if keyed.
    if config.OPENAI_API_KEY:
        return await _openai(system, user, json_mode, temperature)
    raise LLMUnavailable(f"no LLM configured for provider={provider}")


async def complete_json(system: str, user: str, *, temperature: float = 0.3) -> Any:
    raw = await complete(system, user, json_mode=True, temperature=temperature)
    return _loads_lenient(raw)


async def _openai(system: str, user: str, json_mode: bool, temperature: float) -> str:
    if not config.OPENAI_API_KEY:
        raise LLMUnavailable("OPENAI_API_KEY not set")
    try:
        from openai import AsyncOpenAI
    except ImportError as e:  # pragma: no cover
        raise LLMUnavailable("openai SDK not installed") from e
    client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
    kwargs: dict[str, Any] = {
        "model": config.LLM_MODEL,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = await client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content or ""


async def _anthropic(system: str, user: str, json_mode: bool, temperature: float) -> str:
    if not config.ANTHROPIC_API_KEY:
        raise LLMUnavailable("ANTHROPIC_API_KEY not set")
    try:
        from anthropic import AsyncAnthropic
    except ImportError as e:  # pragma: no cover
        raise LLMUnavailable("anthropic SDK not installed") from e
    client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
    sys = system + ("\n\nRespond with a single valid JSON object and nothing else." if json_mode else "")
    resp = await client.messages.create(
        model=config.LLM_MODEL if config.LLM_PROVIDER == "anthropic" else "claude-sonnet-4-6",
        max_tokens=2048,
        temperature=temperature,
        system=sys,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")


def _loads_lenient(raw: str) -> Any:
    """Parse JSON even if the model wrapped it in prose or a ```json fence."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = min((i for i in (raw.find("{"), raw.find("[")) if i != -1), default=-1)
        end = max(raw.rfind("}"), raw.rfind("]"))
        if start != -1 and end != -1:
            return json.loads(raw[start : end + 1])
        raise
