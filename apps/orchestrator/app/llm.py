"""Provider-agnostic LLM wrapper for server-side reasoning (extraction, healing,
caller simulation, judging). Defaults to NVIDIA Nemotron via NIM (OpenAI-compatible);
OpenAI/Anthropic/Gemini also supported, Bedrock runs agent-side. Imports are lazy + guarded so
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


async def _dispatch(system: str, user: str, json_mode: bool, temperature: float,
                    model: str | None = None) -> str:
    provider = config.LLM_PROVIDER
    async with _gate():
        if provider == "openai":
            return await _openai(system, user, json_mode, temperature, model)
        if provider == "anthropic":
            return await _anthropic(system, user, json_mode, temperature, model)
        if provider == "gemini":  # open-weights Gemma via Google's OpenAI-compatible endpoint ($0 path)
            return await _gemini(system, user, json_mode, temperature, model)
        if provider == "nvidia":  # NVIDIA Nemotron open-weights via NIM (OpenAI-compatible) — theme #1
            return await _nvidia(system, user, json_mode, temperature, model)
        # bedrock (AWS) runs in the agent via boto3; server-side here falls back to OpenAI if keyed.
        if config.OPENAI_API_KEY:
            return await _openai(system, user, json_mode, temperature, model)
        raise LLMUnavailable(f"no LLM configured for provider={provider}")


async def complete(system: str, user: str, *, json_mode: bool = False, temperature: float = 0.4,
                   model: str | None = None) -> str:
    # `model` overrides the provider default for THIS call — e.g. a rich reasoner for extraction vs a
    # fast model for the high-volume swarm — without changing the provider or wiring a second client.
    return await _retry(lambda: _dispatch(system, user, json_mode, temperature, model))


async def complete_json(system: str, user: str, *, temperature: float = 0.3, model: str | None = None) -> Any:
    # Retries cover BOTH a transient API error AND a model that returned prose we couldn't parse —
    # re-sampling often yields clean JSON. (complete() also retries the API layer; parse retries here.)
    async def once() -> Any:
        raw = await complete(system, user, json_mode=True, temperature=temperature, model=model)
        return _loads_lenient(raw)
    return await _retry(once)


# ── native tool-calling (the trace simulator uses the SAME mechanism the live agent does) ──
def _oai_client_model() -> tuple[Any, str, str]:
    """Return (AsyncOpenAI client, model, system_prefix) for the active OpenAI-compatible provider.
    Tool-calling is only wired for the OpenAI-shaped providers (openai/gemini/nvidia) — the live
    voice agent calls tools through this exact API (Pipecat's OpenAILLMService on NIM), so the sim
    matches production. Anthropic/Bedrock have no tool path here → LLMUnavailable (caller falls back)."""
    provider = config.LLM_PROVIDER
    try:
        from openai import AsyncOpenAI
    except ImportError as e:  # pragma: no cover
        raise LLMUnavailable("openai SDK not installed") from e
    if provider == "nvidia":
        if not config.NVIDIA_API_KEY:
            raise LLMUnavailable("NVIDIA_API_KEY not set")
        import os
        # Prefer the agent's tool-capable Nemotron so the sim's tool-calling fidelity matches the live line.
        model = os.getenv("AGENT_NVIDIA_MODEL") or config.NVIDIA_MODEL
        return AsyncOpenAI(api_key=config.NVIDIA_API_KEY, base_url=config.NVIDIA_BASE_URL, timeout=config.LLM_TIMEOUT_S), model, "detailed thinking off\n\n"
    if provider == "gemini":
        if not config.GEMINI_API_KEY:
            raise LLMUnavailable("GEMINI_API_KEY not set")
        return (AsyncOpenAI(api_key=config.GEMINI_API_KEY,
                            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                            timeout=config.LLM_TIMEOUT_S),
                config.GEMINI_MODEL, "")
    if provider == "openai" or config.OPENAI_API_KEY:
        if not config.OPENAI_API_KEY:
            raise LLMUnavailable("OPENAI_API_KEY not set")
        return AsyncOpenAI(api_key=config.OPENAI_API_KEY, timeout=config.LLM_TIMEOUT_S), config.LLM_MODEL, ""
    raise LLMUnavailable(f"tool-calling not supported for provider={provider}")


def tool_calling_available() -> bool:
    """True when the active provider has a wired tool-calling path (so the trace sim can run)."""
    if config.LLM_PROVIDER == "anthropic":
        return bool(config.ANTHROPIC_API_KEY)
    try:
        _oai_client_model()
        return True
    except LLMUnavailable:
        return False


# ── Anthropic tool-use: same caller contract, different wire format ──────────────────────────
def _to_anthropic_tools(tools: list[dict]) -> list[dict]:
    out = []
    for t in tools:
        fn = t.get("function", t)
        out.append({"name": fn["name"], "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters") or {"type": "object", "properties": {}}})
    return out


def _to_anthropic_messages(messages: list[dict]) -> list[dict]:
    """Translate the OpenAI-format running history the sim builds into Anthropic blocks: an
    assistant `tool_calls` message becomes tool_use blocks; consecutive `tool` messages coalesce
    into one user message of tool_result blocks. A leading assistant turn (the greeting) is dropped
    because Anthropic requires the conversation to open on the user role."""
    out: list[dict] = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            block = {"type": "tool_result", "tool_use_id": m.get("tool_call_id", "") or "tool",
                     "content": m.get("content", "") or ""}
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
        elif role == "assistant" and m.get("tool_calls"):
            content: list[dict] = []
            if m.get("content"):
                content.append({"type": "text", "text": m["content"]})
            for tc in m["tool_calls"]:
                fn = tc["function"]
                raw = fn.get("arguments", "{}")
                try:
                    inp = _loads_lenient(raw) if isinstance(raw, str) else (raw or {})
                except Exception:
                    inp = {}
                content.append({"type": "tool_use", "id": tc.get("id", "") or "tool",
                                "name": fn["name"], "input": inp if isinstance(inp, dict) else {}})
            out.append({"role": "assistant", "content": content})
        else:
            out.append({"role": role, "content": m.get("content") or ""})
    while out and out[0]["role"] != "user":  # Anthropic must open on a user turn
        out.pop(0)
    return out


async def _anthropic_tools(system: str, messages: list[dict], tools: list[dict], temperature: float,
                           model: str | None = None, max_tokens: int = 1024, force_tool: bool = False) -> dict:
    if not config.ANTHROPIC_API_KEY:
        raise LLMUnavailable("ANTHROPIC_API_KEY not set")
    try:
        from anthropic import AsyncAnthropic
    except ImportError as e:  # pragma: no cover
        raise LLMUnavailable("anthropic SDK not installed") from e
    client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY, timeout=config.LLM_TIMEOUT_S)
    model = model or (config.LLM_MODEL if config.LLM_PROVIDER == "anthropic" else "claude-haiku-4-5")
    extra = {"tool_choice": {"type": "any"}} if force_tool else {}  # any => must call SOME tool
    async with _gate():
        resp = await client.messages.create(
            model=model, max_tokens=max_tokens, temperature=temperature, system=system,
            messages=_to_anthropic_messages(messages), tools=_to_anthropic_tools(tools), **extra)
    text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
    calls = [{"id": getattr(b, "id", "") or "", "name": b.name,
              "args": b.input if isinstance(b.input, dict) else {}}
             for b in resp.content if getattr(b, "type", "") == "tool_use"]
    return {"content": text, "tool_calls": calls}


async def complete_tools(system: str, messages: list[dict], tools: list[dict],
                         *, temperature: float = 0.3, model: str | None = None,
                         max_tokens: int = 1024, force_tool: bool = False) -> dict:
    """One tool-calling turn. `messages` is an OpenAI-format running history (user/assistant/tool);
    returns the assistant message as {"content": str, "tool_calls": [{"id","name","args"}]}. Raises
    LLMUnavailable if the provider has no tool path. Retries transient API errors with backoff.
    `model` overrides the provider default (e.g. pin a cheap model for budget-bounded code-heal);
    `force_tool` requires the model to call a tool rather than reply with prose."""
    if config.LLM_PROVIDER == "anthropic":
        return await _retry(lambda: _anthropic_tools(system, messages, tools, temperature, model, max_tokens, force_tool))

    client, default_model, sys_prefix = _oai_client_model()
    model = model or default_model
    sys_msg = {"role": "system", "content": sys_prefix + system}
    choice = "required" if force_tool else "auto"

    async def once() -> dict:
        async with _gate():
            resp = await client.chat.completions.create(
                model=model, temperature=temperature, max_tokens=max_tokens,
                messages=[sys_msg, *messages], tools=tools, tool_choice=choice)
        msg = resp.choices[0].message
        calls = []
        for tc in (getattr(msg, "tool_calls", None) or []):
            fn = getattr(tc, "function", None)
            if not fn:
                continue
            try:
                args = _loads_lenient(fn.arguments) if isinstance(fn.arguments, str) else (fn.arguments or {})
            except Exception:
                args = {}
            calls.append({"id": getattr(tc, "id", "") or "", "name": fn.name,
                          "args": args if isinstance(args, dict) else {}})
        return {"content": msg.content or "", "tool_calls": calls}

    return await _retry(once)


async def _openai(system: str, user: str, json_mode: bool, temperature: float, model: str | None = None) -> str:
    if not config.OPENAI_API_KEY:
        raise LLMUnavailable("OPENAI_API_KEY not set")
    try:
        from openai import AsyncOpenAI
    except ImportError as e:  # pragma: no cover
        raise LLMUnavailable("openai SDK not installed") from e
    client = AsyncOpenAI(api_key=config.OPENAI_API_KEY, timeout=config.LLM_TIMEOUT_S)
    kwargs: dict[str, Any] = {
        "model": model or config.LLM_MODEL,
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


async def _gemini(system: str, user: str, json_mode: bool, temperature: float, model: str | None = None) -> str:
    # Open-weights Gemma for non-latency-critical server-side reasoning (the $0 path; lines up
    # with the "open-weights models" theme). Google's OpenAI-compatible endpoint, no extra dep.
    if not config.GEMINI_API_KEY:
        raise LLMUnavailable("GEMINI_API_KEY not set")
    try:
        from openai import AsyncOpenAI
    except ImportError as e:  # pragma: no cover
        raise LLMUnavailable("openai SDK not installed") from e
    client = AsyncOpenAI(api_key=config.GEMINI_API_KEY,
                         base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                         timeout=config.LLM_TIMEOUT_S)
    kwargs: dict[str, Any] = {
        "model": model or config.GEMINI_MODEL,
        "temperature": temperature,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = await client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content or ""


async def _nvidia(system: str, user: str, json_mode: bool, temperature: float, model: str | None = None) -> str:
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
    client = AsyncOpenAI(api_key=config.NVIDIA_API_KEY, base_url=config.NVIDIA_BASE_URL, timeout=config.LLM_TIMEOUT_S)
    # "detailed thinking off" = the Llama-Nemotron reasoning toggle. For our JSON tasks we want a
    # clean object, not a <think> trace (which both bloats latency and breaks parsing on big specs).
    sys = "detailed thinking off\n\n" + system
    resp = await client.chat.completions.create(
        model=model or config.NVIDIA_MODEL, temperature=temperature,
        messages=[{"role": "system", "content": sys}, {"role": "user", "content": user}],
    )
    return resp.choices[0].message.content or ""


async def _anthropic(system: str, user: str, json_mode: bool, temperature: float, model: str | None = None) -> str:
    if not config.ANTHROPIC_API_KEY:
        raise LLMUnavailable("ANTHROPIC_API_KEY not set")
    try:
        from anthropic import AsyncAnthropic
    except ImportError as e:  # pragma: no cover
        raise LLMUnavailable("anthropic SDK not installed") from e
    client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY, timeout=config.LLM_TIMEOUT_S)
    sys = system + ("\n\nRespond with a single valid JSON object and nothing else." if json_mode else "")
    resp = await client.messages.create(
        model=model or (config.LLM_MODEL if config.LLM_PROVIDER == "anthropic" else "claude-sonnet-4-6"),
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
