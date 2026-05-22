"""
FastAPI backend for the MXL Input Selector.

Endpoints
---------
GET  /status         – current state (selected/active input, slot connections, flow IDs)
POST /input-select   – pre-select an input  {"input": 1|2|3}
POST /take           – switch output to the pre-selected input
"""

from __future__ import annotations

import logging
import threading

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.gst_selector import GstSelector
from backend.nmos_bridge import NmosBridge

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

app = FastAPI(title="MXL Input Selector")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

selector = GstSelector()
bridge = NmosBridge(
    nmos_base="http://localhost:9530",
    on_connect_input=selector.connect_input,
    on_disconnect_input=selector.disconnect_input,
)


# ── Startup ───────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup() -> None:
    def _init() -> None:
        # Discover the sender's flow ID first so the pipeline can be built
        output_flow_id = bridge.discover_output_flow_id()
        if output_flow_id:
            selector.set_output_flow_id(output_flow_id)
        else:
            log.error("Could not discover output flow ID – pipeline will not start")

        # Discover the 3 receivers; the poll loop then monitors IS-05 activations
        bridge.discover_receivers()
        bridge.run()  # blocks – poll loop

    threading.Thread(target=_init, name="nmos-bridge", daemon=True).start()


# ── Models ────────────────────────────────────────────────────────────────────

class InputSelectReq(BaseModel):
    input: int  # 1, 2, or 3


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/status")
async def status():
    return selector.get_status()


@app.post("/input-select")
async def input_select(req: InputSelectReq):
    if req.input not in (1, 2, 3):
        raise HTTPException(status_code=400, detail="input must be 1, 2, or 3")
    selector.select_input(req.input)
    return {"status": "ok", "selected_input": req.input}


@app.post("/take")
async def take():
    st = selector.get_status()
    if st["selected_input"] is None:
        raise HTTPException(status_code=400, detail="No input pre-selected")
    selector.take()
    return {"status": "ok", "active_input": selector.get_status()["active_input"]}
