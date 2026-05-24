# SPDX-FileCopyrightText: 2025 Contributors to the Media eXchange Layer project.
# SPDX-License-Identifier: Apache-2.0
"""
GStreamer MXL-to-WebRTC pipeline.

Reads one MXL video flow and/or one MXL audio flow via mxlsrc, encodes them,
and publishes to MediaMTX via WHIP using webrtcbin + a Python WHIP handshake.
MediaMTX does a near-passthrough to WebRTC for the browser.

Supported modes (determined by which flow UUIDs are supplied):
  video + audio  → mxlsrc(video) + mxlsrc(audio) → webrtcbin
  video only     → mxlsrc(video) → webrtcbin
  audio only     → mxlsrc(audio) → webrtcbin

WHIP handshake (triggered by webrtcbin's on-negotiation-needed signal):
  1. on-offer-created  → set-local-description
  2. on-ice-gathering-done → POST SDP offer to MEDIAMTX_WHIP_URL
  3. set-remote-description with SDP answer (201 response body)
"""

from __future__ import annotations

import gc
import logging
import os
import threading
import urllib.error
import urllib.request

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstWebRTC", "1.0")
gi.require_version("GstSdp", "1.0")
from gi.repository import GLib, Gst, GstSdp, GstWebRTC

Gst.init(None)
log = logging.getLogger(__name__)

MEDIAMTX_WHIP = os.environ.get("MEDIAMTX_WHIP_URL", "http://localhost:8889/mxl2webrtc/whip")

# ICE gathering timeout in seconds before sending the offer anyway
_ICE_TIMEOUT = 10.0


