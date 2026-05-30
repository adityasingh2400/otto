"""Code-heal: the coding-agent sibling of the policy healer (heal.py).

Same loop, different writer. Where heal() takes a failure and has an LLM author a *policy*,
code_heal() takes a CODE_SPACE failure — one naming a tool-layer invariant no prompt can
guarantee (idempotency, a tool that always returns, a secure boundary) — and has a coding agent
author a *code diff* to the tool layer. The pieces map 1:1:

    detect          failure.evaluate(spec, trace)              ← identical engine
    synthesize      coding agent writes a diff                 ← was: LLM writes a Policy
    VERIFY          replay the recorded tool calls through the patched code, re-evaluate
                    → the target failure must be GONE and nothing new may break (monotonic,
                      the same guarantee safe_apply gives the policy path)            ← THE ORACLE
    ship            produce the verified diff (+ optionally write it / open a PR)

The verification is DETERMINISTIC and LLM-free: a code fix changes `tool_result` events, so we
replay the exact tool calls the agent already made against the patched implementation and let the
same detectors judge. No re-simulation, no stochasticity in the gate.

Anti-cheat (the code analog of failure.governed): the agent may edit ONLY the tool-impl file; it
cannot touch failure.py, the persona, or the trace, so it cannot make a failure "disappear" by
deleting its detector. The grader (failure.evaluate) is ours and runs on a pinned import.
"""

from __future__ import annotations

import difflib
import importlib.util
import sys
from dataclasses import asdict, dataclass, field
from typing import Awaitable, Callable, Optional

from otto_spec import AgentSpec, CallEvent, CallTrace

from . import config, failure, llm, tool_engine
from .events import bus
from .personas import Persona

# Tool result statuses that mean the action did NOT succeed (mirrors swarm.py / failure.py).
_FAIL_STATUS = {"unavailable", "out_of_stock", "sold_out", "error", "declined", "failed"}

# The ONLY file the coding agent is allowed to rewrite — the tool implementation layer. This is the
# code-space anti-cheat boundary: the grader (failure.py) and the repro (persona/trace) are off-limits.
TARGET_PATH = config.ROOT / "apps" / "orchestrator" / "app" / "mock_services.py"

CODE_SYS = (
    "You are a senior backend engineer fixing a STRUCTURAL bug in a phone agent's tool layer "
    "(mock_services.py). The agent's prompt is fine — the gap is in the CODE: an invariant the "
    "implementation must guarantee (idempotency, always returning a result, refusing a sensitive "
    "input, a correct status). Fix the root cause in the code, minimally, preserving every other "
    "behavior and the module's public interface. Return the COMPLETE corrected file via submit_patch."
)

_SUBMIT_TOOL = [{
    "type": "function",
    "function": {
        "name": "submit_patch",
        "description": "Submit the complete corrected contents of mock_services.py.",
        "parameters": {
            "type": "object",
            "properties": {"source": {"type": "string",
                                      "description": "the entire new file contents, top to bottom"}},
            "required": ["source"],
        },
    },
}]


@dataclass
class CodeFix:
    """One code-space failure run through the coding agent + replay oracle."""
    failure_id: str
    persona: str
    accepted: bool
    reason: str
    before_failures: list[str] = field(default_factory=list)  # ids present before the patch (incl. target)
    after_failures: list[str] = field(default_factory=list)   # ids present after  (target must be absent)
    diff: str = ""                                            # unified diff of the tool-layer file

    def to_dict(self) -> dict:
        return asdict(self)


# ── the verification oracle: replay recorded tool calls through a (patched) implementation ──
def _load_module(source: str, suffix: str):
    """Exec `source` as a standalone module that still resolves `from . import config` (relative to
    the `app` package). Returns the module object; raises on syntax error so the caller can reject."""
    name = f"app._codeheal_mock_{suffix}"
    spec_ = importlib.util.spec_from_loader(name, loader=None)
    mod = importlib.util.module_from_spec(spec_)
    mod.__dict__["__package__"] = "app"   # so the module's relative imports resolve to app.*
    mod.__dict__["__name__"] = name
    sys.modules[name] = mod
    exec(compile(source, f"<{name}>", "exec"), mod.__dict__)
    return mod


