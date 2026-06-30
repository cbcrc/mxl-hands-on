# SPDX-FileCopyrightText: 2025 Contributors to the Media eXchange Layer project.
# SPDX-License-Identifier: Apache-2.0
"""
FastAPI backend for the WebRTC-to-MXL Gateway.
Serves both the REST API and the React frontend on port 9600.

Endpoints
---------
GET    /config           – return the MediaMTX WHIP URL for the browser publisher
POST   /get-domains      – scan /mxl-domain for domain_def.json files
GET    /domains          – return the cached domain list
POST   /pipeline/start   – build and start the WHEP→mxlsink pipeline
POST   /pipeline/stop    – stop the pipeline
GET    /pipeline/status  – return pipeline state and the active flow
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.gst_webrtc2mxl import GstWriter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

MXL_DOMAIN_ROOT = os.environ.get("MXL_DOMAIN", "/mxl-domain")
MEDIAMTX_WHIP   = os.environ.get("MEDIAMTX_WHIP_URL",
                                 "http://localhost:8889/webrtc2mxl/whip")

app = FastAPI(title="WebRTC to MXL Gateway")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_domains: list[dict] = []
_writer = GstWriter()


# ── Request models ──────────────────────────────────────────────────────────

class StartConfig(BaseModel):
    domain_path: str
    grouphint: str = "WEBRTC2MXL"
    label: str = "webrtc-audio"
    description: str = "webrtc-audio-out"


# ── Domain scanning ─────────────────────────────────────────────────────────

def _scan_domains() -> list[dict]:
    found: list[dict] = []
    root = Path(MXL_DOMAIN_ROOT)
    if not root.exists():
        log.warning("MXL_DOMAIN_ROOT %s does not exist", MXL_DOMAIN_ROOT)
        return found
    for def_file in sorted(root.rglob("domain_def.json")):
        try:
            data = json.loads(def_file.read_text())
            found.append({
                "id":          data.get("id", "unknown"),
                "label":       data.get("label", ""),
                "description": data.get("description", ""),
                "path":        str(def_file.parent),
            })
        except Exception as exc:
            log.warning("Could not parse %s: %s", def_file, exc)
    log.info("Domain scan found %d domain(s)", len(found))
    return found


# ── Startup ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup() -> None:
    global _domains
    _domains = _scan_domains()


# ── Routes ──────────────────────────────────────────────────────────────────

@app.get("/config")
async def api_config() -> dict:
    return {"mediamtx_whip_url": MEDIAMTX_WHIP}


@app.post("/get-domains")
async def api_get_domains() -> list[dict]:
    global _domains
    _domains = _scan_domains()
    return _domains


@app.get("/domains")
async def api_domains() -> list[dict]:
    return _domains


@app.post("/pipeline/start")
async def api_pipeline_start(cfg: StartConfig) -> dict:
    if not cfg.grouphint.strip() or not cfg.label.strip() or not cfg.description.strip():
        raise HTTPException(status_code=400, detail="Group hint, label and description are required")
    try:
        return _writer.start(
            cfg.domain_path,
            cfg.grouphint.strip(),
            cfg.label.strip(),
            cfg.description.strip(),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/pipeline/stop")
async def api_pipeline_stop() -> dict:
    _writer.stop()
    return _writer.get_status()


@app.get("/pipeline/status")
async def api_pipeline_status() -> dict:
    return _writer.get_status()


# ── Static frontend (must be last) ──────────────────────────────────────────
app.mount("/", StaticFiles(directory="/app/frontend/dist", html=True), name="static")
