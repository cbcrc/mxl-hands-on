"""
FastAPI backend for the MXL Test Generator.
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

from backend.gst_generator import GstGenerator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
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

# ── Models ────────────────────────────────────────────────────────────────────


class FlowCfg(BaseModel):
    active: bool
    description: str
    label: str


class AudioFlowCfg(FlowCfg):
    channels: int = 2


class StartRequest(BaseModel):
    domain: str
    grouphint: str = "Test-Generator"
    resolution: str = "1920x1080"
    framerate: str = "30"
    video: FlowCfg
    audio1: AudioFlowCfg
    audio2: AudioFlowCfg


class PatternReq(BaseModel):
    pattern: str


class TimecodeReq(BaseModel):
    enabled: bool


class IdentReq(BaseModel):
    text: str


class LevelReq(BaseModel):
    db: float


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
    return generator.get_status()


@app.post("/pipeline/start")
def pipeline_start(req: StartRequest):
    try:
        uuids = generator.start(req.model_dump())
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"status": "started", "flow_uuids": uuids}


@app.post("/pipeline/stop")
def pipeline_stop():
    generator.stop()
    return {"status": "stopped"}


# ── Patterns / options ────────────────────────────────────────────────────────


@app.get("/patterns")
def get_patterns():
    return generator.get_patterns()


@app.get("/options")
def get_options():
    return generator.get_options()


# ── Video ─────────────────────────────────────────────────────────────────────


@app.post("/video/test-pattern")
def video_pattern(req: PatternReq):
    try:
        generator.set_video_pattern(req.pattern)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "ok"}


@app.post("/video/timecode")
def video_timecode(req: TimecodeReq):
    generator.set_timecode(req.enabled)
    return {"status": "ok"}


@app.post("/video/ident")
def video_ident(req: IdentReq):
    generator.set_ident(req.text)
    return {"status": "ok"}


# ── Audio Flow 1 ──────────────────────────────────────────────────────────────


@app.post("/audio/flow1/test-pattern")
def audio1_pattern(req: PatternReq):
    try:
        generator.set_audio_pattern(1, req.pattern)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "ok"}


@app.post("/audio/flow1/level")
def audio1_level_set(req: LevelReq):
    generator.set_audio_level(1, req.db)
    return {"status": "ok", "level_db": generator.get_audio_level(1)}


@app.get("/audio/flow1/level")
def audio1_level_get():
    return {"level_db": generator.get_audio_level(1)}


# ── Audio Flow 2 ──────────────────────────────────────────────────────────────


@app.post("/audio/flow2/test-pattern")
def audio2_pattern(req: PatternReq):
    try:
        generator.set_audio_pattern(2, req.pattern)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "ok"}


@app.post("/audio/flow2/level")
def audio2_level_set(req: LevelReq):
    generator.set_audio_level(2, req.db)
    return {"status": "ok", "level_db": generator.get_audio_level(2)}


@app.get("/audio/flow2/level")
def audio2_level_get():
    return {"level_db": generator.get_audio_level(2)}


# ── Static frontend (must be last — catches everything not matched above) ──────
app.mount("/", StaticFiles(directory="/app/frontend/dist", html=True), name="static")