def _is_failure_result(result: dict) -> bool:
    return str((result or {}).get("status", "")).lower() in _FAIL_STATUS or bool((result or {}).get("error"))


async def replay(spec: AgentSpec, trace_dict: dict, persona: Optional[Persona], services_mod) -> list:
    """Re-execute the recorded tool_call events against `services_mod` (a fresh mock_services), rebuild
    the trace, and return the failures the SAME detectors find. say/hear events carry through unchanged;
    tool_result events are REGENERATED by the patched code — that's exactly what a code fix changes.
    Deterministic and LLM-free."""
    src_events = trace_dict.get("events", []) or []
    orig_results = [e for e in src_events if e.get("kind") == "tool_result"]
    seed = getattr(persona, "setup", None) if persona else None
    events: list[CallEvent] = []
    orig_mod = tool_engine.mock_services
    tool_engine.mock_services = services_mod   # route execute() through the patched implementation
    try:
        with services_mod.isolated_state(seed):
            ri = 0
            for ev in src_events:
                kind = ev.get("kind")
                if kind in ("say", "hear"):
                    events.append(CallEvent(kind=kind, t_ms=ev.get("t_ms", 0) or 0,
                                            text=ev.get("text", "") or "", asr_conf=ev.get("asr_conf")))
                    continue
                if kind != "tool_call":
                    continue
                name, args = ev.get("name"), ev.get("args") or {}
                orig = orig_results[ri] if ri < len(orig_results) else {}
                ri += 1
                events.append(CallEvent(kind="tool_call", t_ms=ev.get("t_ms", 0) or 0, name=name, args=args))
                # Re-execute through the (patched) tool layer; an executor error is itself a failed
                # outcome, not a crash — same contract the live sim uses.
                try:
                    result = await tool_engine.execute(spec, name, args)
                except Exception as e:  # noqa: BLE001
                    result = {"error": f"{type(e).__name__}: {e}"[:200]}
                # Preserve any INJECTED latency floor from the original result so a perturb-driven
                # slow_action can't be falsely "fixed" by replaying at instant speed.
                latency = max(int((result or {}).get("_latency_ms", 0) or 0), int(orig.get("latency_ms", 0) or 0))
                ok = not _is_failure_result(result if isinstance(result, dict) else {})
                events.append(CallEvent(kind="tool_result", t_ms=ev.get("t_ms", 0) or 0, name=name, ok=ok,
                                        result=result if isinstance(result, dict) else {}, latency_ms=latency))
    finally:
        tool_engine.mock_services = orig_mod
        sys.modules.pop(getattr(services_mod, "__name__", ""), None)
    trace = CallTrace(call_id=trace_dict.get("call_id", "replay"),
                      persona=trace_dict.get("persona", ""), events=events)
    return failure.evaluate(spec, trace)


