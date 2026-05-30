# @otto/spec

The `AgentSpec` — the single contract every Otto component reads and writes.

- **Source of truth:** `src/otto_spec/models.py` (Pydantic). Both Python apps
  (`apps/orchestrator`, `apps/agent`) import it via an editable path dependency.
- **Generated:** `agent-spec.schema.json` (`uv run python export_schema.py`).
- **Mirror:** `types.ts` for the Next.js dashboard.
- **Demo fallback:** `piccino.json` — a naive v1 extraction, intentionally weak on
  the three hero failure modes (allergy guarantee, large-party routing, guessed
  availability) so the swarm catches real failures and the healer makes real patches.

The self-heal loop edits **only** `policies[]`. The live system prompt is compiled
from the spec (`spec.compile_prompt()`), so each patch is a clean before/after diff.
