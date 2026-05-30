"""Explicit Twilio Media Streams server — the MANUAL alternative.

Primary path is simpler (Pipecat's runner gives you the server + TwiML):
    uv run --python 3.12 bot.py --transport twilio --proxy <ngrok-host>

Use this file only if you need custom FastAPI routes alongside the call websocket:
    uv run --python 3.12 uvicorn twilio_server:app --port 7860
    # then point the Twilio number's Voice webhook at POST {PUBLIC_BASE_URL}/twiml
"""

from __future__ import annotations

import os

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.runner.utils import parse_telephony_websocket
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport

import bot

app = FastAPI()
PUBLIC = os.getenv("PUBLIC_BASE_URL", "")


@app.post("/twiml")
async def twiml(_request: Request) -> HTMLResponse:
    ws = PUBLIC.replace("https://", "wss://").replace("http://", "ws://") + "/ws"
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           f'<Response><Connect><Stream url="{ws}"/></Connect></Response>')
    return HTMLResponse(content=xml, media_type="application/xml")


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    await websocket.accept()
    _type, call = await parse_telephony_websocket(websocket)
    serializer = TwilioFrameSerializer(
        stream_sid=call["stream_id"], call_sid=call["call_id"],
        account_sid=os.getenv("TWILIO_ACCOUNT_SID"), auth_token=os.getenv("TWILIO_AUTH_TOKEN"),
    )
    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True, audio_out_enabled=True, add_wav_header=False,
            vad_analyzer=SileroVADAnalyzer(), serializer=serializer,
        ),
    )
    await bot.run_bot(transport, await bot.load_spec())