class GstReceiver:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pipeline: Gst.Pipeline | None = None
        self._webrtcbin: Gst.Element | None = None
        self._running = False
        self._error_msg: str | None = None
        self._video_flow_uuid: str | None = None
        self._audio_flow_uuid: str | None = None
        self._domain_path: str | None = None

        self._glib_loop = GLib.MainLoop()
        threading.Thread(target=self._glib_loop.run, daemon=True).start()

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self, domain_path: str, video_flow_uuid: str | None, audio_flow_uuid: str | None) -> None:
        with self._lock:
            self._teardown()
            self._domain_path = domain_path
            self._video_flow_uuid = video_flow_uuid
            self._audio_flow_uuid = audio_flow_uuid
            self._error_msg = None
            try:
                self._build_pipeline()
            except Exception as exc:
                log.error("Failed to build pipeline: %s", exc)
                self._error_msg = str(exc)
                self._running = False
                raise

    def stop(self) -> None:
        with self._lock:
            self._teardown()
            self._video_flow_uuid = None
            self._audio_flow_uuid = None
            self._domain_path = None

    def get_status(self) -> dict:
        with self._lock:
            v = self._video_flow_uuid
            a = self._audio_flow_uuid
            if v and a:
                mode = "video+audio"
            elif v:
                mode = "video"
            elif a:
                mode = "audio"
            else:
                mode = "stopped"
            return {
                "running":          self._running,
                "video_flow_uuid":  v,
                "audio_flow_uuid":  a,
                "mode":             mode if self._running else "stopped",
                "error":            self._error_msg,
            }

    # ── Pipeline construction ─────────────────────────────────────────────────

    def _build_pipeline(self) -> None:
        v = self._video_flow_uuid
        a = self._audio_flow_uuid
        domain = self._domain_path

        pipeline = Gst.Pipeline.new("mxl2webrtc")

        webrtcbin = Gst.ElementFactory.make("webrtcbin", "webrtcbin")
        if not webrtcbin:
            raise RuntimeError("Could not create webrtcbin — is gstreamer1.0-plugins-bad installed?")
        webrtcbin.set_property("bundle-policy", GstWebRTC.WebRTCBundlePolicy.MAX_BUNDLE)
        webrtcbin.set_property("stun-server", "")
        pipeline.add(webrtcbin)

        if v:
            self._add_video_branch(pipeline, webrtcbin, v, domain)
        if a:
            self._add_audio_branch(pipeline, webrtcbin, a, domain)

        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_error)
        bus.connect("message::eos", self._on_eos)
        bus.connect("message::state-changed", self._on_state_changed)

        webrtcbin.connect("on-negotiation-needed", self._on_negotiation_needed)
        webrtcbin.connect("on-ice-candidate", self._on_ice_candidate)

        self._pipeline = pipeline
        self._webrtcbin = webrtcbin

        ret = pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            pipeline.set_state(Gst.State.NULL)
            raise RuntimeError("Pipeline failed to reach PLAYING state")

        self._running = True
        mode = "video+audio" if v and a else "video" if v else "audio"
        log.info("Pipeline started (mode: %s)", mode)

    def _add_video_branch(
        self,
        pipeline: Gst.Pipeline,
        webrtcbin: Gst.Element,
        flow_uuid: str,
        domain: str,
    ) -> None:
        elements = [
            ("mxlsrc",      "mxlsrc-video"),
            ("capsfilter",  "vcaps"),
            ("videoconvert","vconv"),
            ("x264enc",     "venc"),
            ("h264parse",   "vparse"),
            ("rtph264pay",  "vpay"),
            ("queue",       "vqueue"),
        ]
        elems = self._make_and_add(pipeline, elements)

        elems["mxlsrc-video"].set_property("video-flow-id", flow_uuid)
        elems["mxlsrc-video"].set_property("domain", domain)

        caps = Gst.Caps.from_string("video/x-raw,format=v210")
        elems["vcaps"].set_property("caps", caps)

        enc = elems["venc"]
        enc.set_property("tune", 4)           # zerolatency
        enc.set_property("speed-preset", 2)   # ultrafast
        enc.set_property("key-int-max", 30)

        pay = elems["vpay"]
        pay.set_property("config-interval", -1)
        pay.set_property("pt", 96)

        self._link_elements(elems, ["mxlsrc-video", "vcaps", "vconv", "venc", "vparse", "vpay", "vqueue"])

        # Request a video pad on webrtcbin
        trans_caps = Gst.Caps.from_string("application/x-rtp,media=video,encoding-name=H264,payload=96")
        src_pad = elems["vqueue"].get_static_pad("src")
        sink_pad = webrtcbin.request_pad_simple("sink_%u")
        if not sink_pad:
            raise RuntimeError("Could not request video sink pad from webrtcbin")
        if src_pad.link(sink_pad) != Gst.PadLinkReturn.OK:
            raise RuntimeError("Failed to link video queue to webrtcbin")

    def _add_audio_branch(
        self,
        pipeline: Gst.Pipeline,
        webrtcbin: Gst.Element,
        flow_uuid: str,
        domain: str,
    ) -> None:
        elements = [
            ("mxlsrc",        "mxlsrc-audio"),
            ("capsfilter",    "acaps"),
            ("audioconvert",  "aconv"),
            ("audioresample", "aresample"),
            ("opusenc",       "aenc"),
            ("rtpopuspay",    "apay"),
            ("queue",         "aqueue"),
        ]
        elems = self._make_and_add(pipeline, elements)

        elems["mxlsrc-audio"].set_property("audio-flow-id", flow_uuid)
        elems["mxlsrc-audio"].set_property("domain", domain)

        caps = Gst.Caps.from_string("audio/x-raw,format=F32LE,layout=interleaved")
        elems["acaps"].set_property("caps", caps)

        elems["apay"].set_property("pt", 97)

        self._link_elements(elems, ["mxlsrc-audio", "acaps", "aconv", "aresample", "aenc", "apay", "aqueue"])

        src_pad = elems["aqueue"].get_static_pad("src")
        sink_pad = webrtcbin.request_pad_simple("sink_%u")
        if not sink_pad:
            raise RuntimeError("Could not request audio sink pad from webrtcbin")
        if src_pad.link(sink_pad) != Gst.PadLinkReturn.OK:
            raise RuntimeError("Failed to link audio queue to webrtcbin")

    @staticmethod
    def _make_and_add(pipeline: Gst.Pipeline, elements: list[tuple[str, str]]) -> dict[str, Gst.Element]:
        result: dict[str, Gst.Element] = {}
        for factory, name in elements:
            elem = Gst.ElementFactory.make(factory, name)
            if not elem:
                raise RuntimeError(f"Could not create GStreamer element '{factory}' (name='{name}')")
            pipeline.add(elem)
            result[name] = elem
        return result

    @staticmethod
    def _link_elements(elems: dict[str, Gst.Element], order: list[str]) -> None:
        for i in range(len(order) - 1):
            src = elems[order[i]]
            dst = elems[order[i + 1]]
            if not src.link(dst):
                raise RuntimeError(f"Failed to link {order[i]} → {order[i + 1]}")

    # ── WHIP handshake ────────────────────────────────────────────────────────

    def _on_negotiation_needed(self, webrtcbin: Gst.Element) -> None:
        log.info("Negotiation needed — creating offer")
        try:
            # Use a lambda closure so no user_data argument is needed
            promise = Gst.Promise.new_with_change_func(
                lambda p: self._on_offer_created(p, webrtcbin)
            )
            webrtcbin.emit("create-offer", None, promise)
        except Exception as exc:
            log.error("create-offer failed: %s", exc, exc_info=True)

    def _on_offer_created(self, promise: Gst.Promise, webrtcbin: Gst.Element) -> None:
        log.info("Offer created — setting local description")
        try:
            reply = promise.get_reply()
            if reply is None:
                log.error("Promise reply is None")
                return
            offer = reply.get_value("offer")
            if offer is None:
                log.error("Offer is None in reply structure")
                return
            # Don't call wait() here — it can block inside a GLib promise callback
            webrtcbin.emit("set-local-description", offer, Gst.Promise.new())
            log.info("Local description set — waiting for ICE gathering (max %.0fs)", _ICE_TIMEOUT)
            threading.Thread(target=self._wait_ice_then_whip, args=(webrtcbin,), daemon=True).start()
        except Exception as exc:
            log.error("_on_offer_created failed: %s", exc, exc_info=True)

    def _wait_ice_then_whip(self, webrtcbin: Gst.Element) -> None:
        import time
        interval = 0.1
        elapsed = 0.0
        while elapsed < _ICE_TIMEOUT:
            try:
                state = webrtcbin.get_property("ice-gathering-state")
                if state == GstWebRTC.WebRTCICEGatheringState.COMPLETE:
                    log.info("ICE gathering complete after %.1fs", elapsed)
                    break
            except Exception as exc:
                log.warning("Error reading ICE state: %s", exc)
                break
            time.sleep(interval)
            elapsed += interval
        else:
            log.warning("ICE gathering did not complete in %.0fs, sending offer anyway", _ICE_TIMEOUT)

        # Re-read local description after trickle ICE (all candidates inlined)
        try:
            local_desc = webrtcbin.get_property("local-description")
            if local_desc is None:
                log.error("local-description is None after ICE gathering — cannot send WHIP offer")
                return
            sdp_text = local_desc.sdp.as_text()
            log.info("Sending WHIP offer (%d bytes) to %s", len(sdp_text), MEDIAMTX_WHIP)
            self._do_whip(webrtcbin, sdp_text)
        except Exception as exc:
            log.error("Failed to read local-description: %s", exc, exc_info=True)

    def _do_whip(self, webrtcbin: Gst.Element, sdp_offer: str) -> None:
        whip_url = MEDIAMTX_WHIP
        log.info("POSTing SDP offer to %s", whip_url)
        try:
            req = urllib.request.Request(
                whip_url,
                data=sdp_offer.encode(),
                method="POST",
                headers={"Content-Type": "application/sdp"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status not in (200, 201):
                    raise RuntimeError(f"WHIP endpoint returned HTTP {resp.status}")
                answer_sdp = resp.read().decode()

        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            msg = f"WHIP HTTP {exc.code}: {body[:200]}"
            log.error(msg)
            with self._lock:
                self._error_msg = msg
                self._running = False
            return
        except Exception as exc:
            msg = f"WHIP error: {exc}"
            log.error(msg)
            with self._lock:
                self._error_msg = msg
                self._running = False
            return

        log.info("WHIP handshake complete — applying remote SDP answer")
        _, sdp_msg = GstSdp.SDPMessage.new_from_text(answer_sdp)
        answer = GstWebRTC.WebRTCSessionDescription.new(
            GstWebRTC.WebRTCSDPType.ANSWER, sdp_msg
        )
        promise = Gst.Promise.new()
        webrtcbin.emit("set-remote-description", answer, promise)
        promise.wait()
        log.info("Remote description set — streaming to MediaMTX")

    def _on_ice_candidate(self, _webrtcbin: Gst.Element, mline_index: int, candidate: str) -> None:
        # trickle ICE not used; MediaMTX accepts the full offer with gathered candidates
        log.debug("ICE candidate [%d]: %s", mline_index, candidate)

    # ── Teardown ──────────────────────────────────────────────────────────────

    def _teardown(self) -> None:
        if self._pipeline is None:
            return
        log.info("Tearing down pipeline…")
        self._pipeline.set_state(Gst.State.NULL)
        self._pipeline.get_state(5 * Gst.SECOND)
        self._pipeline = None
        self._webrtcbin = None
        self._running = False
        gc.collect()
        log.info("Teardown complete")

    # ── GStreamer bus callbacks ────────────────────────────────────────────────

    def _on_error(self, _bus: Gst.Bus, msg: Gst.Message) -> None:
        err, debug = msg.parse_error()
        log.error("GStreamer error: %s  debug: %s", err, debug)
        with self._lock:
            self._error_msg = str(err)
            self._running = False

    def _on_eos(self, _bus: Gst.Bus, _msg: Gst.Message) -> None:
        log.info("End of stream")
        with self._lock:
            self._running = False

    def _on_state_changed(self, _bus: Gst.Bus, msg: Gst.Message) -> None:
        if msg.src != self._pipeline:
            return
        _old, new, _pending = msg.parse_state_changed()
        if new == Gst.State.PLAYING:
            with self._lock:
                self._running = True
        elif new in (Gst.State.NULL, Gst.State.READY):
            with self._lock:
                if self._running:
                    self._running = False
