#!/usr/bin/env bash
# Boots the orchestrator, which also serves the mission-control dashboard at /.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || cp .env.example .env
echo "→ http://localhost:8000   (dashboard + API)"
cd apps/orchestrator
exec uv run --python 3.12 uvicorn app.main:app --reload --port 8000
