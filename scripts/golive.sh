#!/usr/bin/env bash
# One command to take the line LIVE for a real phone call — ZERO manual pointing.
#
# Brings up the full stack and wires it together automatically:
#   1. ngrok          → a public HTTPS URL for the agent's Twilio media-stream server
#   2. orchestrator   → :8000, started WITH PUBLIC_BASE_URL so the dashboard's Deploy button
#                       auto-points the Twilio number's Voice webhook at the live agent
#   3. voice agent    → Twilio transport; auto-resolves the DEPLOYED business (no OTTO_SESSION),
#                       so its word-by-word transcript streams into the dashboard you're watching
#
# Then: open http://localhost:8000/app/, build a business, press Deploy, and call the number.
# The accent/uhh-ehm/shouting detection fires live, exactly like the simulator.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
AGENT_PORT="${AGENT_PORT:-7860}"   # pipecat runner's default; the Twilio media-stream websocket

command -v ngrok >/dev/null || { echo "Install ngrok first → https://ngrok.com/download"; exit 1; }

echo "▸ starting ngrok on :$AGENT_PORT …"
pkill -f "ngrok http" 2>/dev/null || true
nohup ngrok http "$AGENT_PORT" >/tmp/otto_ngrok.log 2>&1 &
PUBLIC=""
for _ in $(seq 1 25); do
  PUBLIC=$(curl -s http://127.0.0.1:4040/api/tunnels 2>/dev/null \
    | python3 -c 'import sys,json;ts=json.load(sys.stdin).get("tunnels",[]);print(next((t["public_url"] for t in ts if t["public_url"].startswith("https")),""))' 2>/dev/null || true)
  [ -n "$PUBLIC" ] && break; sleep 1
done
[ -n "$PUBLIC" ] || { echo "✗ couldn't get an ngrok URL (see /tmp/otto_ngrok.log)"; exit 1; }
export PUBLIC_BASE_URL="$PUBLIC"
echo "▸ public URL: $PUBLIC  (Deploy will point the Twilio webhook → $PUBLIC/twiml)"

echo "▸ (re)starting orchestrator on :8000 with PUBLIC_BASE_URL set …"
pkill -f "uvicorn app.main:app" 2>/dev/null || true
( cd apps/orchestrator && PUBLIC_BASE_URL="$PUBLIC" nohup uv run --python 3.12 python -m uvicorn app.main:app --port 8000 >/tmp/otto_orch.log 2>&1 & )
until curl -s http://localhost:8000/api/health >/dev/null 2>&1; do sleep 1; done
echo "▸ orchestrator up. Open http://localhost:8000/app/ → build a business → press Deploy."

echo "▸ starting the live voice agent (Twilio). It serves whatever you Deploy — no OTTO_SESSION needed."
cd apps/agent
exec env PUBLIC_BASE_URL="$PUBLIC" ORCH_BASE_URL="http://localhost:8000" \
  uv run --python 3.12 python bot.py --transport twilio --proxy "${PUBLIC#https://}"
