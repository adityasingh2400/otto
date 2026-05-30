"""Twilio Media Streams → Pipecat (the live phone call).

Point the Twilio number's Voice webhook at  POST {PUBLIC_BASE_URL}/twiml.
Run:  uv run --python 3.12 uvicorn twilio_server:app --port 7860
      ngrok http 7860   # set PUBLIC_BASE_URL to the https URL

D2 TODO(team): confirm transport/serializer import paths against the pinned Pipecat +
the pipecat-quickstart-phone-bot repo, and parse the Twilio `start` event for
streamSid/callSid before constructing the serializer.
"""

from __future__ import annotations

import os
import pathlib

import httpx
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse
from lineforge_spec import AgentSpec

import bot

app = FastAPI()
ORCH = os.getenv("ORCH_BASE_URL", "http://localhost:8000")
PUBLIC = os.getenv("PUBLIC_BASE_URL", "")
SESSION = os.getenv("LINEFORGE_SESSION", "")  # which built session's spec to serve


@app.post("/twiml")
async def twiml(_request: Request) -> HTMLResponse:
    ws_url = PUBLIC.replace("https://", "wss://").replace("http://", "ws://") + "/ws"
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<Response><Connect><Stream url="{ws_url}"/></Connect></Response>'
    )
    return HTMLResponse(content=xml, media_type="application/xml")


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    await websocket.accept()
    spec = await _load_spec()
    # TODO(team): read the Twilio start frames, then:
    from pipecat.serializers.twilio import TwilioFrameSerializer
    from pipecat.transports.network.fastapi_websocket import (
        FastAPIWebsocketParams,
        FastAPIWebsocketTransport,
    )

    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(serializer=TwilioFrameSerializer(stream_sid="TODO")),
    )
    await bot.run_bot(transport, spec)


async def _load_spec() -> AgentSpec:
    if SESSION:
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                r = await c.get(f"{ORCH}/api/spec/{SESSION}")
                if r.status_code == 200:
                    return AgentSpec.model_validate(r.json())
        except Exception:
            pass
    cached = pathlib.Path(__file__).resolve().parents[2] / "packages" / "spec" / "piccino.json"
    return AgentSpec.model_validate_json(cached.read_text())
