# SPDX-FileCopyrightText: 2025 Contributors to the Media eXchange Layer project.
# SPDX-License-Identifier: Apache-2.0
"""
FastAPI backend for the MXL Info GUI.

Endpoints
---------
POST /get-domains          – scan /mxl-domain for domain_def.json files
GET  /domains              – return cached domain list
GET  /scan-domain          – list MXL flows in a domain  ?domain_path=<path>
GET  /flow-info            – get detailed flow info       ?domain_path=<path>&flow_uuid=<uuid>
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

MXL_INFO_BIN  = "/opt/mxl/tools/mxl-info/mxl-info"
MXL_DOMAIN_ROOT = os.environ.get("MXL_DOMAIN", "/mxl-domain")

app = FastAPI(title="MXL Info GUI")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── In-memory state ────────────────────────────────────────────────────────────

_domains: list[dict] = []   # [{id, path}]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _scan_domains() -> list[dict]:
    """Walk MXL_DOMAIN_ROOT and collect every domain_def.json found."""
    found: list[dict] = []
    root = Path(MXL_DOMAIN_ROOT)
    if not root.exists():
        log.warning("MXL_DOMAIN_ROOT %s does not exist", MXL_DOMAIN_ROOT)
        return found
    for def_file in sorted(root.rglob("domain_def.json")):
        try:
            data = json.loads(def_file.read_text())
            domain_id = data.get("id", "unknown")
            # Store the directory that contains domain_def.json so we can pass
            # it directly to: mxl-info -d <directory>
            found.append({"id": domain_id, "path": str(def_file.parent)})
        except Exception as exc:
            log.warning("Could not parse %s: %s", def_file, exc)
    log.info("Domain scan found %d domain(s)", len(found))
    return found


def _parse_scan_output(stdout: str) -> list[dict]:
    """Parse mxl-info -d output into a list of flow dicts.

    Actual output format:
        <Group Name>: <mxl-url>
            <Format> : <UUID> - <Label>
            ...
    """
    flows: list[dict] = []
    current_group = ""
    for line in stdout.splitlines():
        if not line.strip():
            continue
        if line.startswith("\t") or line.startswith("  "):
            # Flow line: "\tVideo : uuid - Label"
            m = re.match(r'^\s*(\w+)\s*:\s*([0-9a-f-]+)\s*-\s*(.+)$', line)
            if m:
                fmt       = m.group(1).strip()
                uuid      = m.group(2).strip()
                label     = m.group(3).strip()
                grouphint = f"{current_group}:{fmt}" if current_group else fmt
                flows.append({
                    "flow_uuid":      uuid,
                    "flow_label":     label,
                    "flow_grouphint": grouphint,
                })
        else:
            # Group header: "Media Function 1: mxl://..."
            m = re.match(r'^([^:]+):', line)
            if m:
                current_group = m.group(1).strip()
    return flows


def _parse_flow_info_output(stdout: str, flow_uuid: str) -> dict:
    """Parse mxl-info -d … -f … output into a structured dict."""
    fields: dict[str, str] = {}
    for line in stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("- Flow"):
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            fields[key.strip()] = val.strip()
    return {"flow_uuid": flow_uuid, "fields": fields}


# ── Startup ────────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup() -> None:
    global _domains
    _domains = _scan_domains()


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.post("/get-domains")
async def api_get_domains() -> list[dict]:
    """Trigger a fresh domain scan and return the updated list."""
    global _domains
    _domains = _scan_domains()
    return _domains


@app.get("/domains")
async def api_domains() -> list[dict]:
    """Return the cached domain list (populated at startup / last scan)."""
    return _domains


@app.get("/scan-domain")
async def api_scan_domain(domain_path: str = Query(..., description="Absolute path to the MXL domain directory")) -> list[dict]:
    """Run mxl-info -d <domain_path> and return the list of MXL flows."""
    try:
        result = subprocess.run(
            [MXL_INFO_BIN, "-d", domain_path],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail=f"mxl-info binary not found at {MXL_INFO_BIN}")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="mxl-info scan timed out")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    if result.returncode != 0:
        log.warning("mxl-info scan-domain stderr: %s", result.stderr.strip())

    return _parse_scan_output(result.stdout)


@app.get("/flow-info")
async def api_flow_info(
    domain_path: str = Query(..., description="Absolute path to the MXL domain directory"),
    flow_uuid: str   = Query(..., description="UUID of the MXL flow"),
) -> dict:
    """Run mxl-info -d <domain_path> -f <flow_uuid> and return parsed info."""
    try:
        result = subprocess.run(
            [MXL_INFO_BIN, "-d", domain_path, "-f", flow_uuid],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail=f"mxl-info binary not found at {MXL_INFO_BIN}")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="mxl-info flow-info timed out")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    if result.returncode != 0:
        log.warning("mxl-info flow-info stderr: %s", result.stderr.strip())

    return _parse_flow_info_output(result.stdout, flow_uuid)
