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
    "input, a correct status). Make ONE minimal fix with edit_file: copy a SHORT, exact, UNIQUE "
    "snippet from the file as old_string and return the corrected new_string. Preserve every other "
    "behavior and the module's public interface. Keep the edit as small as possible."
)

_EDIT_TOOL = [{
    "type": "function",
    "function": {
        "name": "edit_file",
        "description": ("Apply one exact find-and-replace edit to mock_services.py. old_string must be a "
                        "verbatim, UNIQUE snippet of the current file; new_string replaces it."),
        "parameters": {
            "type": "object",
            "properties": {
                "old_string": {"type": "string", "description": "exact snippet to find (unique in the file)"},
                "new_string": {"type": "string", "description": "the replacement text"},
            },
            "required": ["old_string", "new_string"],
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


def _seed_from_trace(trace_dict: dict) -> Optional[Callable[[dict], None]]:
    """Reconstruct backend PRECONDITIONS from a live trace's own recorded results, so a state-dependent
    failure reproduces on replay against a fresh backend (a live trace has no persona.setup to seed from).

    We seed only SCARCITY signals — a slot that was full, an item out of stock, a taken appointment —
    never success EFFECTS, which the replayed calls recreate themselves. And we suppress force_unavailable
    when the call actually DID reserve a table: if a booking succeeded, 'the place was full' wasn't the
    binding precondition. This keeps self-contained failures (duplicate_side_effect, whose precondition is
    its own first call) pristine — _seed_from_trace returns None for them."""
    events = trace_dict.get("events", []) or []
    force_unavailable = had_confirmed_reserve = False
    deplete: set[str] = set()
    appt_times: set[str] = set()
    last_call: dict = {}
    for ev in events:
        if ev.get("kind") == "tool_call":
            last_call = ev
            continue
        if ev.get("kind") != "tool_result":
            continue
        name, res, args = ev.get("name"), (ev.get("result") or {}), (last_call.get("args") or {})
        status = str(res.get("status", "")).lower()
        if name in ("check_availability", "reserve_table"):
            if res.get("available") is False or res.get("tables_left") == 0 or status == "unavailable":
                force_unavailable = True
            if res.get("reservation_id") or status == "confirmed":
                had_confirmed_reserve = True
        elif name in ("get_inventory", "order_item"):
            if status == "out_of_stock" or res.get("in_stock") is False or res.get("qty") == 0:
                item = str(args.get("item") or res.get("item") or "").lower().strip()
                if item:
                    deplete.add(item)
        elif name == "book_appointment" and status == "unavailable":
            t = str(args.get("time") or "")
            if t:
                appt_times.add(t)
        last_call = {}
    force_unavailable = force_unavailable and not had_confirmed_reserve
    if not (force_unavailable or deplete or appt_times):
        return None

    def seed(st: dict) -> None:
        if force_unavailable:
            st["force_unavailable"] = True
        for item in deplete:
            st["inventory"][item] = 0
        for t in appt_times:
            st["appointments"].append({"id": f"SEED-{t}", "name": "seed", "time": t, "detail": "precondition"})
    return seed


async def replay(spec: AgentSpec, trace_dict: dict, persona: Optional[Persona], services_mod) -> list:
    """Re-execute the recorded tool_call events against `services_mod` (a fresh mock_services), rebuild
    the trace, and return the failures the SAME detectors find. say/hear events carry through unchanged;
    tool_result events are REGENERATED by the patched code — that's exactly what a code fix changes.
    Deterministic and LLM-free."""
    src_events = trace_dict.get("events", []) or []
    orig_results = [e for e in src_events if e.get("kind") == "tool_result"]
    # Pre-launch personas carry an authoritative setup seed; a live trace (persona=None, or no setup)
    # has none, so derive the precondition from the trace's own recorded results.
    seed = (persona.setup if persona and getattr(persona, "setup", None) else None) or _seed_from_trace(trace_dict)
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
        name = getattr(services_mod, "__name__", "")
        if name.startswith("app._codeheal_mock_"):  # only evict throwaway patched modules, never the real one
            sys.modules.pop(name, None)
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


async def _run_agent(messages: list[dict], current_source: str, model: str) -> tuple[Optional[str], str]:
    """One coding-agent turn. Returns (new_source, note): the patched source if the agent emitted a
    cleanly-applicable edit, else (None, why) so the caller can feed the reason back and retry."""
    msg = await llm.complete_tools(CODE_SYS, messages, _EDIT_TOOL, temperature=0.0,
                                   model=model, max_tokens=4000, force_tool=True)
    edit = next((c for c in msg["tool_calls"] if c["name"] == "edit_file"), None)
    if not edit:
        return None, "no edit_file call"
    old, new = edit["args"].get("old_string"), edit["args"].get("new_string")
    if not isinstance(old, str) or not isinstance(new, str) or not old:
        return None, "edit_file missing old_string/new_string"
    n = current_source.count(old)
    if n == 0:
        return None, "old_string not found verbatim in the file"
    if n > 1:
        return None, "old_string matched multiple places — make it unique"
    return current_source.replace(old, new, 1), "ok"


# patch_fn lets a test inject a deterministic writer (no LLM, no budget): (failure_dict, current_src) -> new_src
PatchFn = Callable[[dict, str], str]

# A target = (failure_dict, repro-trace-dict, persona-or-None). Both entry points build this and
# hand it to the shared loop, so the pre-launch swarm path and the production/Cekura trace path
# run the identical author→verify→escalate machinery.
Target = "tuple[dict, dict, Optional[Persona]]"


def _as_dict(f) -> dict:
    return f.to_dict() if hasattr(f, "to_dict") else dict(f)


def _models() -> list[str]:
    """The model ladder: cheap model for the budgeted hops, then one shot on the escalation model."""
    ladder = [config.CODE_HEAL_MODEL] * max(1, config.CODE_HEAL_MAX_HOPS)
    if config.CODE_HEAL_ESCALATE_MODEL.strip():
        ladder.append(config.CODE_HEAL_ESCALATE_MODEL.strip())
    return ladder


async def _heal_one(session_id: str, spec: AgentSpec, fd: dict, trace: dict, persona: Optional[Persona],
                    working_source: str, tag: str, patch_fn: Optional[PatchFn]) -> tuple[CodeFix, str]:
    """Author + verify ONE code-space failure, escalating up the model ladder until a patch passes the
    replay oracle (or attempts run out). Returns (CodeFix, new_working_source)."""
    target_id = fd.get("id", "")
    persona_id = getattr(persona, "id", "") or ""
    messages = [{"role": "user", "content": _task_prompt(spec, fd, trace)
                 + "\n\n--- CURRENT mock_services.py (copy old_string verbatim from here) ---\n"
                 + working_source}]
    last_reason = "agent did not submit a patch"
    for attempt, model in enumerate(_models()):
        try:
            if patch_fn:
                new_source, note = patch_fn(fd, working_source), "ok"
            else:
                new_source, note = await _run_agent(messages, working_source, model)
        except Exception as e:  # noqa: BLE001
            last_reason = f"agent error ({model}): {type(e).__name__}: {e}"
            break
        if not new_source:   # no applicable edit — feed the reason back and let it retry
            last_reason = note
            if patch_fn:
                break
            messages.append({"role": "assistant", "content": f"(edit could not be applied: {note})"})
            messages.append({"role": "user", "content":
                             f"That edit failed ({note}). Quote a SHORT, exact, UNIQUE snippet from the "
                             "file as old_string and try edit_file again."})
            continue
        v = await _verify(spec, trace, persona, target_id, working_source, new_source, f"{tag}_{attempt}")
        last_reason = v["reason"]
        if v["accepted"]:
            diff = "".join(difflib.unified_diff(
                working_source.splitlines(keepends=True), new_source.splitlines(keepends=True),
                fromfile="a/app/mock_services.py", tofile="b/app/mock_services.py"))
            return CodeFix(target_id, persona_id, True, v["reason"], v["before"], v["after"], diff), new_source
        # rejected by the replay oracle — hand the verdict back; the next attempt may be a stronger model
        messages.append({"role": "assistant", "content": f"(submitted a patch; verifier said: {v['reason']})"})
        messages.append({"role": "user", "content":
                         f"That patch was rejected: {v['reason']}. Failures still present: {v['after']}. "
                         f"Make '{target_id}' go away without breaking anything, then call edit_file again."})
        if patch_fn:   # a deterministic stub won't change across attempts — don't spin
            break
    return CodeFix(target_id, persona_id, False, last_reason), working_source


async def _run_targets(session_id: str, spec: AgentSpec, targets: list, round_no: int,
                       patch_fn: Optional[PatchFn]) -> tuple[list[CodeFix], str, str]:
    """Run every target through the author→verify→escalate loop, stacking accepted diffs onto one
    working copy. Returns (fixes, working_source, original_source). No file is written here."""
    can_author = patch_fn is not None or (config.llm_available() and llm.tool_calling_available())
    original_source = TARGET_PATH.read_text()
    working_source = original_source
    fixes: list[CodeFix] = []
    for idx, (fd, trace, persona) in enumerate(targets):
        if not can_author:
            fixes.append(CodeFix(fd.get("id", ""), getattr(persona, "id", "") or "", False,
                                 "no coding-agent backend available (set an LLM key or pass patch_fn)"))
            continue
        fix, working_source = await _heal_one(session_id, spec, fd, trace, persona,
                                              working_source, f"{round_no}_{idx}", patch_fn)
        fixes.append(fix)
        if fix.accepted:
            await bus.publish(session_id, {"type": "code_patch", "round": round_no, "fix": fix.to_dict()})
    return fixes, working_source, original_source


def _targets_from_report(report: dict, personas: list[Persona]) -> list:
    persona_by_id = {p.id: p for p in personas}
    targets, seen = [], set()
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
    return targets


async def heal_code(session_id: str, spec: AgentSpec, report: dict, personas: list[Persona],
                    round_no: int, *, patch_fn: Optional[PatchFn] = None) -> dict:
    """PRE-LAUNCH path: route every CODE_SPACE failure in a swarm report to the coding agent, verify each
    against the replay oracle, return accepted/rejected diffs. Writes to the tree only if CODE_HEAL_APPLY.
    A strict no-op when there are no code-space failures."""
    targets = _targets_from_report(report, personas)
    if not targets:
        return {"round": round_no, "checked": 0, "fixes": [], "applied": [], "wrote": False}
    await bus.publish(session_id, {"type": "stage", "stage": "code_heal", "status": "start",
                                   "detail": f"{len(targets)} structural failure(s) → coding agent"})
    fixes, working_source, original_source = await _run_targets(session_id, spec, targets, round_no, patch_fn)
    applied = [f.failure_id for f in fixes if f.accepted]
    wrote = False
    if applied and config.CODE_HEAL_APPLY and working_source != original_source:
        TARGET_PATH.write_text(working_source)
        wrote = True
    rej = sum(1 for f in fixes if not f.accepted)
    detail = (f"{len(applied)} verified, {rej} rejected"
              + (" · written to tree" if wrote else " · diff only (CODE_HEAL_APPLY=0)"))
    await bus.publish(session_id, {"type": "stage", "stage": "code_heal", "status": "done", "detail": detail})
    return {"round": round_no, "checked": len(targets), "wrote": wrote,
            "applied": applied, "fixes": [f.to_dict() for f in fixes]}


async def heal_code_for_trace(session_id: str, spec: AgentSpec, trace, failures: list,
                              persona: Optional[Persona] = None, round_no: int = 1, *,
                              patch_fn: Optional[PatchFn] = None) -> dict:
    """PRODUCTION path (what the live/Cekura observe loop calls): given ONE live call's trace + the
    failures detected on it, fix every code-space failure, then — if CODE_HEAL_MERGE — gate the stacked
    fix through the conversational harness (Cekura when keyed) and, only if it passes, MERGE it to the
    live line (write + hot-reload + commit). Rolls back if the harness regresses. No-op if no code-space
    failure. `failures` may be FailureInstance objects or dicts."""
    trace_dict = trace.model_dump() if hasattr(trace, "model_dump") else dict(trace)
    code_failures = [d for d in (_as_dict(f) for f in failures) if d.get("fix_space") == "code"]
    if not code_failures:
        return {"round": round_no, "checked": 0, "fixes": [], "applied": [], "merged": False}
    targets = [(fd, trace_dict, persona) for fd in code_failures]
    await bus.publish(session_id, {"type": "stage", "stage": "code_heal", "status": "start",
                                   "detail": f"{len(targets)} structural failure(s) on live call → coding agent"})
    fixes, working_source, original_source = await _run_targets(session_id, spec, targets, round_no, patch_fn)
    applied = [f.failure_id for f in fixes if f.accepted]

    merged, gate_rate = False, None
    if applied and config.CODE_HEAL_MERGE and working_source != original_source:
        merged, gate_rate = await _gate_and_merge(session_id, spec, working_source, original_source, applied)

    rej = sum(1 for f in fixes if not f.accepted)
    if not applied:
        detail = f"0 verified, {rej} rejected"
    elif merged:
        detail = f"{len(applied)} fix(es) verified + harness-gated ({int(gate_rate*100)}%) → MERGED to live line"
    elif gate_rate is not None:
        detail = f"{len(applied)} verified but harness regressed ({int(gate_rate*100)}%) → rolled back, NOT merged"
    else:
        detail = f"{len(applied)} verified · merge off (CODE_HEAL_MERGE=0), diff only"
    await bus.publish(session_id, {"type": "stage", "stage": "code_heal", "status": "done", "detail": detail})
    return {"round": round_no, "checked": len(targets), "merged": merged, "gate_pass_rate": gate_rate,
            "applied": applied, "fixes": [f.to_dict() for f in fixes]}


def _reload_target() -> None:
    """Hot-reload mock_services so the running line (agent + dashboard, via tool_engine.mock_services)
    picks up the patched tool layer in place — no restart. reload() mutates the existing module object,
    so every `from . import mock_services` reference sees the new functions."""
    import importlib
    from . import mock_services
    importlib.reload(mock_services)


async def _gate_and_merge(session_id: str, spec: AgentSpec, working_source: str, original_source: str,
                          applied: list[str]) -> tuple[bool, float]:
    """The second gate + the merge. Write the verified fix, hot-reload it, then run the conversational
    harness (Cekura when SWARM_MODE=cekura + keyed, else the local sim) across the full pre-launch suite.
    Keep + commit only if it clears PASS_GATE; otherwise roll the file back. Returns (merged, pass_rate)."""
    from . import archetypes
    from .swarm import run_swarm
    await bus.publish(session_id, {"type": "stage", "stage": "code_gate", "status": "start",
                                   "detail": "verified locally — now gating the fix through the conversational harness"})
    TARGET_PATH.write_text(working_source)
    _reload_target()
    try:
        personas = archetypes.select_for(spec.business.type, config.SWARM_PERSONAS)
        gate = await run_swarm(session_id, spec, round_no=spec.meta.version, personas=personas)
        rate = float(gate.get("pass_rate", 0.0))
    except Exception as e:  # noqa: BLE001 — a harness error must not leave a half-merged tree
        TARGET_PATH.write_text(original_source)
        _reload_target()
        await bus.publish(session_id, {"type": "stage", "stage": "code_gate", "status": "done",
                                       "detail": f"harness error ({type(e).__name__}) → rolled back, NOT merged"})
        return False, 0.0
    if rate < config.PASS_GATE:
        TARGET_PATH.write_text(original_source)   # the fix regressed conversations — undo it
        _reload_target()
        await bus.publish(session_id, {"type": "stage", "stage": "code_gate", "status": "done",
                                       "detail": f"harness {int(rate*100)}% < gate {int(config.PASS_GATE*100)}% → rolled back"})
        return False, rate
    if config.CODE_HEAL_COMMIT:
        _commit(applied)
    await bus.publish(session_id, {"type": "stage", "stage": "code_gate", "status": "done",
                                   "detail": f"harness {int(rate*100)}% ≥ gate → fix is live"})
    return True, rate


def _commit(applied: list[str]) -> None:
    """Commit the merged fix to the current branch (durable record of the live-line change)."""
    import subprocess
    msg = f"fix(auto): code-heal merged {', '.join(applied)} after harness gate\n\nAuthored by the code-heal coding agent, verified by the replay oracle + conversational harness."
    try:
        subprocess.run(["git", "add", str(TARGET_PATH)], cwd=str(config.ROOT), check=True,
                       capture_output=True, timeout=20)
        subprocess.run(["git", "commit", "-m", msg], cwd=str(config.ROOT), check=True,
                       capture_output=True, timeout=20)
    except Exception:  # noqa: BLE001 — a commit failure must not unwind a verified, live fix
        pass
