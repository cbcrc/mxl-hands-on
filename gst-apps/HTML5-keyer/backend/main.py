"""
FastAPI backend for the MXL HTML5 Keyer.

Endpoints
---------
POST /get-domains     – rescan /mxl-domain for domain_def.json files
GET  /domains         – return cached domain list
GET  /scan-domain     – list MXL flows in a domain  ?domain_path=<path>
POST /pipeline/start  – read input format, build and start the pipeline
POST /pipeline/stop   – stop the pipeline
GET  /pipeline/status – return pipeline state
POST /pipeline/key    – toggle key on/off live (compositor.sink_1 alpha)
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.gst_keyer import GstKeyer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

MXL_INFO_BIN    = "/opt/mxl/tools/mxl-info/mxl-info"
MXL_DOMAIN_ROOT = os.environ.get("MXL_DOMAIN", "/mxl-domain")

app = FastAPI(title="MXL HTML5 Keyer")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_domains: list[dict] = []
_keyer = GstKeyer()


# ── Request models ────────────────────────────────────────────────────────────


class StartConfig(BaseModel):
    domain_path:     str
    input_flow_uuid: str
    html5_url:       str
    grouphint:       str = "HTML5-Keyer"
    description:     str = "keyer-out-1"
    label:           str = "html5-keyer-video"


class KeyBody(BaseModel):
    on: bool


# ── Domain / flow scanning helpers (same logic as input-selector) ─────────────


def _scan_domains() -> list[dict]:
    found: list[dict] = []
    root = Path(MXL_DOMAIN_ROOT)
    if not root.exists():
        log.warning("MXL_DOMAIN_ROOT %s does not exist", MXL_DOMAIN_ROOT)
        return found
    for def_file in sorted(root.rglob("domain_def.json")):
        try:
            data = json.loads(def_file.read_text())
            found.append({"id": data.get("id", "unknown"), "path": str(def_file.parent)})
        except Exception as exc:
            log.warning("Could not parse %s: %s", def_file, exc)
    log.info("Domain scan found %d domain(s)", len(found))
    return found


def _parse_scan_output(stdout: str) -> list[dict]:
    flows: list[dict] = []
    current_group = ""
    for line in stdout.splitlines():
        if not line.strip():
            continue
        if line[0] in ("\t", " "):
            m = re.match(
                r'^\s+(.+?)\s*:\s*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\s*-\s*(.+)$',
                line,
                re.IGNORECASE,
            )
            if m:
                role  = m.group(1).strip()
                uuid_ = m.group(2).strip()
                label = m.group(3).strip()
                if role.upper() == "MISSING ROLE":
                    role = ""
                grouphint = f"{current_group}:{role}" if current_group and role else current_group or role
                flows.append({"flow_uuid": uuid_, "flow_label": label, "flow_grouphint": grouphint})
        else:
            if line.strip().startswith("Invalid group name"):
                current_group = ""
            else:
                m = re.match(r'^([^:]+):', line)
                if m:
                    current_group = m.group(1).strip()
    return flows


def _read_flow_description(domain_path: str, flow_uuid: str) -> str:
    path = Path(domain_path) / f"{flow_uuid}.mxl-flow" / "flow_def.json"
    if not path.exists():
        return ""
    try:
        return json.loads(path.read_text()).get("description", "")
    except Exception:
        return ""


def _scan_domain_path(domain_path: str) -> list[dict]:
    try:
        result = subprocess.run(
            [MXL_INFO_BIN, "-d", domain_path],
            capture_output=True, text=True, timeout=10,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail=f"mxl-info binary not found at {MXL_INFO_BIN}")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="mxl-info scan timed out")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    if result.returncode != 0:
        log.warning("mxl-info stderr: %s", result.stderr.strip())

    flows = _parse_scan_output(result.stdout)
    for flow in flows:
        flow["description"] = _read_flow_description(domain_path, flow["flow_uuid"])
    return flows


# ── Startup ───────────────────────────────────────────────────────────────────


@app.on_event("startup")
async def startup() -> None:
    global _domains
    _domains = _scan_domains()


# ── Routes ────────────────────────────────────────────────────────────────────


@app.post("/get-domains")
async def api_get_domains() -> list[dict]:
    global _domains
    _domains = _scan_domains()
    return _domains


@app.get("/domains")
async def api_domains() -> list[dict]:
    return _domains


@app.get("/scan-domain")
async def api_scan_domain(
    domain_path: str = Query(..., description="Absolute path to the MXL domain directory"),
) -> list[dict]:
    return _scan_domain_path(domain_path)


@app.post("/pipeline/start")
async def api_pipeline_start(cfg: StartConfig) -> dict:
    if not cfg.input_flow_uuid.strip():
        raise HTTPException(status_code=400, detail="input_flow_uuid is required")
    if not (cfg.html5_url.startswith("http://") or cfg.html5_url.startswith("https://")):
        raise HTTPException(status_code=400, detail="html5_url must start with http:// or https://")
    if not cfg.description.strip() or not cfg.label.strip():
        raise HTTPException(status_code=400, detail="description and label are required")
    try:
        _keyer.start(
            cfg.domain_path,
            cfg.input_flow_uuid,
            cfg.html5_url,
            cfg.grouphint,
            cfg.description,
            cfg.label,
        )
    except ValueError as exc:
        detail = exc.args[0] if exc.args else {"detail": str(exc)}
        raise HTTPException(status_code=400, detail=detail)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return _keyer.get_status()


@app.post("/pipeline/stop")
async def api_pipeline_stop() -> dict:
    _keyer.stop()
    return _keyer.get_status()


@app.get("/pipeline/status")
async def api_pipeline_status() -> dict:
    return _keyer.get_status()


@app.post("/pipeline/key")
async def api_pipeline_key(body: KeyBody) -> dict:
    try:
        _keyer.set_key(body.on)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _keyer.get_status()


# ── Static frontend (must be last) ────────────────────────────────────────────
app.mount("/", StaticFiles(directory="/app/frontend/dist", html=True), name="static")
