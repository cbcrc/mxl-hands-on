# SPDX-FileCopyrightText: 2025 Contributors to the Media eXchange Layer project.
# SPDX-License-Identifier: Apache-2.0
"""
FastAPI backend for the MXL-to-WebRTC gateway.

Endpoints
---------
GET /status  – pipeline state, video/audio MXL flow IDs
"""

from __future__ import annotations

import logging
import os
import threading

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.gst_mxl2webrtc import GstMxl2WebRtc
from backend.nmos_bridge import NmosBridge

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

app = FastAPI(title="MXL to WebRTC Gateway")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

pipeline = GstMxl2WebRtc()
bridge = NmosBridge(
    nmos_base="http://localhost:9550",
    on_connect_video=pipeline.connect_video,
    on_connect_audio=pipeline.connect_audio,
    on_disconnect_video=pipeline.disconnect_video,
    on_disconnect_audio=pipeline.disconnect_audio,
)


# ── Startup ───────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup() -> None:
    def _init() -> None:
        bridge.discover_receivers()
        bridge.run()  # blocks – poll loop

    threading.Thread(target=_init, name="nmos-bridge", daemon=True).start()


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/status")
async def status():
    return pipeline.get_status()


# ── Static frontend ───────────────────────────────────────────────────────────

from fastapi.staticfiles import StaticFiles

if os.path.isdir("/app/frontend/dist"):
    app.mount("/", StaticFiles(directory="/app/frontend/dist", html=True), name="static")
