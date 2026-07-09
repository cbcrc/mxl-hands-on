# SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
# SPDX-License-Identifier: Apache-2.0
"""
FastAPI backend for the MXL HTML5 Keyer.

Endpoints
---------
GET  /config          – UI bootstrap config (default mode)
POST /get-domains     – rescan /mxl-domain for domain_def.json files
GET  /domains         – return cached domain list
GET  /scan-domain     – list MXL flows in a domain  ?domain_path=<path>
POST /pipeline/start  – build and start the pipeline (key or prompt mode)
POST /pipeline/stop   – stop the pipeline
GET  /pipeline/status – return pipeline state
POST /pipeline/key    – toggle key on/off live (compositor.sink_1 alpha)

Prompt (teleprompter) mode adds:
GET  /prompter-api/presets      – resolution/framerate preset list
POST /prompter-api/update       – OGraf updateAction data (scriptText, …)
POST /prompter-api/play         – OGraf playAction
POST /prompter-api/stop         – OGraf stopAction
POST /prompter-api/action       – OGraf customAction (pause/resume/speedUp/speedDown)
WS   /prompter-ws               – the CEF-hosted graphic connects here for commands
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.gst_keyer import GstKeyer
from backend.voice_tracker import VoiceTracker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

MXL_INFO_BIN    = "/opt/mxl/tools/mxl-info/mxl-info"
MXL_DOMAIN_ROOT = os.environ.get("MXL_DOMAIN", "/mxl-domain")

# Mode the UI opens in ("key" | "prompt").  Overridable from docker-compose so a
# deployment can boot straight into the teleprompter form.  The static React
# bundle can't read env at runtime, so it fetches this via GET /config.
KEYER_DEFAULT_MODE = os.environ.get("KEYER_DEFAULT_MODE", "key").strip().lower()
if KEYER_DEFAULT_MODE not in ("key", "prompt"):
    KEYER_DEFAULT_MODE = "key"

# Internal URL the CEF browser loads in prompt mode — the OGraf host page is
# served by this same FastAPI process (see the /prompter static mount below).
PROMPTER_URL = "http://localhost:9600/prompter/"

# Resolution/framerate presets for prompt mode → (width, height, num, den).
RESOLUTION_PRESETS: dict[str, tuple[int, int, int, int]] = {
    "1080p25": (1920, 1080, 25, 1),
    "1080p50": (1920, 1080, 50, 1),
    "1080p60": (1920, 1080, 60, 1),
    "1080p2997": (1920, 1080, 30000, 1001),
    "1080p5994": (1920, 1080, 60000, 1001),
    "720p50":  (1280, 720, 50, 1),
    "720p60":  (1280, 720, 60, 1),
    "720p5994": (1280, 720, 60000, 1001),
    "2160p25": (3840, 2160, 25, 1),
    "2160p50": (3840, 2160, 50, 1),
    "2160p60": (3840, 2160, 60, 1),
}

app = FastAPI(title="MXL HTML5 Keyer")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_domains: list[dict] = []
_keyer = GstKeyer()


# ── Prompter WebSocket hub ─────────────────────────────────────────────────────


class PrompterHub:
    """
    Broadcasts OGraf commands to the CEF-hosted teleprompter page(s) over a
    WebSocket.  The latest data/play state is retained and replayed when a page
    (re)connects, so a script set before CEF finished loading still lands.
    """

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._data: dict = {}
        self._playing = False

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)
        # Replay current state to the freshly connected page.
        if self._data:
            await self._safe_send(ws, {"type": "update", "data": self._data})
        if self._playing:
            await self._safe_send(ws, {"type": "play"})

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    def reset(self) -> None:
        self._data = {}
        self._playing = False

    async def _safe_send(self, ws: WebSocket, msg: dict) -> bool:
        try:
            await ws.send_json(msg)
            return True
        except Exception:
            return False

    async def _broadcast_async(self, msg: dict) -> None:
        dead = []
        for ws in list(self._clients):
            if not await self._safe_send(ws, msg):
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)

    def broadcast(self, msg: dict) -> None:
        """Thread-safe broadcast (callable from any thread)."""
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._broadcast_async(msg), self._loop)

    def update_data(self, data: dict) -> None:
        self._data.update(data)
        self.broadcast({"type": "update", "data": data})

    def set_playing(self, playing: bool) -> None:
        self._playing = playing
        self.broadcast({"type": "play" if playing else "stop"})

    def send_action(self, action: str, param: Optional[str] = None) -> None:
        msg = {"type": "action", "action": action}
        if param is not None:
            msg["param"] = param
        self.broadcast(msg)

    def send_transcript(self, text: str) -> None:
        self.broadcast({"type": "transcript", "text": text})

    def send_voice_command(self, command: str, param: Optional[str] = None) -> None:
        msg = {"type": "voice_command", "command": command}
        if param is not None:
            msg["param"] = param
        self.broadcast(msg)


# Story markers ([STORY: Name] lines) parsed out of scriptText so the
# grammar-constrained voice-command recognizer (VoiceTracker.set_story_names)
# can be rebuilt with today's actual story titles — mirrors the same regex
# used client-side in teleprompter.js / App.jsx.
_STORY_MARKER_RE = re.compile(r"^\[STORY:\s*(.+?)\]\s*$", re.IGNORECASE | re.MULTILINE)


def _parse_story_names(script_text: str) -> list[str]:
    return [m.strip() for m in _STORY_MARKER_RE.findall(script_text)]


_hub = PrompterHub()
_voice = VoiceTracker(
    broadcast=_hub.send_transcript,
    on_command=lambda cmd: _hub.send_voice_command(cmd["command"], cmd.get("param")),
)


# ── Request models ────────────────────────────────────────────────────────────


class StartConfig(BaseModel):
    mode:            str = "key"          # "key" | "prompt"
    domain_path:     str
    # key mode
    input_flow_uuid: str = ""
    html5_url:       str = ""
    # prompt mode
    audio_flow_uuid: Optional[str] = None
    resolution_preset: str = ""
    voice_language:  str = "en-US"
    # common output identity
    grouphint:       str = "HTML5-Keyer"
    description:     str = "keyer-out-1"
    label:           str = "html5-keyer-video"


class KeyBody(BaseModel):
    on: bool


class PrompterUpdate(BaseModel):
    scriptText:          Optional[str]   = None
    scrollSpeed:         Optional[float] = None
    mirrored:            Optional[bool]  = None
    enableVoiceTracking: Optional[bool]  = None
    voiceLanguage:       Optional[str]   = None
    fontSize:            Optional[float] = None
    enableCountdown:     Optional[bool]  = None
    showStatusBar:       Optional[bool]  = None
    reverseScroll:       Optional[bool]  = None


class PrompterAction(BaseModel):
    action: str   # pause | resume | speedUp | speedDown | jumpToStory
    param:  Optional[str] = None   # story name, for jumpToStory


def _full_status() -> dict:
    """Pipeline status augmented with voice-tracking state."""
    status = _keyer.get_status()
    status["voice_tracking"] = _voice.is_enabled()
    status["voice_language"] = _voice.language()
    return status


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


def _parse_scan_output(stdout: str) -> list[dict]:
    flows: list[dict] = []
    current_group = ""
    # mxl-info -d now prints a leading "Domain Definition:" block (id / label /
    # description) before the flow listing.  Skip its indented lines so they are
    # never mistaken for flows.
    in_domain_def = False
    for line in stdout.splitlines():
        if not line.strip():
            continue
        if line[0] in ("\t", " "):
            if in_domain_def:
                continue
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
            if line.strip().startswith("Domain Definition"):
                in_domain_def = True
                continue
            in_domain_def = False
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
    _hub.set_loop(asyncio.get_running_loop())


# ── Routes ────────────────────────────────────────────────────────────────────


@app.post("/get-domains")
async def api_get_domains() -> list[dict]:
    global _domains
    _domains = _scan_domains()
    return _domains


@app.get("/config")
async def api_config() -> dict:
    return {"default_mode": KEYER_DEFAULT_MODE}


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
    if not cfg.description.strip() or not cfg.label.strip():
        raise HTTPException(status_code=400, detail="description and label are required")

    if cfg.mode == "prompt":
        if cfg.resolution_preset not in RESOLUTION_PRESETS:
            raise HTTPException(status_code=400, detail="Unknown resolution_preset")
        W, H, num, den = RESOLUTION_PRESETS[cfg.resolution_preset]
        # Reset prompter state for the new session.
        _hub.reset()
        _voice.reset()
        _voice.set_language(cfg.voice_language)
        try:
            _keyer.start_prompt(
                cfg.domain_path,
                cfg.audio_flow_uuid or None,
                W, H, num, den,
                PROMPTER_URL,
                cfg.grouphint,
                cfg.description,
                cfg.label,
                audio_cb=_voice.feed,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        return _full_status()

    # ── Key mode ──
    if not cfg.input_flow_uuid.strip():
        raise HTTPException(status_code=400, detail="input_flow_uuid is required")
    if not (cfg.html5_url.startswith("http://") or cfg.html5_url.startswith("https://")):
        raise HTTPException(status_code=400, detail="html5_url must start with http:// or https://")
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
    return _full_status()


@app.post("/pipeline/stop")
async def api_pipeline_stop() -> dict:
    _keyer.stop()
    _voice.reset()
    _hub.reset()
    return _full_status()


@app.get("/pipeline/status")
async def api_pipeline_status() -> dict:
    return _full_status()


@app.post("/pipeline/key")
async def api_pipeline_key(body: KeyBody) -> dict:
    try:
        _keyer.set_key(body.on)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _full_status()


# ── Prompter (teleprompter) control — OGraf control points ─────────────────────


@app.get("/prompter-api/presets")
async def api_prompter_presets() -> list[dict]:
    return [
        {"id": pid, "width": w, "height": h, "num": n, "den": d,
         "label": f"{w}×{h} @ {n if d == 1 else f'{n}/{d}'}"}
        for pid, (w, h, n, d) in RESOLUTION_PRESETS.items()
    ]


@app.post("/prompter-api/update")
async def api_prompter_update(body: PrompterUpdate) -> dict:
    data = {k: v for k, v in body.dict().items() if v is not None}
    if "voiceLanguage" in data:
        _voice.set_language(data["voiceLanguage"])
    if "enableVoiceTracking" in data:
        _voice.set_enabled(bool(data["enableVoiceTracking"]))
    if "scriptText" in data:
        _voice.set_story_names(_parse_story_names(data["scriptText"]))
    if data:
        _hub.update_data(data)
    return {"ok": True}


@app.post("/prompter-api/play")
async def api_prompter_play() -> dict:
    _hub.set_playing(True)
    return {"ok": True}


@app.post("/prompter-api/stop")
async def api_prompter_stop() -> dict:
    _hub.set_playing(False)
    return {"ok": True}


@app.post("/prompter-api/action")
async def api_prompter_action(body: PrompterAction) -> dict:
    if body.action not in ("pause", "resume", "speedUp", "speedDown", "jumpToStory"):
        raise HTTPException(status_code=400, detail="Unknown action")
    _hub.send_action(body.action, body.param)
    return {"ok": True}


@app.websocket("/prompter-ws")
async def prompter_ws(ws: WebSocket) -> None:
    await _hub.connect(ws)
    try:
        while True:
            # The page does not send anything we act on; this keeps the socket
            # open and lets us detect disconnects.
            await ws.receive_text()
    except WebSocketDisconnect:
        _hub.disconnect(ws)
    except Exception:
        _hub.disconnect(ws)


# ── Static mounts (must be last) ──────────────────────────────────────────────
# OGraf host page + teleprompter graphic loaded by CEF in prompt mode.
app.mount("/prompter", StaticFiles(directory="/app/prompter", html=True), name="prompter")
# React frontend.
app.mount("/", StaticFiles(directory="/app/frontend/dist", html=True), name="static")
