#!/usr/bin/env bash
# One-command bootstrap. Safe to re-run. No API keys required for this step.
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f .env ] || { cp .env.example .env; echo "▸ created .env from template"; }

echo "▸ spec package + JSON schema"
(cd packages/spec && uv run --python 3.12 python export_schema.py)

echo "▸ orchestrator deps"
(cd apps/orchestrator && uv sync --python 3.12 >/dev/null)

echo "▸ tests"
(cd apps/orchestrator && uv run --python 3.12 --with pytest python -m pytest tests -q)

echo "▸ agent deps (pipecat — needs network, ~1-2 min)"
(cd apps/agent && uv sync --python 3.12 >/dev/null 2>&1 && echo "  agent deps installed" \
  || echo "  agent deps skipped — run later: cd apps/agent && uv sync --python 3.12")

echo
echo "✓ ready."
echo "  zero-key demo:  ./scripts/dev.sh   → open http://localhost:8000"
echo "  go live:        see docs/RUNBOOK.md"
