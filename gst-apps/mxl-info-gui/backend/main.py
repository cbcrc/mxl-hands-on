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
GET  /orphan-flows         – list .mxl-flow dirs not reported by mxl-info ?domain_path=<path>
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

MXL_INFO_BIN    = "/opt/mxl/tools/mxl-info/mxl-info"
MXL_DOMAIN_ROOT = os.environ.get("MXL_DOMAIN", "/mxl-domain")

# UUID pattern used for matching in flow lines
_UUID_RE = re.compile(
    r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
    re.IGNORECASE,
)

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
            found.append({"id": domain_id, "path": str(def_file.parent)})
        except Exception as exc:
            log.warning("Could not parse %s: %s", def_file, exc)
    log.info("Domain scan found %d domain(s)", len(found))
    return found


def _parse_scan_output(stdout: str) -> list[dict]:
    """Parse mxl-info -d output into a list of flow dicts.

    Output format (non-terminal):
        <GroupName>: mxl:///<domain>?id=<uuid>...
            <RoleInGroup> : <UUID> - <Label>

    When the flow has no grouphint group name the header reads:
        Invalid group name (empty string): mxl://...

    When the flow has no grouphint role the role column reads "MISSING ROLE".
    """
    flows: list[dict] = []
    current_group = ""
    for line in stdout.splitlines():
        if not line.strip():
            continue
        if line[0] in ("\t", " "):
            # Flow line: "\t<role> : <uuid> - <label>"
            # role may be "MISSING ROLE" or a format string like "Video"
            m = re.match(
                r'^\s+(.+?)\s*:\s*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\s*-\s*(.+)$',
                line,
                re.IGNORECASE,
            )
            if m:
                role  = m.group(1).strip()
                uuid  = m.group(2).strip()
                label = m.group(3).strip()
                # Treat "MISSING ROLE" as an absent role
                if role.upper() == "MISSING ROLE":
                    role = ""
                if current_group and role:
                    grouphint = f"{current_group}:{role}"
                else:
                    grouphint = current_group or role
                flows.append({
                    "flow_uuid":      uuid,
                    "flow_label":     label,
                    "flow_grouphint": grouphint,
                })
        else:
            # Group header line: "<GroupName>: mxl://..."
            # Detect the special "invalid/empty group" marker
            if line.strip().startswith("Invalid group name"):
                current_group = ""
            else:
                m = re.match(r'^([^:]+):', line)
                if m:
                    current_group = m.group(1).strip()
    return flows


def _parse_flow_info_output(stdout: str, flow_uuid: str) -> dict:
    """Parse mxl-info -d … -f … output into a structured dict.

    Output format:
        - Flow [<uuid>]
            <Key padded>: <value>
            ...
    """
    fields: dict[str, str] = {}
    for line in stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("- Flow"):
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            fields[key.strip()] = val.strip()
    return {"flow_uuid": flow_uuid, "fields": fields}


def _scan_domain_path(domain_path: str) -> list[dict]:
    """Run mxl-info -d and return parsed flows (shared by two endpoints)."""
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
        log.warning("mxl-info stderr: %s", result.stderr.strip())

    return _parse_scan_output(result.stdout)


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
async def api_scan_domain(
    domain_path: str = Query(..., description="Absolute path to the MXL domain directory"),
) -> list[dict]:
    """Run mxl-info -d <domain_path> and return the list of MXL flows."""
    return _scan_domain_path(domain_path)


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


@app.get("/orphan-flows")
async def api_orphan_flows(
    domain_path: str = Query(..., description="Absolute path to the MXL domain directory"),
) -> list[dict]:
    """Return .mxl-flow directories in domain_path that mxl-info -d does not report.

    These are flows whose on-disk directories exist but whose flow definition
    cannot be read by the MXL library (e.g. inactive, corrupt, or leftover
    from a previous session).
    """
    domain = Path(domain_path)
    if not domain.is_dir():
        raise HTTPException(status_code=404, detail="Domain path not found")

    # Collect UUIDs known to mxl-info
    known_flows = _scan_domain_path(domain_path)
    known_uuids = {f["flow_uuid"].lower() for f in known_flows}

    orphans: list[dict] = []
    for entry in sorted(domain.iterdir()):
        if not (entry.is_dir() and entry.suffix == ".mxl-flow"):
            continue
        uuid_str = entry.stem
        if not _UUID_RE.fullmatch(uuid_str):
            continue
        if uuid_str.lower() in known_uuids:
            continue

        # Attempt to read flow_def.json for basic metadata
        label       = ""
        grouphint   = ""
        description = ""
        flow_def_path = entry / "flow_def.json"
        if flow_def_path.exists():
            try:
                flow_def = json.loads(flow_def_path.read_text())
                label       = flow_def.get("label", "")
                description = flow_def.get("description", "")
                tags        = flow_def.get("tags", {})
                gh_array    = tags.get("urn:x-nmos:tag:grouphint/v1.0", [])
                if gh_array:
                    grouphint = gh_array[0]
            except Exception as exc:
                log.warning("Could not parse flow_def.json for %s: %s", uuid_str, exc)

        orphans.append({
            "flow_uuid":      uuid_str,
            "flow_label":     label,
            "flow_grouphint": grouphint,
            "description":    description,
            "directory":      str(entry),
        })

    log.info("Orphan flow scan in %s found %d orphan(s)", domain_path, len(orphans))
    return orphans


# ── Static frontend (must be last — catches everything not matched above) ──────
app.mount("/", StaticFiles(directory="/app/frontend/dist", html=True), name="static")
