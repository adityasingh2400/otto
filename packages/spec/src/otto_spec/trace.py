"""CallTrace — the structured event stream of a single call.

This is the second half of the contract (AgentSpec is the first). The agent emits one of
these per call; the orchestrator's failure engine (app/failure.py) classifies it. Unlike a
plain transcript, a CallTrace records *what the agent did*, not just what it said: every
tool call with its args, result, outcome, and latency. That's what lets Otto catch the
failures a voice-only eval misses — the right words wrapped around a wrong, slow, failed,
or never-taken action.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

# hear = caller turn · say = agent turn · tool_call = agent invoked a tool ·
# tool_result = the tool returned (ok + result + latency)
EventKind = Literal["hear", "say", "tool_call", "tool_result"]


class CallEvent(BaseModel):
    kind: EventKind
    t_ms: int = 0  # ms offset from the start of the call (for latency / dead-air math)
    # hear | say
    text: str = ""
    asr_conf: float = 1.0  # hear only: ASR confidence 0..1 (a low-confidence write is a failure)
    # tool_call | tool_result
    name: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    ok: Optional[bool] = None  # tool_result only: did the tool succeed
    result: dict[str, Any] = Field(default_factory=dict)  # tool_result payload
    latency_ms: int = 0  # tool_result: wall time the caller waited on this tool
    error: str = ""


class CallTrace(BaseModel):
    call_id: str = ""
    persona: str = ""  # the scenario this call corresponds to, if known (for routing)
    events: list[CallEvent] = Field(default_factory=list)

    # ── convenience views the detectors use ─────────────────────────────────
    def of_kind(self, kind: EventKind) -> list[CallEvent]:
        return [e for e in self.events if e.kind == kind]

    def says(self) -> list[CallEvent]:
        return self.of_kind("say")

    def hears(self) -> list[CallEvent]:
        return self.of_kind("hear")

    def tool_calls(self) -> list[CallEvent]:
        return self.of_kind("tool_call")

    def tool_results(self) -> list[CallEvent]:
        return self.of_kind("tool_result")

    def called(self, name: str) -> bool:
        return any(e.name == name for e in self.tool_calls())

    @property
    def transcript(self) -> str:
        """Caller/agent dialogue only — for the LLM-judge conversation detector."""
        out = []
        for e in self.events:
            if e.kind == "say":
                out.append(f"Agent: {e.text}")
            elif e.kind == "hear":
                out.append(f"Caller: {e.text}")
        return "\n".join(out)
