# SPDX-FileCopyrightText: 2025 CBC/Radio-Canada
# SPDX-License-Identifier: Apache-2.0
"""
GStreamer WebRTC-to-MXL pipeline.

Pulls a browser-published Opus stream from MediaMTX over WHEP and writes it into
an MXL audio flow:

    MediaMTX --(WHEP)--> webrtcbin → rtpopusdepay → opusdec → audioconvert
                       → audioresample → capsfilter(F32LE,48k,interleaved)
                       → queue → mxlsink(flow-id, domain)

webrtcbin is used as a WHEP *consumer*: it adds a recvonly Opus transceiver, then
acts as the offerer (create-offer → POST to MediaMTX WHEP → apply the answer).
MediaMTX returns 404 until the browser's WHIP publish is live, so the WHEP POST is
retried with backoff. The incoming media pad appears asynchronously via
`pad-added`, where the decode→mxlsink branch is linked.

Flow UUIDs are deterministic (UUID v5) so restarting with the same flow name
reuses the same flow directory.
"""

from __future__ import annotations

import gc
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
import uuid

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstWebRTC", "1.0")
gi.require_version("GstSdp", "1.0")
from gi.repository import GLib, Gst, GstSdp, GstWebRTC

Gst.init(None)
log = logging.getLogger(__name__)

MEDIAMTX_WHEP = os.environ.get("MEDIAMTX_WHEP_URL",
                               "http://localhost:8889/webrtc2mxl/whep")

# ICE gathering timeout (seconds) before sending the SDP offer anyway.
_ICE_TIMEOUT = 10.0

# WHEP POST retry: MediaMTX 404s until the browser's WHIP publish is live.
_WHEP_RETRIES = 20
_WHEP_RETRY_INTERVAL = 1.0

# Fixed namespace — must never change after deployment; changing it orphans
# previously written flow directories.
_MXL_NS = uuid.UUID("5f3a2b1c-7d6e-4f80-9a1b-2c3d4e5f6a7b")

# Caps mxlsink requires for an audio flow: 32-bit float, interleaved, 48 kHz.
# Channels are intentionally omitted so the flow inherits the source's channel
# count (audioconvert adapts whatever the mic provides).
_MXL_AUDIO_CAPS = "audio/x-raw,format=F32LE,rate=48000,layout=interleaved"

# Opus caps for the recvonly transceiver (payload 111 matches MediaMTX's WHIP).
_OPUS_CAPS = "application/x-rtp,media=audio,encoding-name=OPUS,clock-rate=48000,payload=111"


