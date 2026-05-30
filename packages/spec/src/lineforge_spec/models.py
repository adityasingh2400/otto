"""AgentSpec Pydantic models — source of truth for the LineForge contract.

Regenerate JSON Schema + check drift with:  uv run python export_schema.py
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

PolicyCategory = Literal["safety", "booking", "knowledge", "voice_behavior"]
Severity = Literal["low", "medium", "high", "critical"]
PolicySource = Literal["extracted", "healed", "seed"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Business(BaseModel):
    name: str
    type: str = "restaurant"
    location: str = ""
    timezone: str = "America/Los_Angeles"
    public_url: str = ""
    # Legal posture for the demo — surfaced in the greeting too.
    disclaimer: str = (
        "This is a sandbox AI assistant built from public website information for a "
        "demo. It is not the official phone line; reservations and orders are mocked."
    )


class Voice(BaseModel):
    persona: str = "warm, polished, local"
    greeting: str = "Hi, thanks for calling. How can I help?"
    tts_voice: str = ""  # provider-specific voice id, set by the agent runtime


class KnowledgeItem(BaseModel):
    topic: str
    content: str
    source_url: str = ""
    confidence: float = 0.7  # extractor's confidence; low-confidence => "don't guess"


class ToolParam(BaseModel):
    name: str
    type: str = "string"
    description: str = ""
    required: bool = True


class ToolSpec(BaseModel):
    name: str
    description: str
    params: list[ToolParam] = Field(default_factory=list)
    # Deterministic mock used in the demo (and as a Cekura mock-tool mapping).
    mock_behavior: dict[str, Any] = Field(default_factory=dict)


class Policy(BaseModel):
    """A patchable business rule. The self-heal loop edits ONLY these."""

    id: str
    category: PolicyCategory
    trigger: str  # when this rule applies (natural language)
    rule: str  # what the agent must do
    severity: Severity = "medium"
    source: PolicySource = "extracted"


class EscalationRule(BaseModel):
    id: str
    condition: str
    action: str = "offer to connect to a staff member or take a message"


class Meta(BaseModel):
    version: int = 1
    parent_version: Optional[int] = None
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
    notes: str = ""


class AgentSpec(BaseModel):
    business: Business
    voice: Voice = Field(default_factory=Voice)
    knowledge: list[KnowledgeItem] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    tools: list[ToolSpec] = Field(default_factory=list)
    policies: list[Policy] = Field(default_factory=list)
    escalation_rules: list[EscalationRule] = Field(default_factory=list)
    safety_rules: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    meta: Meta = Field(default_factory=Meta)

    # ── policy helpers (used by the healer) ──────────────────────────────────
    def get_policy(self, policy_id: str) -> Optional[Policy]:
        return next((p for p in self.policies if p.id == policy_id), None)

    def upsert_policy(self, policy: Policy) -> Literal["added", "modified"]:
        for i, p in enumerate(self.policies):
            if p.id == policy.id:
                self.policies[i] = policy
                return "modified"
        self.policies.append(policy)
        return "added"

    def bump_version(self, notes: str = "") -> None:
        parent = self.meta.version
        self.meta = Meta(
            version=parent + 1,
            parent_version=parent,
            created_at=self.meta.created_at,
            updated_at=_now(),
            notes=notes,
        )

    # ── prompt compilation (the live system prompt) ──────────────────────────
    def compile_prompt(self) -> str:
        b = self.business
        lines: list[str] = []
        lines.append(
            f"You are the AI phone assistant for {b.name}, a {b.type}"
            + (f" in {b.location}." if b.location else ".")
        )
        lines.append(b.disclaimer)
        lines.append("")
        lines.append("# Manner")
        lines.append(
            f"{self.voice.persona}. This is a phone call: keep replies short and "
            "natural, ask one question at a time, and confirm details back."
        )

        if self.capabilities:
            lines.append("\n# You can help with")
            lines += [f"- {c}" for c in self.capabilities]

        if self.tools:
            lines.append("\n# Tools — call these; never invent their results")
            for t in self.tools:
                ps = ", ".join(p.name for p in t.params)
                lines.append(f"- {t.name}({ps}): {t.description}")

        if self.knowledge:
            lines.append("\n# Knowledge")
            for k in self.knowledge:
                hedge = "  (uncertain — do not state as fact)" if k.confidence < 0.5 else ""
                lines.append(f"- {k.topic}: {k.content}{hedge}")
        lines.append(
            "If a caller asks something not covered above, say you don't want to "
            "guess and offer to connect them or take a message. Never invent menu "
            "items, prices, hours, or availability."
        )

        if self.policies:
            lines.append("\n# Policies — follow exactly")
            for cat in ("safety", "booking", "knowledge", "voice_behavior"):
                group = [p for p in self.policies if p.category == cat]
                if not group:
                    continue
                lines.append(f"## {cat.replace('_', ' ').title()}")
                for p in group:
                    lines.append(f"- When {p.trigger}: {p.rule}")

        if self.escalation_rules:
            lines.append("\n# Escalate (connect to staff / take a message)")
            lines += [f"- If {e.condition}: {e.action}" for e in self.escalation_rules]

        if self.safety_rules:
            lines.append("\n# Safety")
            lines += [f"- {s}" for s in self.safety_rules]

        if self.out_of_scope:
            lines.append("\n# Out of scope — politely decline")
            lines += [f"- {o}" for o in self.out_of_scope]

        lines.append(f'\n# Greeting (say this first)\n"{self.voice.greeting}"')
        return "\n".join(lines)


# ── spec diffing (powers the auto-patch screen) ──────────────────────────────
class PolicyDiff(BaseModel):
    change: Literal["added", "modified"]
    policy_id: str
    category: PolicyCategory
    before: Optional[Policy] = None
    after: Policy


def diff_specs(old: AgentSpec, new: AgentSpec) -> list[PolicyDiff]:
    """Policy-level diff between two spec versions, for the before/after UI."""
    diffs: list[PolicyDiff] = []
    old_by_id = {p.id: p for p in old.policies}
    for p in new.policies:
        prev = old_by_id.get(p.id)
        if prev is None:
            diffs.append(PolicyDiff(change="added", policy_id=p.id, category=p.category, after=p))
        elif prev != p:
            diffs.append(
                PolicyDiff(change="modified", policy_id=p.id, category=p.category, before=prev, after=p)
            )
    return diffs
