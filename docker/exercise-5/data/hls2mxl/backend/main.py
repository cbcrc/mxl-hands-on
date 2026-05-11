"""
FastAPI backend for the HLS-to-MXL gateway.

Endpoints
---------
GET  /status      – current state (playing/idle/error, current URL, flow IDs)
POST /hls-link    – set the HLS URL          {"url": "https://...m3u8"}
POST /apply       – connect to the set URL and start streaming to MXL
POST /stop        – stop the current stream
"""

from __future__ import annotations

import logging
import threading

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.gst_hls import GstHls
from backend.nmos_bridge import NmosBridge

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S,%f",
)
log = logging.getLogger(__name__)

app = FastAPI(title="HLS to MXL Gateway")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

player = GstHls()
bridge = NmosBridge(
    nmos_base="http://localhost:9520",
    on_activate=lambda: _safe(player.apply),
    on_deactivate=lambda: _safe(player.stop),
)


def _safe(fn):
    try:
        fn()
    except Exception as exc:
        log.error("Error in callback: %s", exc)


# ── Startup ───────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup() -> None:
    def _init():
        flow_ids = bridge.discover_flow_ids()
        vid = flow_ids.get("video")
        aud = flow_ids.get("audio")
        if vid and aud:
            player.set_flow_ids(vid, aud)
        else:
            log.error("Could not discover video/audio flow IDs")
        bridge.run()  # blocks – poll loop

    threading.Thread(target=_init, name="nmos-bridge", daemon=True).start()


# ── Models ────────────────────────────────────────────────────────────────────

class HlsLinkReq(BaseModel):
    url: str


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/status")
async def status():
    return player.get_status()

@app.post("/hls-link")
async def hls_link(req: HlsLinkReq):
    if not req.url.strip():
        raise HTTPException(status_code=400, detail="URL cannot be empty")
    player.set_url(req.url.strip())
    return {"status": "ok", "url": req.url.strip()}

@app.post("/apply")
async def apply():
    try:
        player.apply()
        bridge.set_senders_active(True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "ok"}

@app.post("/stop")
async def stop():
    player.stop()
    bridge.set_senders_active(False)
    return {"status": "ok"}
