"""
FastAPI backend for the MXL HTML5 Keyer.

Endpoints
---------
GET  /status          – current state (key_enabled, input/output flow IDs, pipeline status)
POST /keyer-control   – toggle the key overlay  {"enabled": true|false}
"""

from __future__ import annotations

import logging
import threading

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.gst_keyer import GstKeyer
from backend.nmos_bridge import NmosBridge

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

app = FastAPI(title="MXL HTML5 Keyer")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

keyer = GstKeyer()
bridge = NmosBridge(
    nmos_base="http://localhost:9540",
    on_connect=keyer.connect_input,
    on_disconnect=keyer.disconnect_input,
)


# ── Startup ───────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup() -> None:
    def _init() -> None:
        # Discover the sender's flow ID so the pipeline output can be initialised
        output_flow_id = bridge.discover_output_flow_id()
        if output_flow_id:
            keyer.set_output_flow_id(output_flow_id)
        else:
            log.error("Could not discover output flow ID – pipeline will not start")

        # Discover the single receiver
        bridge.discover_receiver()

        # Start the polling loop (blocks – runs in daemon thread)
        bridge.run()

    threading.Thread(target=_init, name="nmos-bridge", daemon=True).start()


# ── Models ────────────────────────────────────────────────────────────────────

class KeyerControlReq(BaseModel):
    enabled: bool


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/status")
async def status():
    return keyer.get_status()


@app.post("/keyer-control")
async def keyer_control(req: KeyerControlReq):
    new_state = keyer.set_key_state(req.enabled)
    return {"status": "ok", "key_enabled": new_state}
