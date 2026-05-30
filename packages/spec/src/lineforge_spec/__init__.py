"""LineForge AgentSpec — the single contract shared across the orchestrator,
the Pipecat agent, and (via JSON Schema / TS types) the dashboard.

The self-heal loop only ever edits `AgentSpec.policies`. The live system prompt is
compiled from the spec (`spec.compile_prompt()`), so every patch is a legible
before/after diff and the loop stays trustworthy.
"""

from .models import (
    AgentSpec,
    Business,
    EscalationRule,
    KnowledgeItem,
    Meta,
    Policy,
    ToolParam,
    ToolSpec,
    Voice,
    diff_specs,
    PolicyDiff,
)

__all__ = [
    "AgentSpec",
    "Business",
    "Voice",
    "KnowledgeItem",
    "ToolParam",
    "ToolSpec",
    "Policy",
    "EscalationRule",
    "Meta",
    "diff_specs",
    "PolicyDiff",
]
