# SPDX-FileCopyrightText: 2025 Contributors to the Media eXchange Layer project.
# SPDX-License-Identifier: Apache-2.0
"""
GStreamer MXL-to-WebRTC pipeline.

Architecture:
  mxlsrc(video-flow-id)  → videoconvert → video/x-raw,format=I420 → queue → webrtcsink
  mxlsrc(audio-flow-id)  → audioconvert → audioresample → audio/x-raw,format=S16LE,channels=2,rate=48000 → queue → webrtcsink

webrtcsink is configured with:
  - run-signalling-server=true  (embedded WS signaling on port 8443)
  - signaller.host-addr = ws://0.0.0.0:8443  (accept connections from host browser)
  - consumer-added signal: restrict ICE UDP ports to 50000-50020

The pipeline is built/torn down when video/audio flow IDs change.
"""

from __future__ import annotations

import gc
import json
import logging
import os
import threading
import time

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst

Gst.init(None)
log = logging.getLogger(__name__)


class GstMxl2WebRtc:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pipeline: Gst.Pipeline | None = None
        self._mxl_domain = os.getenv("MXL_DOMAIN", "/mxl-domain")

        self._video_flow_id: str | None = None
        self._audio_flow_id: str | None = None
        self._state: str = "idle"   # idle / playing / error
        self._error_msg: str | None = None

        self._glib_loop = GLib.MainLoop()
        threading.Thread(target=self._glib_loop.run, daemon=True).start()

    # ── Public API ────────────────────────────────────────────────────────────

    def connect_video(self, flow_id: str) -> None:
        with self._lock:
            log.info("connect_video: %s", flow_id)
            self._video_flow_id = flow_id
            self._rebuild_pipeline()

    def connect_audio(self, flow_id: str) -> None:
        with self._lock:
            log.info("connect_audio: %s", flow_id)
            self._audio_flow_id = flow_id
            self._rebuild_pipeline()

    def disconnect_video(self) -> None:
        with self._lock:
            log.info("disconnect_video")
            self._teardown()
            self._video_flow_id = None
            self._state = "idle"

    def disconnect_audio(self) -> None:
        with self._lock:
            log.info("disconnect_audio")
            self._teardown()
            self._audio_flow_id = None
            self._state = "idle"

    def get_status(self) -> dict:
        with self._lock:
            return {
                "state":          self._state,
                "video_flow_id":  self._video_flow_id,
                "audio_flow_id":  self._audio_flow_id,
                "error":          self._error_msg,
            }

    # ── Pipeline ─────────────────────────────────────────────────────────────

    def _rebuild_pipeline(self) -> None:
        """Tear down and rebuild if both flow IDs are set. Caller must hold lock."""
        if not self._video_flow_id or not self._audio_flow_id:
            log.info("Skipping rebuild: waiting for both video and audio flow IDs")
            return
        self._teardown()
        try:
            self._build_pipeline()
        except Exception as exc:
            log.error("Failed to build pipeline: %s", exc)
            self._state = "error"
            self._error_msg = str(exc)

    def _teardown(self) -> None:
        """Stop and release the current pipeline. Caller must hold lock."""
        if self._pipeline is None:
            return
        log.info("Tearing down pipeline…")
        self._pipeline.set_state(Gst.State.NULL)
        self._pipeline.get_state(5 * Gst.SECOND)
        self._pipeline = None
        gc.collect()
        log.info("Teardown complete")

    def _build_pipeline(self) -> None:
        """Construct and start the GStreamer pipeline. Caller must hold lock."""
        domain = self._mxl_domain

        pipeline = Gst.Pipeline.new("mxl2webrtc")

        # ── Video branch ──────────────────────────────────────────────────────
        vsrc = Gst.ElementFactory.make("mxlsrc", "vsrc")
        vsrc.set_property("video-flow-id", self._video_flow_id)
        vsrc.set_property("domain", domain)

        vconv = Gst.ElementFactory.make("videoconvert", "vconv")
        vcaps = Gst.ElementFactory.make("capsfilter", "vcaps")
        vcaps.set_property("caps", Gst.Caps.from_string("video/x-raw,format=I420"))
        vqueue = Gst.ElementFactory.make("queue", "vqueue")

        # ── Audio branch ──────────────────────────────────────────────────────
        asrc = Gst.ElementFactory.make("mxlsrc", "asrc")
        asrc.set_property("audio-flow-id", self._audio_flow_id)
        asrc.set_property("domain", domain)

        aconv = Gst.ElementFactory.make("audioconvert", "aconv")
        aresample = Gst.ElementFactory.make("audioresample", "aresample")
        acaps = Gst.ElementFactory.make("capsfilter", "acaps")
        acaps.set_property(
            "caps",
            Gst.Caps.from_string("audio/x-raw,format=S16LE,channels=2,rate=48000,layout=interleaved"),
        )
        aqueue = Gst.ElementFactory.make("queue", "aqueue")

        # ── webrtcsink ────────────────────────────────────────────────────────
        webrtcsink = Gst.ElementFactory.make("webrtcsink", "webrtcsink")
        if not webrtcsink:
            raise RuntimeError("Could not create webrtcsink element – is libgstrswebrtc.so installed?")
        webrtcsink.set_property("run-signalling-server", True)
        try:
            signaller = webrtcsink.get_property("signaller")
            if signaller:
                signaller.set_property("host-addr", "ws://0.0.0.0:8443")
        except Exception as e:
            log.warning("Could not set signaller host-addr: %s", e)
        webrtcsink.connect("consumer-added", self._on_consumer_added)

        # ── Assemble pipeline ─────────────────────────────────────────────────
        for el in (vsrc, vconv, vcaps, vqueue, asrc, aconv, aresample, acaps, aqueue, webrtcsink):
            if not el:
                raise RuntimeError(f"Could not create GStreamer element")
            pipeline.add(el)

        vsrc.link(vconv)
        vconv.link(vcaps)
        vcaps.link(vqueue)

        asrc.link(aconv)
        aconv.link(aresample)
        aresample.link(acaps)
        acaps.link(aqueue)

        video_sink_pad = webrtcsink.get_request_pad("video_%u")
        audio_sink_pad = webrtcsink.get_request_pad("audio_%u")

        vqueue.get_static_pad("src").link(video_sink_pad)
        aqueue.get_static_pad("src").link(audio_sink_pad)

        # ── Bus handlers ──────────────────────────────────────────────────────
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error",         self._on_error)
        bus.connect("message::eos",           self._on_eos)
        bus.connect("message::state-changed", self._on_state_changed)

        ret = pipeline.set_state(Gst.State.PLAYING)
        log.info("Pipeline set_state(PLAYING) → %s", ret)

        self._pipeline = pipeline
        self._state    = "playing"
        self._error_msg = None

        threading.Thread(target=self._patch_flow_defs, daemon=True).start()

    # ── GStreamer callbacks ────────────────────────────────────────────────────

    def _on_consumer_added(self, _sink, consumer_id: str, webrtcbin) -> None:
        log.info("WebRTC consumer added: %s", consumer_id)
        try:
            agent = webrtcbin.get_property("ice-agent")
            if agent:
                agent.set_property("min-rtp-port", 50000)
                agent.set_property("max-rtp-port", 50020)
        except Exception as exc:
            log.warning("Could not restrict ICE ports: %s", exc)

    def _on_error(self, _bus: Gst.Bus, msg: Gst.Message) -> None:
        err, debug = msg.parse_error()
        log.error("GStreamer error: %s  debug: %s", err, debug)
        with self._lock:
            self._state     = "error"
            self._error_msg = str(err)

    def _on_eos(self, _bus: Gst.Bus, _msg: Gst.Message) -> None:
        log.info("End of stream")

    def _on_state_changed(self, _bus: Gst.Bus, msg: Gst.Message) -> None:
        if msg.src != self._pipeline:
            return
        _old, new, _pending = msg.parse_state_changed()
        if new == Gst.State.PLAYING:
            with self._lock:
                self._state = "playing"
        elif new in (Gst.State.NULL, Gst.State.READY):
            with self._lock:
                if self._state == "playing":
                    self._state = "idle"

    # ── Flow def patching ─────────────────────────────────────────────────────

    def _patch_flow_defs(self) -> None:
        """Patch flow_def.json for video and audio flows (called in background thread)."""
        flows = {
            self._video_flow_id: ("MXL to WebRTC – Video", "MXL video input for WebRTC output"),
            self._audio_flow_id: ("MXL to WebRTC – Audio", "MXL audio input for WebRTC output"),
        }
        for flow_id, (label, description) in flows.items():
            if not flow_id:
                continue
            path = os.path.join(self._mxl_domain, f"{flow_id}.mxl-flow", "flow_def.json")
            for attempt in range(20):
                try:
                    with open(path) as f:
                        data = json.load(f)
                    data["label"]       = label
                    data["description"] = description
                    with open(path, "w") as f:
                        json.dump(data, f, indent=2)
                    log.info("Patched flow_def.json for %s (attempt %d)", flow_id, attempt + 1)
                    break
                except FileNotFoundError:
                    time.sleep(0.5)
                except Exception as exc:
                    log.warning("Could not patch flow_def.json for %s: %s", flow_id, exc)
                    break
