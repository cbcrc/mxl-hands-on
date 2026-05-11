"""
GStreamer HLS-to-MXL pipeline for the hls2mxl gateway.

Two-phase startup:
  Phase 1 – Warmup (10 s): uridecodebin → fakesink(s)
    Lets HLS settle on the highest quality variant before MXL is involved.
  Phase 2 – Real pipeline:
    uridecodebin uri=<url> connection-speed=50000
        ├─ video pad → videoconvert → videoscale → caps(1920×1080) → videorate → caps(60fps) → videoconvert → queue → mxlsink
        └─ audio pad → audioconvert → audioresample → audio/x-raw,S24LE,2ch,48kHz → queue → mxlsink

The pipeline is torn down and rebuilt each time a new URL is applied.
The MXL flow UUIDs are stable because they come from the NMOS node (not from
this pipeline) and are discovered once at startup.
"""

from __future__ import annotations

import gc
import json
import logging
import os
import shutil
import threading

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst

Gst.init(None)
log = logging.getLogger(__name__)

WARMUP_SECONDS = 10


class GstHls:
    def __init__(self) -> None:
        self._lock      = threading.Lock()
        self._pipeline: Gst.Pipeline | None = None
        self._hls_url:  str  = ""
        self._state:    str  = "idle"   # idle | warming_up | playing | error
        self._error_msg: str = ""
        self._video_flow_id: str | None = None
        self._audio_flow_id: str | None = None
        self._mxl_domain: str = os.getenv("MXL_DOMAIN", "/mxl-domain")

        self._glib_loop = GLib.MainLoop()
        threading.Thread(target=self._glib_loop.run, daemon=True).start()

    # ── Public API ────────────────────────────────────────────────────────────

    def set_flow_ids(self, video_flow_id: str, audio_flow_id: str) -> None:
        with self._lock:
            self._video_flow_id = video_flow_id
            self._audio_flow_id = audio_flow_id
            log.info("Flow IDs set – video=%s  audio=%s", video_flow_id, audio_flow_id)

    def set_url(self, url: str) -> None:
        with self._lock:
            self._hls_url = url
            log.info("HLS URL set → %s", url)

    def apply(self) -> None:
        """Tear down any existing pipeline, clean stale MXL dirs, then start
        a warmup pipeline.  The real MXL pipeline starts after WARMUP_SECONDS."""
        with self._lock:
            if not self._hls_url:
                raise ValueError("No HLS URL configured")
            if not self._video_flow_id or not self._audio_flow_id:
                raise ValueError("Flow IDs not yet discovered")
            self._teardown()
            self._cleanup_flow_dirs()
            self._build_warmup_pipeline()

    def stop(self) -> None:
        with self._lock:
            self._teardown()

    def get_status(self) -> dict:
        with self._lock:
            return {
                "state":         self._state,
                "hls_url":       self._hls_url,
                "video_flow_id": self._video_flow_id,
                "audio_flow_id": self._audio_flow_id,
                "error":         self._error_msg,
            }

    # ── Pipeline ─────────────────────────────────────────────────────────────

    def _cleanup_flow_dirs(self) -> None:
        """Remove stale MXL flow directories (e.g. left over from a hard kill).
        Caller must hold self._lock."""
        for flow_id in (self._video_flow_id, self._audio_flow_id):
            if not flow_id:
                continue
            path = os.path.join(self._mxl_domain, f"{flow_id}.mxl-flow")
            if os.path.exists(path):
                try:
                    shutil.rmtree(path)
                    log.info("Removed stale flow dir: %s", flow_id)
                except Exception as exc:
                    log.warning("Could not remove flow dir %s: %s", flow_id, exc)

    def _teardown(self) -> None:
        """Stop and release the current pipeline. Caller must hold self._lock."""
        if self._pipeline is None:
            return
        log.info("Tearing down pipeline...")
        self._pipeline.set_state(Gst.State.NULL)
        self._pipeline.get_state(5 * Gst.SECOND)
        self._pipeline = None
        gc.collect()
        self._state = "idle"
        log.info("Teardown complete")

    def _build_warmup_pipeline(self) -> None:
        """Phase 1: connect to HLS with fakesinks so the adaptive bitrate logic
        can select the highest quality variant.  Caller must hold self._lock."""
        url = self._hls_url
        pipeline = Gst.Pipeline.new("hls2mxl-warmup")

        src = Gst.ElementFactory.make("uridecodebin", "src")
        src.set_property("uri", url)
        src.set_property("connection-speed", 50000)
        pipeline.add(src)

        # Keep a list so pad-added closure can reference them safely
        _sinks: list[Gst.Element] = []

        def on_pad_added(_src, pad):
            caps = pad.get_current_caps() or pad.query_caps(None)
            name = caps.get_structure(0).get_name() if caps else ""
            log.info("Warmup pad-added: %s", name)
            # Attach a dedicated fakesink for each decoded stream
            fsink = Gst.ElementFactory.make("fakesink", None)
            fsink.set_property("sync", False)
            pipeline.add(fsink)
            fsink.sync_state_with_parent()
            pad.link(fsink.get_static_pad("sink"))
            _sinks.append(fsink)

        src.connect("pad-added", on_pad_added)

        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", lambda b, m: log.warning(
            "Warmup error (ignored): %s", m.parse_error()[0]))

        pipeline.set_state(Gst.State.PLAYING)
        self._pipeline = pipeline
        self._state    = "warming_up"
        log.info("Warmup pipeline started – real MXL pipeline in %ds", WARMUP_SECONDS)

        # Schedule the real pipeline on the GLib main loop (same thread as callbacks)
        GLib.timeout_add(WARMUP_SECONDS * 1000, self._switch_to_real_pipeline)

    def _switch_to_real_pipeline(self) -> bool:
        """Phase 2: called by GLib timer after warmup.  Tears down warmup and
        starts the real pipeline writing to mxlsink."""
        log.info("Warmup complete – switching to MXL pipeline...")
        with self._lock:
            self._teardown()
            self._cleanup_flow_dirs()   # clean anything the warmup may have created
            try:
                self._build_pipeline()
            except Exception as exc:
                log.error("Failed to start MXL pipeline: %s", exc)
                self._state     = "error"
                self._error_msg = str(exc)
        return False  # Don't reschedule

    def _build_pipeline(self) -> None:
        """Build and start the HLS pipeline. Caller must hold self._lock."""
        url    = self._hls_url
        domain = self._mxl_domain
        vid    = self._video_flow_id
        aud    = self._audio_flow_id

        pipeline = Gst.Pipeline.new("hls2mxl")

        # ── Source ────────────────────────────────────────────────────────────
        src = Gst.ElementFactory.make("uridecodebin", "src")
        src.set_property("uri", url)
        # Force highest HLS variant from the first segment so mxlsink never
        # sees a mid-stream format change (connection-speed is in kbps).
        src.set_property("connection-speed", 50000)
        pipeline.add(src)

        # ── Video branch (built now; linked dynamically) ───────────────────
        vconv   = Gst.ElementFactory.make("videoconvert",  "vconv")
        vscale  = Gst.ElementFactory.make("videoscale",    "vscale")
        # Resolution constraint only — videoscale can satisfy this.
        vrcaps  = Gst.ElementFactory.make("capsfilter",    "vrcaps")
        vrcaps.set_property("caps", Gst.Caps.from_string(
            "video/x-raw,width=1920,height=1080"
        ))
        # videorate satisfies the framerate constraint independently.
        vrate   = Gst.ElementFactory.make("videorate",     "vrate")
        vfcaps  = Gst.ElementFactory.make("capsfilter",    "vfcaps")
        vfcaps.set_property("caps", Gst.Caps.from_string(
            "video/x-raw,framerate=60/1"
        ))
        # Normalize pixel format after all scaling/rate conversion so
        # mxlsink always sees the same format even if HLS variant changes
        # the upstream pixel format mid-stream.
        vnorm   = Gst.ElementFactory.make("videoconvert",  "vnorm")
        # Lock the exact caps mxlsink will see — prevents any re-negotiation.
        vfinal  = Gst.ElementFactory.make("capsfilter",    "vfinal")
        vfinal.set_property("caps", Gst.Caps.from_string(
            "video/x-raw,format=v210,width=1920,height=1080,framerate=60/1"
        ))
        vqueue  = Gst.ElementFactory.make("queue",         "vqueue")
        vsink   = Gst.ElementFactory.make("mxlsink",       "vsink")
        vsink.set_property("flow-id", vid)
        vsink.set_property("domain",  domain)
        vsink.set_property("sync",    False)

        for el in (vconv, vscale, vrcaps, vrate, vfcaps, vnorm, vfinal, vqueue, vsink):
            pipeline.add(el)
        vconv.link(vscale)
        vscale.link(vrcaps)
        vrcaps.link(vrate)
        vrate.link(vfcaps)
        vfcaps.link(vnorm)
        vnorm.link(vfinal)
        vfinal.link(vqueue)
        vqueue.link(vsink)

        # ── Audio branch ──────────────────────────────────────────────────────
        # audioconvert first (handles channel/format), then audioresample (rate).
        # mxlsink only accepts F32LE — do NOT use S24LE here.
        aconv     = Gst.ElementFactory.make("audioconvert",  "aconv")
        aresample = Gst.ElementFactory.make("audioresample", "aresample")
        acaps     = Gst.ElementFactory.make("capsfilter",    "acaps")
        acaps.set_property("caps", Gst.Caps.from_string(
            "audio/x-raw,format=F32LE,channels=2,rate=48000,layout=interleaved"
        ))
        aqueue    = Gst.ElementFactory.make("queue",    "aqueue")
        asink     = Gst.ElementFactory.make("mxlsink",  "asink")
        asink.set_property("flow-id", aud)
        asink.set_property("domain",  domain)
        asink.set_property("sync",    False)

        for el in (aconv, aresample, acaps, aqueue, asink):
            pipeline.add(el)
        aconv.link(aresample)
        aresample.link(acaps)
        acaps.link(aqueue)
        aqueue.link(asink)

        # ── Dynamic pad linking ───────────────────────────────────────────────
        def on_pad_added(_src, pad):
            caps = pad.get_current_caps() or pad.query_caps(None)
            name = caps.get_structure(0).get_name() if caps else ""
            log.info("uridecodebin pad-added: %s", name)
            if name.startswith("video/"):
                sink_pad = vconv.get_static_pad("sink")
                if not sink_pad.is_linked():
                    pad.link(sink_pad)
                    log.info("Linked video pad → vconv")
            elif name.startswith("audio/"):
                sink_pad = aconv.get_static_pad("sink")
                if not sink_pad.is_linked():
                    pad.link(sink_pad)
                    log.info("Linked audio pad → aconv")

        src.connect("pad-added", on_pad_added)

        # ── Bus ───────────────────────────────────────────────────────────────
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error",      self._on_error)
        bus.connect("message::eos",        self._on_eos)
        bus.connect("message::state-changed", self._on_state_changed)

        ret = pipeline.set_state(Gst.State.PLAYING)
        log.info("Pipeline set_state(PLAYING) → %s", ret)

        self._pipeline  = pipeline
        self._state     = "playing"
        self._error_msg = ""
        self._patch_flow_defs()

    def _patch_flow_defs(self) -> None:
        for flow_id, label in (
            (self._video_flow_id, "HLS2MXL – Video"),
            (self._audio_flow_id, "HLS2MXL – Audio"),
        ):
            if not flow_id:
                continue
            path = os.path.join(self._mxl_domain, f"{flow_id}.mxl-flow", "flow_def.json")
            try:
                with open(path) as f:
                    data = json.load(f)
                data["label"]       = label
                data["description"] = f"MXL {label} gateway output"
                with open(path, "w") as f:
                    json.dump(data, f, indent=2)
                log.info("Patched flow_def.json: %s → %s", flow_id, label)
            except Exception as exc:
                log.warning("Could not patch flow_def.json for %s: %s", flow_id, exc)

    def _on_error(self, _bus: Gst.Bus, msg: Gst.Message) -> None:
        err, debug = msg.parse_error()
        log.error("GStreamer error: %s  debug: %s", err, debug)
        should_restart = False
        with self._lock:
            self._state     = "error"
            self._error_msg = str(err)
            should_restart  = bool(self._hls_url and self._video_flow_id)
        if should_restart:
            log.info("Scheduling pipeline auto-restart (warmup) in 2s...")
            GLib.timeout_add(2000, self._auto_restart)

    def _auto_restart(self) -> bool:
        """Called from GLib main loop after an error — re-run warmup + real pipeline."""
        log.info("Auto-restarting via warmup...")
        with self._lock:
            self._teardown()
            self._cleanup_flow_dirs()
            try:
                self._build_warmup_pipeline()
            except Exception as exc:
                log.error("Auto-restart failed: %s", exc)
                self._state = "error"
                self._error_msg = str(exc)
        return False

    def _on_eos(self, _bus: Gst.Bus, _msg: Gst.Message) -> None:
        log.info("End of stream")
        with self._lock:
            self._state = "idle"

    def _on_state_changed(self, _bus: Gst.Bus, msg: Gst.Message) -> None:
        if msg.src == self._pipeline:
            _, new, _ = msg.parse_state_changed()
            if new == Gst.State.PLAYING:
                log.info("Pipeline is PLAYING")
