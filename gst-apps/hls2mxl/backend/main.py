# SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
# SPDX-License-Identifier: Apache-2.0
"""
FastAPI backend for the MXL HLS Gateway.
Serves both the REST API and the React frontend on port 9600.
"""

from __future__ import annotations

import json
import logging
import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.gst_hls2mxl import GstHLS2MXL

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

app = FastAPI(title="MXL HLS Gateway")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

gateway = GstHLS2MXL()


# ── Models ────────────────────────────────────────────────────────────────────


class FlowCfg(BaseModel):
    description: str
    label: str


class StartRequest(BaseModel):
    domain: str
    grouphint: str = "HLS2MXL"
    hls_url: str
    video: FlowCfg
    audio: FlowCfg


class ApplyRequest(BaseModel):
    url: str


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
                description = data.get("description", "")
            except Exception:
                domain_id = ""
                label = os.path.basename(root)
                description = ""
            results.append({"path": root, "id": domain_id, "label": label, "description": description})
    return {"domains": results}


# ── Pipeline ──────────────────────────────────────────────────────────────────


@app.get("/pipeline/status")
def pipeline_status():
    return gateway.get_status()


@app.post("/pipeline/start")
def pipeline_start(req: StartRequest):
    try:
        uuids = gateway.start(req.model_dump())
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"status": "started", "flow_uuids": uuids}


@app.post("/pipeline/stop")
def pipeline_stop():
    gateway.stop()
    return {"status": "stopped"}


# ── HLS ───────────────────────────────────────────────────────────────────────


@app.get("/hls/url")
def hls_url():
    return {"url": gateway.get_status()["hls_url"]}


@app.post("/hls/apply")
def hls_apply(req: ApplyRequest):
    if not req.url.strip():
        raise HTTPException(status_code=400, detail="URL cannot be empty")
    try:
        uuids = gateway.apply(req.url.strip())
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"status": "applying", "flow_uuids": uuids}


# ── Static frontend (must be last — catches everything not matched above) ──────
app.mount("/", StaticFiles(directory="/app/frontend/dist", html=True), name="static")
