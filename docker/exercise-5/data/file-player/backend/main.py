"""
FastAPI backend for the MXL File Player.

Ports:
  9600 – this API
  9500 – nmos-cpp-node (separate process, started by entrypoint.sh)
  9700 – React static frontend (python -m http.server, started by entrypoint.sh)
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .gst_player import GstPlayer
from .nmos_bridge import NmosBridge

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MEDIA_DIR = Path(os.getenv("MEDIA_DIR", "/home/file"))
NMOS_BASE = os.getenv("NMOS_NODE_URL", "http://localhost:9500")
SUPPORTED_EXTENSIONS = {".mp4", ".ts", ".mov", ".mkv", ".mxf", ".avi"}

# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------
app = FastAPI(title="MXL File Player", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

player = GstPlayer()
bridge = NmosBridge(
    nmos_base=NMOS_BASE,
    on_activate=player.play,
    on_deactivate=player.stop,
)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
@app.on_event("startup")
async def startup() -> None:
    # Discover MXL flow IDs from the NMOS node's IS-05 constraints.
    # Runs in a thread to avoid blocking the event loop.
    def _init_flows():
        flow_map = bridge.discover_flow_ids()
        video_id = flow_map.get("video")
        audio_id = flow_map.get("audio")
        if video_id and audio_id:
            player.set_flow_ids(video_id, audio_id)
        else:
            log.warning("Could not discover all flow IDs from NMOS node (video=%s audio=%s)", video_id, audio_id)

        # Start the IS-05 monitor loop
        bridge.run()

    threading.Thread(target=_init_flows, daemon=True).start()


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class LoadRequest(BaseModel):
    filename: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/files")
async def list_files():
    """List playable media files in MEDIA_DIR."""
    if not MEDIA_DIR.exists():
        return {"files": []}
    files = sorted(
        f.name
        for f in MEDIA_DIR.iterdir()
        if f.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    return {"files": files}


@app.post("/load")
async def load(req: LoadRequest):
    """Load a file by name from MEDIA_DIR and start playing immediately."""
    path = MEDIA_DIR / req.filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {req.filename}")
    player.load(str(path))
    bridge.set_senders_active(True)
    return {"status": "playing", "file": req.filename}


@app.post("/play")
async def play():
    """Start / resume playback."""
    player.play()
    bridge.set_senders_active(True)
    return {"status": "playing"}


@app.post("/stop")
async def stop():
    """Stop playback and release the pipeline."""
    player.stop()
    bridge.set_senders_active(False)
    return {"status": "stopped"}


@app.get("/status")
async def status():
    """Return current player state and metadata."""
    return JSONResponse(content=player.get_status())
