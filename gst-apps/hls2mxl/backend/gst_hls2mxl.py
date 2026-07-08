# SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
# SPDX-License-Identifier: Apache-2.0
"""
GStreamer HLS-to-MXL pipeline.

Two-phase startup:
  Phase 1 – Warmup (STABILISE_SECONDS):
    uridecodebin → per-pad fakesink(sync=False)
    Lets the HLS adaptive-bitrate logic settle on the highest quality variant
    before mxlsink is involved.  No valves, no blocking of preroll.

  Phase 2 – Real pipeline:
    uridecodebin → [pad-added] → videoconvert → capsfilter(v210)
                                               → queue → mxlsink(sync=False)
                              → [pad-added] → audioconvert → audioresample
                                           → capsfilter(F32LE,48kHz)
                                           → queue → mxlsink(sync=False)

Flow UUIDs are deterministic (UUID v5) so applying a new HLS URL preserves IDs
as long as the group hint is unchanged.

The original single-pipeline valve approach was abandoned because the valve
blocks mxlsink preroll, which stalls the GStreamer state machine and causes
the HLS audio decoder to stop producing data before the valve opens.
"""

from __future__ import annotations

import gc
import json
import logging
import os
import threading
import time
import uuid
from typing import Optional

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst

Gst.init(None)
log = logging.getLogger(__name__)

# Fixed namespace — must never change after deployment; changing it orphans
# previously written flow directories.
_MXL_HLS_NS = uuid.UUID("a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d")

STABILISE_SECONDS = 10


