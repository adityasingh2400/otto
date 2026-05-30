"""Provider-agnostic LLM wrapper for server-side reasoning (extraction, healing,
caller simulation, judging). Defaults to OpenAI; Anthropic supported. Bedrock/Gemini
are marked TODO(team) — flip them on for the AWS track. Imports are lazy + guarded so
a missing SDK or key never crashes the orchestrator; callers fall back to static mode.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
from typing import Any

from . import config


class LLMUnavailable(RuntimeError):
    pass


# One concurrency gate per event loop (keyed by loop so tests that spin fresh loops never collide).
# Bounds simultaneous outbound LLM calls: a burst of extractions/heals queues through N slots instead
# of all hitting the provider at once and tripping its rate limit.
_GATES: dict[asyncio.AbstractEventLoop, asyncio.Semaphore] = {}


def _gate() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    sem = _GATES.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(max(1, config.LLM_MAX_CONCURRENCY))
        _GATES[loop] = sem
    return sem


async def _retry(fn):
    """Retry a coroutine factory on transient failures (API 429/timeout/5xx, unparseable JSON) with
    exponential backoff + jitter. LLMUnavailable (missing key/SDK) is config, not transient — fail fast."""
    attempts = max(1, config.LLM_MAX_RETRIES)
    last: Exception | None = None
    for i in range(attempts):
        try:
            return await fn()
        except LLMUnavailable:
            raise
        except Exception as e:  # noqa: BLE001 — provider SDKs raise many error types; treat them uniformly
            last = e
            if i == attempts - 1:
                break
            await asyncio.sleep((config.LLM_RETRY_BASE_MS / 1000) * (2 ** i) + random.uniform(0, 0.3))
    raise last  # type: ignore[misc]


async def _dispatch(system: str, user: str, json_mode: bool, temperature: float) -> str:
    provider = config.LLM_PROVIDER
    async with _gate():
        if provider == "openai":
            return await _openai(system, user, json_mode, temperature)
        if provider == "anthropic":
            return await _anthropic(system, user, json_mode, temperature)
        if provider == "gemini":  # open-weights Gemma via Google's OpenAI-compatible endpoint ($0 path)
            return await _gemini(system, user, json_mode, temperature)
        if provider == "nvidia":  # NVIDIA Nemotron open-weights via NIM (OpenAI-compatible) — theme #1
            return await _nvidia(system, user, json_mode, temperature)
        # bedrock (AWS) runs in the agent via boto3; server-side here falls back to OpenAI if keyed.
        if config.OPENAI_API_KEY:
            return await _openai(system, user, json_mode, temperature)
        raise LLMUnavailable(f"no LLM configured for provider={provider}")


async def complete(system: str, user: str, *, json_mode: bool = False, temperature: float = 0.4) -> str:
    return await _retry(lambda: _dispatch(system, user, json_mode, temperature))


async def complete_json(system: str, user: str, *, temperature: float = 0.3) -> Any:
    # Retries cover BOTH a transient API error AND a model that returned prose we couldn't parse —
    # re-sampling often yields clean JSON. (complete() also retries the API layer; parse retries here.)
    async def once() -> Any:
        raw = await complete(system, user, json_mode=True, temperature=temperature)
        return _loads_lenient(raw)
    return await _retry(once)


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


async def _gemini(system: str, user: str, json_mode: bool, temperature: float) -> str:
    # Open-weights Gemma for non-latency-critical server-side reasoning (the $0 path; lines up
    # with the "open-weights models" theme). Google's OpenAI-compatible endpoint, no extra dep.
    if not config.GEMINI_API_KEY:
        raise LLMUnavailable("GEMINI_API_KEY not set")
    try:
        from openai import AsyncOpenAI
    except ImportError as e:  # pragma: no cover
        raise LLMUnavailable("openai SDK not installed") from e
    client = AsyncOpenAI(api_key=config.GEMINI_API_KEY,
                         base_url="https://generativelanguage.googleapis.com/v1beta/openai/")
    kwargs: dict[str, Any] = {
        "model": config.GEMINI_MODEL,
        "temperature": temperature,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = await client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content or ""


async def _nvidia(system: str, user: str, json_mode: bool, temperature: float) -> str:
    # NVIDIA Nemotron (open-weights) served via NIM — build.nvidia.com, OpenAI-compatible, so
    # this is the same client as the OpenAI/Gemini branches with a base_url swap. This is the
    # hackathon's "NVIDIA-accelerated open-weights" theme #1, and the Daily+NVIDIA voice
    # blueprint's LLM. response_format is omitted (NIM model support varies); the json prompts
    # say "JSON only" and _loads_lenient parses prose-wrapped output, so JSON tasks still work.
    if not config.NVIDIA_API_KEY:
        raise LLMUnavailable("NVIDIA_API_KEY not set")
    try:
        from openai import AsyncOpenAI
    except ImportError as e:  # pragma: no cover
        raise LLMUnavailable("openai SDK not installed") from e
    client = AsyncOpenAI(api_key=config.NVIDIA_API_KEY, base_url=config.NVIDIA_BASE_URL)
    # "detailed thinking off" = the Llama-Nemotron reasoning toggle. For our JSON tasks we want a
    # clean object, not a <think> trace (which both bloats latency and breaks parsing on big specs).
    sys = "detailed thinking off\n\n" + system
    resp = await client.chat.completions.create(
        model=config.NVIDIA_MODEL, temperature=temperature,
        messages=[{"role": "system", "content": sys}, {"role": "user", "content": user}],
    )
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


def _extract_balanced(s: str) -> str | None:
    """Return the first complete, brace-balanced JSON value in s (string-aware), so a reasoning
    trace or prose around the JSON — even with braces in it — doesn't corrupt the slice."""
    start = min((i for i in (s.find("{"), s.find("[")) if i != -1), default=-1)
    if start == -1:
        return None
    open_ch = s[start]
    close_ch = "}" if open_ch == "{" else "]"
    depth, in_str, esc = 0, False, False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return s[start : i + 1]
    return s[start:]


def _loads_lenient(raw: str) -> Any:
    """Parse JSON from real LLM output: strip reasoning traces (<think>…</think>), markdown fences,
    surrounding prose; balanced-brace scan; tolerate trailing commas. Robust across models/sites."""
    raw = (raw or "").strip()
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL | re.IGNORECASE).strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw[:4].lower() == "json":
            raw = raw[4:]
        raw = raw.strip()
    for candidate in (raw, _extract_balanced(raw)):
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            try:  # tolerate trailing commas before } or ]
                return json.loads(re.sub(r",(\s*[}\]])", r"\1", candidate))
            except json.JSONDecodeError:
                continue
    raise json.JSONDecodeError("no parseable JSON in model output", raw[:200], 0)
