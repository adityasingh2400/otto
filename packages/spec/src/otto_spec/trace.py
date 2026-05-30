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


class AudioFeatures(BaseModel):
    """Paralinguistic signal for a caller turn — the layer plain transcripts/observability are
    blind to. The agent's STT + VAD pipeline (apps/agent/bot.py) populates whatever it can per
    `hear`; it's absent (None) on text-only or replayed traces, so detectors must treat every
    field as optional. This is what lets Otto classify *how* a call sounded — a thick accent, a
    shouting caller, a noisy line, a barge-in — not just what words were exchanged."""
    snr_db: Optional[float] = None        # signal-to-noise ratio; low = noisy line / background
    noise: str = ""                        # "" | quiet | street | cafe | wind | music | crosstalk
    volume_dbfs: Optional[float] = None    # loudness in dBFS (closer to 0 = louder; shouting ≳ -6)
    speech_rate_wpm: Optional[float] = None  # words/minute (very high = rushed, very low = halting)
    lang: str = ""                         # detected language of the turn (e.g. "en", "es")
    lang_switch: bool = False              # caller switched language vs the prior turn
    sentiment: Optional[float] = None      # -1..1 (negative = upset)
    arousal: Optional[float] = None        # 0..1 emotional intensity (shouting/panic = high)
    barge_in: bool = False                 # caller spoke over the agent (talk-over / interruption)
    repeat_request: bool = False           # caller asked the agent to repeat ("what?", "say again")
    disfluency: Optional[float] = None     # 0..1 density of fillers/hesitations/false-starts in this turn.
    # IMPORTANT: STT must run VERBATIM (Deepgram filler_words=true / smart_format off) so "uhh", "ehm",
    # elongations, and false starts survive into `text` instead of being scrubbed into a clean
    # sentence — that disfluency is signal (a struggling/hesitant caller), and we keep it on purpose.


class CallEvent(BaseModel):
    kind: EventKind
    t_ms: int = 0  # ms offset from the start of the call (for latency / dead-air math)
    # hear | say
    text: str = ""
    asr_conf: float = 1.0  # hear only: ASR confidence 0..1 (a low-confidence write is a failure)
    audio: Optional[AudioFeatures] = None  # hear only: paralinguistic signal (accent/noise/emotion/…)
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

    def audio_hears(self) -> list[CallEvent]:
        """Caller turns that carry paralinguistic signal (the voice-anomaly detectors' input)."""
        return [e for e in self.hears() if e.audio is not None]

    def perceived_latency_ms(self) -> int:
        """Total time the CALLER spent waiting on the agent across the call — the *felt* latency.

        For each caller turn, the gap until the agent's next audible turn (`say`). A slow tool
        with no 'one moment' bridge inflates that gap; a quick acknowledgement shrinks it. This
        is the experience metric that 'feels slow' even when every individual step is within SLA —
        exactly what a per-event latency check misses."""
        total = 0
        for i, ev in enumerate(self.events):
            if ev.kind != "hear" or not ev.t_ms:
                continue
            nxt = next((e for e in self.events[i + 1:] if e.kind == "say" and e.t_ms), None)
            if nxt:
                total += max(0, nxt.t_ms - ev.t_ms)
        return total

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