async def _verify(spec: AgentSpec, trace_dict: dict, persona: Optional[Persona], target_id: str,
                  before_src: str, after_src: str, suffix: str) -> dict:
    """The gate. Accept the patch iff it flips the target failure red→green AND introduces no new
    gating failure (monotonic — the code analog of safe_apply's no-regression guarantee)."""
    if after_src.strip() == before_src.strip():
        return {"accepted": False, "reason": "no-op patch (the agent changed nothing)", "before": [], "after": []}
    try:  # the agent's output must at least be valid Python
        compile(after_src, "<patch>", "exec")
    except SyntaxError as e:
        return {"accepted": False, "reason": f"patch has a syntax error: {e}", "before": [], "after": []}
    try:
        before_mod = _load_module(before_src, f"{suffix}_before")
        before = await replay(spec, trace_dict, persona, before_mod)
        after_mod = _load_module(after_src, f"{suffix}_after")
        if not callable(getattr(after_mod, "call", None)) or not callable(getattr(after_mod, "isolated_state", None)):
            return {"accepted": False, "reason": "patch broke the module's public interface", "before": [], "after": []}
        after = await replay(spec, trace_dict, persona, after_mod)
    except Exception as e:  # noqa: BLE001 — a bad patch must be rejected, never crash the heal
        return {"accepted": False, "reason": f"patch failed to load/replay: {type(e).__name__}: {e}", "before": [], "after": []}

    before_ids = {f.id for f in before}
    after_ids = {f.id for f in after}
    if target_id not in before_ids:
        return {"accepted": False, "reason": f"repro did not reproduce {target_id} on replay (inconclusive)",
                "before": sorted(before_ids), "after": sorted(after_ids)}
    target_cleared = target_id not in after_ids
    new_gating = {f.id for f in failure.gating(after)} - {f.id for f in failure.gating(before)}
    if not target_cleared:
        reason = f"{target_id} still present after the patch"
    elif new_gating:
        reason = f"patch regressed: introduced {sorted(new_gating)}"
    else:
        reason = f"verified: {target_id} cleared, no regressions"
    return {"accepted": target_cleared and not new_gating, "reason": reason,
            "before": sorted(before_ids), "after": sorted(after_ids)}


def _task_prompt(spec: AgentSpec, fd: dict, trace_dict: dict) -> str:
    """The brief handed to the coding agent: the failing invariant + the trace evidence."""
    evidence = "\n".join(f"  - {e}" for e in (fd.get("evidence") or []))
    tool_lines = []
    for ev in trace_dict.get("events", []) or []:
        if ev.get("kind") == "tool_call":
            tool_lines.append(f"  call {ev.get('name')}({ev.get('args')})")
        elif ev.get("kind") == "tool_result":
            tool_lines.append(f"  -> {ev.get('name')} ok={ev.get('ok')} {ev.get('result')}")
    flow = "\n".join(tool_lines[:24])
    return (
        f"Business: {spec.business.name} ({spec.business.type}).\n"
        f"STRUCTURAL failure detected by the trace simulator: [{fd.get('id')}] {fd.get('title')}\n"
        f"Why it's a code bug (not a prompt bug): {fd.get('reason')}\n"
        f"Evidence:\n{evidence}\n\n"
        f"The exact tool calls the agent made (this is what will be replayed to verify your fix):\n{flow}\n\n"
        "Fix the root cause in mock_services.py so that replaying these same calls no longer triggers "
        f"'{fd.get('id')}'. Do not change any unrelated behavior. Return the full corrected file."
    )


async def _run_agent(messages: list[dict]) -> Optional[str]:
    """One coding-agent turn: returns the submitted full-file source, or None if it didn't submit."""
    msg = await llm.complete_tools(CODE_SYS, messages, _SUBMIT_TOOL, temperature=0.0,
                                   model=config.CODE_HEAL_MODEL, max_tokens=8000)
    for c in msg["tool_calls"]:
        if c["name"] == "submit_patch":
            src = c["args"].get("source")
            if isinstance(src, str) and src.strip():
                return src
    return None


# patch_fn lets a test inject a deterministic writer (no LLM, no budget): (failure_dict, current_src) -> new_src
PatchFn = Callable[[dict, str], str]


