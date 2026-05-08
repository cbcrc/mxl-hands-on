"""
GStreamer pipeline wrapper for the MXL file player.

Manages a decodebin pipeline that reads a media file and outputs to two
mxlsink elements (one video, one audio).  Supports load / cue / play /
pause / stop semantics plus automatic EOS-looping.
"""

from __future__ import annotations

import json
import logging
import os
import threading

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst

Gst.init(None)
log = logging.getLogger(__name__)


class GstPlayer:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pipeline: Gst.Pipeline | None = None
        self._file_path: str | None = None
        self._video_flow_id: str | None = None
        self._audio_flow_id: str | None = None
        self._mxl_domain: str = os.getenv("MXL_DOMAIN", "/mxl-domain")
        self._state: str = "idle"

        # GLib main loop (required for GStreamer bus watch)
        self._glib_loop = GLib.MainLoop()
        threading.Thread(target=self._glib_loop.run, daemon=True).start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_flow_ids(self, video_flow_id: str, audio_flow_id: str) -> None:
        with self._lock:
            self._video_flow_id = video_flow_id
            self._audio_flow_id = audio_flow_id
            log.info("Flow IDs set – video=%s  audio=%s", video_flow_id, audio_flow_id)

    def load(self, file_path: str) -> None:
        """Load a file and immediately start playing."""
        with self._lock:
            self._teardown()
            self._file_path = file_path
            self._build_pipeline()
            self._pipeline.set_state(Gst.State.PLAYING)
            self._state = "playing"
            log.info("Loaded and playing: %s", file_path)

    def play(self) -> None:
        with self._lock:
            if not self._pipeline:
                if self._file_path:
                    self._build_pipeline()
                else:
                    log.warning("play() called but no file loaded")
                    return
            self._pipeline.set_state(Gst.State.PLAYING)
            self._state = "playing"
            log.info("Playing")

    def stop(self) -> None:
        with self._lock:
            self._teardown()
            self._state = "stopped" if self._file_path else "idle"
            log.info("Stopped")

    def get_status(self) -> dict:
        with self._lock:
            return {
                "state": self._state,
                "file": self._file_path,
                "video_flow_id": self._video_flow_id,
                "audio_flow_id": self._audio_flow_id,
                "mxl_domain": self._mxl_domain,
            }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_pipeline(self) -> None:
        """Build (or rebuild) the GStreamer pipeline for the current file."""
        if not self._file_path or not self._video_flow_id or not self._audio_flow_id:
            log.warning("Cannot build pipeline – missing file or flow IDs")
            return

        escaped = self._file_path.replace('"', '\\"')
        domain = self._mxl_domain
        vid = self._video_flow_id
        aud = self._audio_flow_id

        pipeline_str = (
            f'filesrc location="{escaped}" '
            f"! decodebin name=dec "
            f"dec. ! videoconvert ! videoscale ! queue "
            f'! mxlsink flow-id="{vid}" domain="{domain}" '
            f"dec. ! audioresample ! audioconvert ! queue "
            f'! mxlsink flow-id="{aud}" domain="{domain}"'
        )
        log.info("Pipeline: %s", pipeline_str)

        pipeline = Gst.parse_launch(pipeline_str)
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::eos", self._on_eos)
        bus.connect("message::error", self._on_error)

        self._pipeline = pipeline
        # Wait up to 10 s for PAUSED so mxlsink has time to write flow_def.json
        pipeline.set_state(Gst.State.PAUSED)
        pipeline.get_state(10 * Gst.SECOND)
        self._patch_flow_defs()

    def _patch_flow_defs(self) -> None:
        """Update label and description in the flow_def.json files written by mxlsink."""
        updates = {
            self._video_flow_id: {
                "label": "File Player – Video",
                "description": "MXL File Player video output",
            },
            self._audio_flow_id: {
                "label": "File Player – Audio",
                "description": "MXL File Player audio output",
            },
        }
        for flow_id, fields in updates.items():
            if not flow_id:
                continue
            path = os.path.join(
                self._mxl_domain, f"{flow_id}.mxl-flow", "flow_def.json"
            )
            try:
                with open(path, "r") as f:
                    data = json.load(f)
                data.update(fields)
                with open(path, "w") as f:
                    json.dump(data, f, indent=2)
                log.info("Patched flow_def.json: %s → %s", flow_id, fields["label"])
            except Exception as exc:
                log.warning("Could not patch flow_def.json for %s: %s", flow_id, exc)

    def _teardown(self) -> None:
        """Set pipeline to NULL and discard it (caller holds lock)."""
        if self._pipeline:
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None

    # ------------------------------------------------------------------
    # Bus callbacks (called from GLib main loop thread)
    # ------------------------------------------------------------------

    def _on_eos(self, _bus: Gst.Bus, _msg: Gst.Message) -> None:
        """Loop: seek back to the beginning on end-of-stream."""
        log.info("EOS – looping back to start")
        with self._lock:
            if self._pipeline and self._state == "playing":
                self._pipeline.seek_simple(
                    Gst.Format.TIME,
                    Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                    0,
                )

    def _on_error(self, _bus: Gst.Bus, msg: Gst.Message) -> None:
        err, debug = msg.parse_error()
        log.error("GStreamer error: %s  debug: %s", err, debug)
        with self._lock:
            self._teardown()
            self._state = "error"
