"""
FastAPI backend for the MXL File Player.
Serves both the REST API and the React frontend on port 9600.
"""

from __future__ import annotations

import json
import logging
import os

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.gst_player import MEDIA_ROOT, GstPlayer, probe_file

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

app = FastAPI(title="MXL File Player")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

player = GstPlayer()

# ── Models ────────────────────────────────────────────────────────────────────


class FlowCfg(BaseModel):
    active: bool = True
    description: str = ""
    label: str = ""


class StartRequest(BaseModel):
    domain: str
    file: str
    grouphint: str = "Clip-Player"
    video: FlowCfg = FlowCfg()
    audio: FlowCfg = FlowCfg()


# ── Domains ───────────────────────────────────────────────────────────────────


@app.get("/domains")
def get_domains():
    results = []
    mxl_root = "/mxl-domain"
    if not os.path.isdir(mxl_root):
        return {"domains": []}
    for root, _dirs, files in os.walk(mxl_root):
        if "domain_def.json" in files:
            path = os.path.join(root, "domain_def.json")
            try:
                with open(path) as f:
                    data = json.load(f)
                domain_id = data.get("id", data.get("uuid", ""))
                label = data.get("label", os.path.basename(root))
            except Exception:
                domain_id = ""
                label = os.path.basename(root)
            results.append({"path": root, "id": domain_id, "label": label})
    return {"domains": results}


# ── Files ─────────────────────────────────────────────────────────────────────


@app.get("/files")
def get_files():
    if not os.path.isdir(MEDIA_ROOT):
        return {"files": []}
    try:
        entries = sorted(
            name for name in os.listdir(MEDIA_ROOT)
            if os.path.isfile(os.path.join(MEDIA_ROOT, name))
        )
    except OSError as exc:
        log.warning("Could not list %s: %s", MEDIA_ROOT, exc)
        return {"files": []}
    return {"files": entries}


@app.get("/files/probe")
def get_file_probe(path: str = Query(..., description="filename within /home/file")):
    try:
        return probe_file(path)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        log.warning("Probe failed for %s: %s", path, exc)
        raise HTTPException(status_code=500, detail=f"probe failed: {exc}")


# ── Pipeline ──────────────────────────────────────────────────────────────────


@app.get("/pipeline/status")
def pipeline_status():
    return player.get_status()


@app.post("/pipeline/start")
def pipeline_start(req: StartRequest):
    try:
        uuids = player.start(req.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"status": "started", "flow_uuids": uuids}


@app.post("/pipeline/stop")
def pipeline_stop():
    player.stop()
    return {"status": "stopped"}


# ── Static frontend (must be last — catches everything not matched above) ─────
app.mount("/", StaticFiles(directory="/app/frontend/dist", html=True), name="static")
