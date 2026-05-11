"""
FastAPI backend for the MXL Test Generator.

Endpoints
---------
GET  /status               – current state (pattern, timecode, ident, flow IDs)
GET  /patterns             – lists available video and audio patterns
POST /video-test-pattern   – change video test pattern  {"pattern": "smpte"}
POST /audio-test-pattern   – change audio test pattern  {"pattern": "sine"}
POST /timecode             – toggle timecode overlay     {"enabled": true}
POST /ident                – set ident text              {"text": "Camera 1"}
"""

from __future__ import annotations

import logging
import threading

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.gst_generator import GstGenerator
from backend.nmos_bridge import NmosBridge

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S,%f",
)
log = logging.getLogger(__name__)

app = FastAPI(title="MXL Test Generator")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

generator = GstGenerator()
bridge    = NmosBridge(
    nmos_base="http://localhost:9510",
    on_activate=None,
    on_deactivate=None,
)

# ── Startup ───────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup() -> None:
    def _init():
        flow_ids = bridge.discover_flow_ids()
        vid = flow_ids.get("video")
        aud = flow_ids.get("audio")
        if vid and aud:
            generator.set_flow_ids(vid, aud)
        else:
            log.error("Could not discover video/audio flow IDs – generator won't start")

        bridge.set_senders_active(True)
        bridge.run()          # blocks – runs the poll loop

    threading.Thread(target=_init, name="nmos-bridge", daemon=True).start()

# ── Models ────────────────────────────────────────────────────────────────────

class VideoPatternReq(BaseModel):
    pattern: str

class AudioPatternReq(BaseModel):
    pattern: str

class TimecodeReq(BaseModel):
    enabled: bool

class IdentReq(BaseModel):
    text: str

class ChannelSelectReq(BaseModel):
    channels: int   # 2 or 6

class AudioLevelReq(BaseModel):
    db: float       # dBFS, -60 to 0, steps of 0.5

# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/status")
async def status():
    return generator.get_status()

@app.get("/patterns")
async def patterns():
    return {
        "video": generator.get_video_patterns(),
        "audio": generator.get_audio_patterns(),
    }

@app.post("/video-test-pattern")
async def video_test_pattern(req: VideoPatternReq):
    try:
        generator.set_video_pattern(req.pattern)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "ok", "video_pattern": req.pattern}

@app.post("/audio-test-pattern")
async def audio_test_pattern(req: AudioPatternReq):
    try:
        generator.set_audio_pattern(req.pattern)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "ok", "audio_pattern": req.pattern}

@app.post("/timecode")
async def timecode(req: TimecodeReq):
    generator.set_timecode(req.enabled)
    return {"status": "ok", "timecode": req.enabled}

@app.post("/ident")
async def ident(req: IdentReq):
    generator.set_ident(req.text)
    return {"status": "ok", "ident": req.text}

@app.post("/channel-select")
async def channel_select(req: ChannelSelectReq):
    try:
        generator.set_channel_count(req.channels)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "ok", "channel_count": req.channels}

@app.post("/audio-level-set")
async def audio_level_set(req: AudioLevelReq):
    generator.set_audio_level(req.db)
    return {"status": "ok", "audio_level_db": generator.get_audio_level()}

@app.get("/audio-level-read")
async def audio_level_read():
    return {"audio_level_db": generator.get_audio_level()}