class GstWriter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pipeline: Gst.Pipeline | None = None
        self._webrtcbin: Gst.Element | None = None
        self._running = False
        self._error_msg: str | None = None
        self._flow_uuid: str | None = None
        self._grouphint: str | None = None
        self._label: str | None = None
        self._description: str | None = None
        self._domain_path: str | None = None
        self._gen = 0  # incremented on each start to cancel stale callbacks

        self._glib_loop = GLib.MainLoop()
        threading.Thread(target=self._glib_loop.run, daemon=True).start()

    # ── Public API ──────────────────────────────────────────────────────────

    def start(self, domain_path: str, grouphint: str, label: str, description: str) -> dict:
        with self._lock:
            self._teardown()
            self._domain_path = domain_path
            self._grouphint = grouphint
            self._label = label
            self._description = description
            # Derive the flow UUID from the group hint (as hls2mxl does) so the
            # same group hint reuses the same flow directory across restarts.
            self._flow_uuid = str(uuid.uuid5(_MXL_NS, f"{grouphint}:audio"))
            self._error_msg = None
            self._gen += 1
            gen = self._gen
            try:
                self._build_pipeline()
            except Exception as exc:
                log.error("Failed to build pipeline: %s", exc, exc_info=True)
                self._error_msg = str(exc)
                self._running = False
                raise
            flow_uuid = self._flow_uuid

        # Patch flow_def.json once mxlsink has created it (background, polls).
        threading.Thread(
            target=self._patch_flow_def,
            args=(domain_path, flow_uuid, grouphint, label, description, gen),
            daemon=True,
        ).start()
        return self.get_status()

    def stop(self) -> None:
        with self._lock:
            self._teardown()
            self._flow_uuid = None
            self._grouphint = None
            self._label = None
            self._description = None
            self._domain_path = None

    def get_status(self) -> dict:
        with self._lock:
            return {
                "running":     self._running,
                "flow_uuid":   self._flow_uuid,
                "grouphint":   self._grouphint,
                "label":       self._label,
                "description": self._description,
                "error":       self._error_msg,
            }

    # ── Pipeline construction ───────────────────────────────────────────────

    def _build_pipeline(self) -> None:
        pipeline = Gst.Pipeline.new("webrtc2mxl")

        webrtcbin = Gst.ElementFactory.make("webrtcbin", "webrtcbin")
        if not webrtcbin:
            raise RuntimeError("Could not create webrtcbin — is gstreamer1.0-plugins-bad installed?")
        webrtcbin.set_property("bundle-policy", GstWebRTC.WebRTCBundlePolicy.MAX_BUNDLE)
        webrtcbin.set_property("stun-server", "")
        pipeline.add(webrtcbin)

        # Add a recvonly Opus transceiver so the offer requests receive-audio.
        # This also triggers on-negotiation-needed.
        caps = Gst.Caps.from_string(_OPUS_CAPS)
        webrtcbin.emit("add-transceiver",
                       GstWebRTC.WebRTCRTPTransceiverDirection.RECVONLY, caps)

        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_error)
        bus.connect("message::eos", self._on_eos)

        webrtcbin.connect("pad-added", self._on_incoming_pad)
        webrtcbin.connect("on-negotiation-needed", self._on_negotiation_needed)

        self._pipeline = pipeline
        self._webrtcbin = webrtcbin

        ret = pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            pipeline.set_state(Gst.State.NULL)
            raise RuntimeError("Pipeline failed to reach PLAYING state")
        self._running = True
        log.info("Pipeline started — pulling WHEP from %s", MEDIAMTX_WHEP)

    def _on_incoming_pad(self, _webrtcbin: Gst.Element, pad: Gst.Pad) -> None:
        """Incoming WebRTC media pad (application/x-rtp) — link the decode→mxlsink
        branch. Built here (not up front) because the pad appears only after the
        remote description is set."""
        try:
            if pad.get_direction() != Gst.PadDirection.SRC:
                return
            caps = pad.get_current_caps() or pad.query_caps(None)
            name = caps.get_structure(0).get_name() if caps else ""
            log.info("webrtcbin pad-added: %s", name)
            if not name.startswith("application/x-rtp"):
                return

            pipeline = self._pipeline
            domain = self._domain_path
            flow_uuid = self._flow_uuid
            if pipeline is None or not domain or not flow_uuid:
                return

            depay  = Gst.ElementFactory.make("rtpopusdepay",  "adepay")
            dec    = Gst.ElementFactory.make("opusdec",       "adec")
            conv   = Gst.ElementFactory.make("audioconvert",  "aconv")
            resamp = Gst.ElementFactory.make("audioresample", "aresample")
            acaps  = Gst.ElementFactory.make("capsfilter",    "acaps")
            queue  = Gst.ElementFactory.make("queue",         "aqueue")
            sink   = Gst.ElementFactory.make("mxlsink",       "asink")
            if not all((depay, dec, conv, resamp, acaps, queue, sink)):
                raise RuntimeError("Could not create the audio decode/mxlsink branch")

            acaps.set_property("caps", Gst.Caps.from_string(_MXL_AUDIO_CAPS))
            sink.set_property("flow-id", flow_uuid)
            sink.set_property("domain", domain)
            sink.set_property("sync", False)

            for el in (depay, dec, conv, resamp, acaps, queue, sink):
                pipeline.add(el)
                el.sync_state_with_parent()

            if not depay.link(dec) or not dec.link(conv) or not conv.link(resamp) \
                    or not resamp.link(acaps) or not acaps.link(queue) or not queue.link(sink):
                raise RuntimeError("Failed to link the audio decode/mxlsink branch")

            if pad.link(depay.get_static_pad("sink")) != Gst.PadLinkReturn.OK:
                raise RuntimeError("Failed to link webrtcbin pad → rtpopusdepay")
            log.info("Linked incoming Opus → mxlsink (flow %s)", flow_uuid)
        except Exception as exc:
            log.error("_on_incoming_pad failed: %s", exc, exc_info=True)
            with self._lock:
                self._error_msg = str(exc)

    # ── WHEP handshake (webrtcbin is the offerer / consumer) ────────────────

    def _on_negotiation_needed(self, webrtcbin: Gst.Element) -> None:
        log.info("Negotiation needed — creating WHEP offer")
        try:
            promise = Gst.Promise.new_with_change_func(
                lambda p: self._on_offer_created(p, webrtcbin)
            )
            webrtcbin.emit("create-offer", None, promise)
        except Exception as exc:
            log.error("create-offer failed: %s", exc, exc_info=True)

    def _on_offer_created(self, promise: Gst.Promise, webrtcbin: Gst.Element) -> None:
        try:
            reply = promise.get_reply()
            offer = reply.get_value("offer") if reply is not None else None
            if offer is None:
                log.error("create-offer produced no offer")
                return
            # Never wait() on a promise inside a GLib callback — do the ICE wait
            # and the blocking WHEP POST on a worker thread.
            webrtcbin.emit("set-local-description", offer, Gst.Promise.new())
            threading.Thread(target=self._wait_ice_then_whep,
                             args=(webrtcbin,), daemon=True).start()
        except Exception as exc:
            log.error("_on_offer_created failed: %s", exc, exc_info=True)

    def _wait_ice_then_whep(self, webrtcbin: Gst.Element) -> None:
        elapsed = 0.0
        while elapsed < _ICE_TIMEOUT:
            try:
                if webrtcbin.get_property("ice-gathering-state") == \
                        GstWebRTC.WebRTCICEGatheringState.COMPLETE:
                    break
            except Exception as exc:
                log.warning("Error reading ICE state: %s", exc)
                break
            time.sleep(0.1)
            elapsed += 0.1
        try:
            local = webrtcbin.get_property("local-description")
            if local is None:
                log.error("local-description is None after ICE gathering")
                return
            self._do_whep(webrtcbin, local.sdp.as_text())
        except Exception as exc:
            log.error("Failed to read local-description: %s", exc, exc_info=True)

    def _do_whep(self, webrtcbin: Gst.Element, sdp_offer: str) -> None:
        """POST the offer to MediaMTX WHEP, retrying while it returns 404 (no
        publisher yet). On success, apply the SDP answer."""
        answer_sdp: str | None = None
        for attempt in range(_WHEP_RETRIES):
            try:
                req = urllib.request.Request(
                    MEDIAMTX_WHEP,
                    data=sdp_offer.encode(),
                    method="POST",
                    headers={"Content-Type": "application/sdp"},
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    if resp.status in (200, 201):
                        answer_sdp = resp.read().decode()
                        break
                    log.warning("WHEP returned HTTP %s (attempt %d)", resp.status, attempt + 1)
            except urllib.error.HTTPError as exc:
                # 404 = MediaMTX path has no publisher yet; keep retrying.
                if exc.code == 404:
                    log.info("WHEP 404 (no publisher yet) — retrying (%d/%d)",
                             attempt + 1, _WHEP_RETRIES)
                else:
                    body = exc.read().decode(errors="replace")
                    log.warning("WHEP HTTP %d: %s", exc.code, body[:200])
            except Exception as exc:
                log.warning("WHEP error (attempt %d): %s", attempt + 1, exc)
            time.sleep(_WHEP_RETRY_INTERVAL)

        if answer_sdp is None:
            msg = f"WHEP handshake failed after {_WHEP_RETRIES} attempts (no publisher?)"
            log.error(msg)
            with self._lock:
                self._error_msg = msg
            return

        log.info("WHEP handshake complete — applying SDP answer")
        _, sdp_msg = GstSdp.SDPMessage.new_from_text(answer_sdp)
        answer = GstWebRTC.WebRTCSessionDescription.new(
            GstWebRTC.WebRTCSDPType.ANSWER, sdp_msg
        )
        promise = Gst.Promise.new()
        webrtcbin.emit("set-remote-description", answer, promise)
        promise.wait()
        log.info("Remote description set — receiving from MediaMTX")

    # ── Background: patch flow_def.json ─────────────────────────────────────

    def _patch_flow_def(self, domain: str, flow_uuid: str, grouphint: str,
                        label: str, description: str, gen: int) -> None:
        """mxlsink creates <domain>/<uuid>.mxl-flow/flow_def.json lazily on the
        first buffer; poll for it, then write the grouphint/label/description."""
        path = os.path.join(domain, f"{flow_uuid}.mxl-flow", "flow_def.json")
        deadline = time.monotonic() + 15
        while not os.path.exists(path):
            if time.monotonic() > deadline:
                log.warning("Timeout waiting for flow_def.json: %s", path)
                return
            with self._lock:
                if self._gen != gen:
                    return
            time.sleep(0.5)
        try:
            with open(path) as f:
                data = json.load(f)
            full_grouphint = f"{grouphint}:Audio"
            data["grouphint"]   = full_grouphint
            data["label"]       = label
            data["description"] = description
            if isinstance(data.get("tags"), dict):
                data["tags"]["urn:x-nmos:tag:grouphint/v1.0"] = [full_grouphint]
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
            log.info("Patched flow_def.json: %s", flow_uuid)
        except Exception as exc:
            log.warning("Could not patch flow_def.json: %s", exc)

    # ── Teardown ────────────────────────────────────────────────────────────

    def _teardown(self) -> None:
        """Caller must hold self._lock."""
        if self._pipeline is None:
            self._running = False
            return
        log.info("Tearing down pipeline…")
        self._pipeline.set_state(Gst.State.NULL)
        self._pipeline.get_state(5 * Gst.SECOND)
        self._pipeline = None
        self._webrtcbin = None
        self._running = False
        gc.collect()
        log.info("Teardown complete")

    # ── Bus callbacks ───────────────────────────────────────────────────────

    def _on_error(self, _bus: Gst.Bus, msg: Gst.Message) -> None:
        err, debug = msg.parse_error()
        log.error("GStreamer error: %s  debug: %s", err, debug)
        with self._lock:
            self._error_msg = str(err)

    def _on_eos(self, _bus: Gst.Bus, _msg: Gst.Message) -> None:
        log.info("End of stream")
        with self._lock:
            self._running = False