class GstHLS2MXL:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pipeline: Optional[Gst.Pipeline] = None
        self._running = False
        self._stabilising = False
        self._gen = 0  # incremented on each start to cancel stale callbacks

        self._config: Optional[dict] = None
        self._hls_url: str = ""
        self._flow_uuids: dict[str, str] = {}

        self._glib_loop = GLib.MainLoop()
        threading.Thread(target=self._glib_loop.run, daemon=True, name="glib-loop").start()

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self, config: dict) -> dict:
        with self._lock:
            if self._running:
                raise RuntimeError("Pipeline is already running")
            self._config = config
            self._hls_url = config["hls_url"]
            self._gen += 1
            self._flow_uuids = self._derive_uuids(config)
            self._running = True
            self._stabilising = True
            self._build_warmup_pipeline(config, dict(self._flow_uuids), self._gen)
            uuids = dict(self._flow_uuids)
        return uuids

    def stop(self) -> None:
        with self._lock:
            self._teardown()
            self._running = False
            self._stabilising = False
            self._flow_uuids = {}

    def apply(self, new_url: str) -> dict:
        with self._lock:
            if not self._running:
                raise RuntimeError("Pipeline is not running")
            config = dict(self._config)
            config["hls_url"] = new_url
            self._hls_url = new_url
            self._teardown()
            self._config = config
            self._gen += 1
            self._flow_uuids = self._derive_uuids(config)
            self._stabilising = True
            self._build_warmup_pipeline(config, dict(self._flow_uuids), self._gen)
            uuids = dict(self._flow_uuids)
        return uuids

    def get_status(self) -> dict:
        with self._lock:
            return {
                "running":     self._running,
                "stabilising": self._stabilising,
                "hls_url":     self._hls_url,
                "flow_uuids":  dict(self._flow_uuids),
            }

    # ── UUID derivation ───────────────────────────────────────────────────────

    def _derive_uuids(self, config: dict) -> dict[str, str]:
        gh = config.get("grouphint", "HLS2MXL")
        return {
            "video": str(uuid.uuid5(_MXL_HLS_NS, f"{gh}:video")),
            "audio": str(uuid.uuid5(_MXL_HLS_NS, f"{gh}:audio")),
        }

    # ── Pipeline teardown ─────────────────────────────────────────────────────

    def _teardown(self) -> None:
        """Stop and release the current pipeline. Caller must hold self._lock."""
        if self._pipeline is None:
            return
        self._pipeline.set_state(Gst.State.NULL)
        self._pipeline.get_state(5 * Gst.SECOND)
        self._pipeline = None
        gc.collect()
        log.info("Pipeline torn down")

    # ── Phase 1: warmup pipeline ──────────────────────────────────────────────

    def _build_warmup_pipeline(self, config: dict, uuids: dict, gen: int) -> None:
        """Build a throw-away warmup pipeline with fakesinks.
        Caller must hold self._lock."""
        url = config["hls_url"]
        pipeline = Gst.Pipeline.new("hls2mxl-warmup")

        src = Gst.ElementFactory.make("uridecodebin", "src")
        src.set_property("uri", url)
        src.set_property("connection-speed", 50000)
        pipeline.add(src)

        def on_pad_added(_src, pad):
            caps = pad.get_current_caps() or pad.query_caps(None)
            name = caps.get_structure(0).get_name() if caps else ""
            log.info("Warmup pad-added: %s", name)
            fsink = Gst.ElementFactory.make("fakesink", None)
            fsink.set_property("sync", False)
            pipeline.add(fsink)
            fsink.sync_state_with_parent()
            pad.link(fsink.get_static_pad("sink"))

        src.connect("pad-added", on_pad_added)

        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", lambda b, m: log.warning(
            "Warmup GStreamer error (ignored): %s", m.parse_error()[0]))

        pipeline.set_state(Gst.State.PLAYING)
        self._pipeline = pipeline
        log.info("Warmup pipeline started — real MXL pipeline in %ds", STABILISE_SECONDS)

        # Schedule the switch on the GLib main loop (same thread as callbacks)
        GLib.timeout_add(
            STABILISE_SECONDS * 1000,
            self._switch_to_real_pipeline,
            config, uuids, gen,
        )

    # ── Phase 2: real MXL pipeline ────────────────────────────────────────────

    def _switch_to_real_pipeline(self, config: dict, uuids: dict, gen: int) -> bool:
        """GLib timer callback: tear down warmup and start the real pipeline."""
        log.info("Warmup complete — switching to MXL pipeline...")
        with self._lock:
            if self._gen != gen:
                log.info("Stale switch callback (gen mismatch), ignoring")
                return False  # do not reschedule
            self._teardown()
            try:
                self._build_real_pipeline(config, uuids)
                self._stabilising = False
            except Exception as exc:
                log.error("Failed to start MXL pipeline: %s", exc)
                self._running = False
                self._stabilising = False

        # Patch flow_def.json in a background thread (polls until files appear)
        if self._running:
            threading.Thread(
                target=self._patch_flow_defs,
                args=(config, uuids, gen),
                daemon=True,
                name="patch-flow-defs",
            ).start()

        return False  # do not reschedule the GLib timer

    def _build_real_pipeline(self, config: dict, uuids: dict) -> None:
        """Build the production pipeline writing to mxlsink. Caller must hold
        self._lock."""
        domain   = config["domain"]
        url      = config["hls_url"]
        vid_uuid = uuids["video"]
        aud_uuid = uuids["audio"]

        pipeline = Gst.Pipeline.new("hls2mxl")

        # ── Source ────────────────────────────────────────────────────────────
        src = Gst.ElementFactory.make("uridecodebin", "src")
        src.set_property("uri", url)
        src.set_property("connection-speed", 50000)
        pipeline.add(src)

        # ── Video branch ──────────────────────────────────────────────────────
        vconv  = Gst.ElementFactory.make("videoconvert", "vconv")
        vcaps  = Gst.ElementFactory.make("capsfilter",   "vcaps")
        vcaps.set_property("caps", Gst.Caps.from_string("video/x-raw,format=v210"))
        vqueue = Gst.ElementFactory.make("queue",        "vqueue")
        vsink  = Gst.ElementFactory.make("mxlsink",      "vsink")
        vsink.set_property("flow-id", vid_uuid)
        vsink.set_property("domain",  domain)
        vsink.set_property("sync",    False)

        for el in (vconv, vcaps, vqueue, vsink):
            pipeline.add(el)
        vconv.link(vcaps)
        vcaps.link(vqueue)
        vqueue.link(vsink)

        # ── Audio branch ──────────────────────────────────────────────────────
        aconv     = Gst.ElementFactory.make("audioconvert",  "aconv")
        aresample = Gst.ElementFactory.make("audioresample", "aresample")
        acaps     = Gst.ElementFactory.make("capsfilter",    "acaps")
        acaps.set_property("caps", Gst.Caps.from_string(
            "audio/x-raw,format=F32LE,rate=48000,layout=interleaved"
        ))
        aqueue = Gst.ElementFactory.make("queue",   "aqueue")
        asink  = Gst.ElementFactory.make("mxlsink", "asink")
        asink.set_property("flow-id", aud_uuid)
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
        bus.connect("message::error",   self._on_error)
        bus.connect("message::warning", self._on_warning)
        bus.connect("message::eos",     self._on_eos)

        pipeline.set_state(Gst.State.PLAYING)
        self._pipeline = pipeline
        log.info("Real MXL pipeline started (sync=False, no valves)")

    # ── Background: patch flow_def.json ──────────────────────────────────────

    def _patch_flow_defs(self, config: dict, uuids: dict, gen: int) -> None:
        domain    = config["domain"]
        grouphint = config.get("grouphint", "HLS2MXL")
        flows = [
            ("video", config.get("video", {}), "Video"),
            ("audio", config.get("audio", {}), "Audio"),
        ]
        for key, flow_cfg, role in flows:
            flow_uuid = uuids.get(key)
            if not flow_uuid:
                continue
            path = os.path.join(domain, f"{flow_uuid}.mxl-flow", "flow_def.json")
            deadline = time.monotonic() + 15
            while not os.path.exists(path):
                if time.monotonic() > deadline:
                    log.warning("Timeout waiting for flow_def.json: %s", path)
                    break
                with self._lock:
                    if self._gen != gen:
                        return
                time.sleep(0.5)
            if not os.path.exists(path):
                continue
            try:
                with open(path) as f:
                    data = json.load(f)
                full_grouphint = f"{grouphint}:{role}"
                data["grouphint"]   = full_grouphint
                data["description"] = flow_cfg.get("description", "")
                data["label"]       = flow_cfg.get("label", "")
                if isinstance(data.get("tags"), dict):
                    data["tags"]["urn:x-nmos:tag:grouphint/v1.0"] = [full_grouphint]
                with open(path, "w") as f:
                    json.dump(data, f, indent=2)
                log.info("Patched flow_def.json: %s (%s)", key, flow_uuid)
            except Exception as exc:
                log.warning("Could not patch flow_def.json for %s: %s", key, exc)

    # ── Bus message handlers ──────────────────────────────────────────────────

    def _on_error(self, _bus: Gst.Bus, msg: Gst.Message) -> None:
        err, debug = msg.parse_error()
        log.error("GStreamer error: %s  debug: %s", err, debug)

    def _on_warning(self, _bus: Gst.Bus, msg: Gst.Message) -> None:
        warn, debug = msg.parse_warning()
        log.warning("GStreamer warning: %s  debug: %s", warn, debug)

    def _on_eos(self, _bus: Gst.Bus, _msg: Gst.Message) -> None:
        log.info("EOS received")