async def heal_code(session_id: str, spec: AgentSpec, report: dict, personas: list[Persona],
                    round_no: int, *, patch_fn: Optional[PatchFn] = None) -> dict:
    """Route every CODE_SPACE failure in the swarm report to the coding agent, verify each fix against
    the replay oracle, and return a report of accepted/rejected diffs. A strict no-op when there are no
    code-space failures. Mirrors heal()'s shape (events, version-style reporting), writer swapped."""
    persona_by_id = {p.id: p for p in personas}
    # Collect (failure, repro-trace, persona) for each distinct code-space failure, dedup by (id, persona).
    targets: list[tuple[dict, dict, Optional[Persona]]] = []
    seen: set[tuple[str, str]] = set()
    for r in report.get("results", []):
        trace = r.get("trace")
        if not trace:
            continue
        for fd in r.get("failures", []) or []:
            if fd.get("fix_space") != "code":
                continue
            key = (fd.get("id", ""), r.get("persona", ""))
            if key in seen:
                continue
            seen.add(key)
            targets.append((fd, trace, persona_by_id.get(r.get("persona", ""))))

    if not targets:
        return {"round": round_no, "checked": 0, "fixes": [], "applied": [], "wrote": False}

    await bus.publish(session_id, {"type": "stage", "stage": "code_heal", "status": "start",
                                   "detail": f"{len(targets)} structural failure(s) → coding agent"})

    can_author = patch_fn is not None or (config.llm_available() and llm.tool_calling_available())
    original_source = TARGET_PATH.read_text()
    working_source = original_source     # accepted fixes accumulate on top of each other
    fixes: list[CodeFix] = []

    for idx, (fd, trace, persona) in enumerate(targets):
        target_id = fd.get("id", "")
        if not can_author:
            fixes.append(CodeFix(target_id, getattr(persona, "id", ""), False,
                                 "no coding-agent backend available (set an LLM key or pass patch_fn)"))
            continue
        messages = [{"role": "user", "content": _task_prompt(spec, fd, trace)}]
        accepted_fix: Optional[CodeFix] = None
        last_reason = "agent did not submit a patch"
        for hop in range(max(1, config.CODE_HEAL_MAX_HOPS)):
            try:
                new_source = (patch_fn(fd, working_source) if patch_fn
                              else await _run_agent(messages))
            except Exception as e:  # noqa: BLE001
                last_reason = f"agent error: {type(e).__name__}: {e}"
                break
            if not new_source:
                break
            v = await _verify(spec, trace, persona, target_id, working_source, new_source, f"{round_no}_{idx}_{hop}")
            last_reason = v["reason"]
            if v["accepted"]:
                diff = "".join(difflib.unified_diff(
                    working_source.splitlines(keepends=True), new_source.splitlines(keepends=True),
                    fromfile="a/app/mock_services.py", tofile="b/app/mock_services.py"))
                working_source = new_source   # stack this fix; later targets verify against it
                accepted_fix = CodeFix(target_id, getattr(persona, "id", ""), True, v["reason"],
                                       v["before"], v["after"], diff)
                break
            # rejected — give the agent the oracle's verdict and let it try again (budget-capped)
            messages.append({"role": "assistant", "content": f"(submitted a patch; verifier said: {v['reason']})"})
            messages.append({"role": "user", "content":
                             f"That patch was rejected: {v['reason']}. Failures still present: {v['after']}. "
                             f"Fix it so '{target_id}' is gone and nothing new breaks. Return the full file again."})
            if patch_fn:   # a deterministic stub won't change across hops — don't spin
                break

        fixes.append(accepted_fix or CodeFix(target_id, getattr(persona, "id", ""), False, last_reason))
        if accepted_fix:
            await bus.publish(session_id, {"type": "code_patch", "round": round_no,
                                           "fix": accepted_fix.to_dict()})

    applied = [f.failure_id for f in fixes if f.accepted]
    wrote = False
    if applied and config.CODE_HEAL_APPLY and working_source != original_source:
        TARGET_PATH.write_text(working_source)   # land the stacked, verified diff into the working tree
        wrote = True

    rej = sum(1 for f in fixes if not f.accepted)
    detail = (f"{len(applied)} verified, {rej} rejected"
              + (" · written to tree" if wrote else " · diff only (CODE_HEAL_APPLY=0)"))
    await bus.publish(session_id, {"type": "stage", "stage": "code_heal", "status": "done", "detail": detail})
    return {"round": round_no, "checked": len(targets), "wrote": wrote,
            "applied": applied, "fixes": [f.to_dict() for f in fixes]}
