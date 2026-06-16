# SPDX-FileCopyrightText: 2025 Contributors to the Media eXchange Layer project.
# SPDX-License-Identifier: Apache-2.0
"""
GStreamer MXL-to-WebRTC pipeline.

Reads one MXL video flow and/or one MXL audio flow via mxlsrc, encodes them,
and delivers them to the browser over WebRTC. Two delivery modes are supported,
selected per pipeline start:

  MediaMTX relay (use_mediamtx=True):
    mxlsrc → encode → webrtcbin --(WHIP push)--> MediaMTX --(WHEP)--> browser
    Mature/compatible everywhere, but cannot carry x264 intra-refresh streams
    (MediaMTX needs IDR keyframes to bootstrap a newly-joining reader).

  Direct (use_mediamtx=False):
    browser <--(WebRTC)--> FastAPI / webrtcbin (this process) ← encode ← mxlsrc
    The backend is its own signalling + media server. Each viewer gets its OWN
    complete pipeline (mxlsrc → encode → webrtcbin) that goes NULL→PLAYING as a
    unit — the same lifecycle as MediaMTX mode, which initialises webrtcbin's ICE
    correctly (adding a webrtcbin to an already-running pipeline does not).
    Signalling is "server-offers": webrtcbin CREATES the offer (the role that
    reliably negotiates H264) and the browser answers — POST /whep returns the
    offer, POST /whep/{id} carries the browser's answer. (A webrtcbin acting as a
    sendonly answerer mis-negotiates: it answers a=inactive / the wrong codec.)
    The browser↔webrtcbin PLI loop is intact, so intra-refresh works and there is
    no relay hop. ICE candidates are made reachable with a pinned UDP port range
    (WEBRTC_ICE_PORT_MIN/MAX, published 1:1 in Docker) whose host candidate address
    is rewritten per request to the host the browser connected to — so a single
    bridged compose works for both local and remote viewers, on any platform.
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

MEDIAMTX_WHIP = os.environ.get("MEDIAMTX_WHIP_URL", "http://localhost:8889/mxl2webrtc/whip")

# ICE gathering timeout in seconds before sending/returning the SDP anyway
_ICE_TIMEOUT = 10.0


def _env_int(name: str) -> int | None:
    val = os.environ.get(name, "").strip()
    try:
        return int(val) if val else None
    except ValueError:
        return None


# Direct-mode ICE reachability (ignored in MediaMTX mode):
#   WEBRTC_ICE_PORT_MIN/MAX – if set, webrtcbin's ICE UDP ports are pinned to this range
#                             so they can be published 1:1 in Docker. Each viewer uses one
#                             port (rtcp-mux + max-bundle), so the range width = max viewers.
# The host ICE candidate address is rewritten per request to the host the browser actually
# connected to (the WHEP request's Host header) — so the SAME bridged compose works whether
# the viewer is local (localhost / Docker Desktop) or on another machine (the host's LAN IP),
# with no per-environment config.
WEBRTC_ICE_PORT_MIN = _env_int("WEBRTC_ICE_PORT_MIN")
WEBRTC_ICE_PORT_MAX = _env_int("WEBRTC_ICE_PORT_MAX")

# Default x264enc properties applied to the video branch. The UI may override
# any of these per pipeline start via the /pipeline/start payload.
#   tune          – x264enc tune flags (4 = zerolatency)
#   speed_preset  – x264enc speed-preset enum (1=ultrafast, 2=superfast, 3=veryfast …)
#   bitrate       – target bitrate in kbit/sec
#   key_int_max   – maximum interval between keyframes (in frames)
#   intra_refresh – use Periodic Intra Refresh instead of IDR keyframes. Only works in
#                   Direct mode (not through the MediaMTX relay); driven automatically by
#                   the delivery mode. Removes the per-GOP keyframe bitrate spike, so it is
#                   the low-latency stutter fix when MediaMTX is bypassed.
DEFAULT_ENCODER_SETTINGS = {
    "tune":          4,
    "speed_preset":  2,
    "bitrate":       10000,
    "key_int_max":   30,
    "intra_refresh": False,
}


# ── SCTE-104 / ST 2038 ANC parsing (consumer side) ──────────────────────────────
#
# The ancillary flow carries caption ANC (DID 0x61) every frame plus a SCTE-104 ANC
# packet (DID 0x41 / SDID 0x07) on triggered frames. We can't treat "non-empty grain"
# as a trigger anymore, so we parse the ANC packets and look for the SCTE-104 one,
# then decode the SCTE-104 multiple_operation_message inside it.

class _BitReader:
    """MSB-first bit reader over a bytes buffer."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0  # bit position

    def read(self, bits: int) -> int:
        v = 0
        for _ in range(bits):
            byte = self._data[self._pos >> 3]
            v = (v << 1) | ((byte >> (7 - (self._pos & 7))) & 1)
            self._pos += 1
        return v

    def align_to_byte(self) -> None:
        if self._pos & 7:
            self._pos = (self._pos + 7) & ~7

    def remaining_bits(self) -> int:
        return len(self._data) * 8 - self._pos


def parse_st2038_packets(data: bytes) -> list[tuple[int, int, list[int]]]:
    """Return (did, sdid, [udw]) for each ST 2038 ANC packet in a grain buffer
    (meta/x-st-2038 = concatenated ST 2038 ANC packets). did/sdid/udw are the low
    8 bits of their 10-bit fields. Layout per data.rs::st2038_anc_packet_from_ancillary_meta."""
    r = _BitReader(data)
    packets: list[tuple[int, int, list[int]]] = []
    # Smallest packet (data_count=0): 6+1+11+12+10+10+10+10(checksum) = 70 bits.
    while r.remaining_bits() >= 70:
        try:
            if r.read(6) != 0:
                break               # not a valid ST 2038 packet start
            r.read(1)               # C (channel)
            r.read(11)              # line
            r.read(12)              # offset
            did = r.read(10)
            sdid = r.read(10)
            dc8 = r.read(10) & 0xFF  # data_count (low 8 bits = UDW count)
            udw = [r.read(10) & 0xFF for _ in range(dc8)]
            r.read(10)              # checksum
            r.align_to_byte()       # ST 2038 pads to a byte boundary with 1-bits
            packets.append((did & 0xFF, sdid & 0xFF, udw))
        except IndexError:
            break
    return packets


def parse_scte104(msg: bytes) -> dict | None:
    """Parse a SCTE-104 multiple_operation_message; return the first op's fields."""
    if len(msg) < 14:
        return None
    i = 4          # skip reserved(2) + messageSize(2)
    i += 1         # protocol_version
    i += 1         # AS_index
    i += 1         # message_number
    i += 2         # DPI_PID_index
    i += 1         # SCTE35_protocol_version
    time_type = msg[i]; i += 1
    if time_type != 0:   # we only ever send time_type=0 (no timestamp bytes)
        return None
    i += 1         # num_ops
    if i + 4 > len(msg):
        return None
    op_id = (msg[i] << 8) | msg[i + 1]; i += 2
    i += 2         # data_length
    out: dict = {"opID": op_id}
    if op_id == 0x0101 and i + 5 <= len(msg):  # splice_request_data
        out["splice_insert_type"] = msg[i]
        out["splice_event_id"] = int.from_bytes(msg[i + 1:i + 5], "big")
    return out


def find_scte104(grain: bytes) -> dict | None:
    """Scan a grain's ANC packets for a SCTE-104 packet (DID 0x41 / SDID 0x07) and
    return its parsed message, or None if there isn't one this frame."""
    for did, sdid, udw in parse_st2038_packets(grain):
        if did == 0x41 and sdid == 0x07:
            return parse_scte104(bytes(udw))
    return None


class GstReceiver:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pipeline: Gst.Pipeline | None = None
        self._webrtcbin: Gst.Element | None = None      # MediaMTX mode: single publisher
        self._sessions: dict[str, dict] = {}            # Direct mode: per-viewer pipelines by id
        self._running = False
        self._use_mediamtx = True
        self._error_msg: str | None = None
        self._video_flow_uuid: str | None = None
        self._audio_flow_uuid: str | None = None
        self._domain_path: str | None = None
        self._enc_settings: dict = dict(DEFAULT_ENCODER_SETTINGS)

        # Ancillary data (captions / SCTE) — decoded by a side pipeline shared by
        # all viewers, independent of the WebRTC delivery mode.
        self._ancillary_flow_uuid: str | None = None   # one flow carries captions + SCTE
        self._data_pipelines: list[Gst.Pipeline] = []
        self._data_error: str | None = None
        self._caption_text: str = ""
        self._caption_ts: float | None = None     # epoch seconds of last caption
        self._scte_ts: float | None = None         # epoch seconds of last SCTE trigger
        self._scte_count: int = 0
        self._scte_event_id: int | None = None     # last decoded SCTE-104 splice_event_id
        self._scte_splice_type: int | None = None  # last decoded splice_insert_type

        self._glib_loop = GLib.MainLoop()
        threading.Thread(target=self._glib_loop.run, daemon=True).start()

    # ── Public API ────────────────────────────────────────────────────────────

    def start(
        self,
        domain_path: str,
        video_flow_uuid: str | None,
        audio_flow_uuid: str | None,
        enc_settings: dict | None = None,
        use_mediamtx: bool = True,
        ancillary_flow_uuid: str | None = None,
    ) -> None:
        with self._lock:
            self._teardown()
            self._domain_path = domain_path
            self._video_flow_uuid = video_flow_uuid
            self._audio_flow_uuid = audio_flow_uuid
            self._ancillary_flow_uuid = ancillary_flow_uuid
            self._use_mediamtx = use_mediamtx
            # Merge caller overrides over the defaults so a partial dict still works.
            self._enc_settings = {**DEFAULT_ENCODER_SETTINGS, **(enc_settings or {})}
            self._error_msg = None
            self._caption_text = ""
            self._caption_ts = None
            self._scte_ts = None
            self._scte_count = 0
            self._scte_event_id = None
            self._scte_splice_type = None
            self._data_error = None
            try:
                if use_mediamtx:
                    self._build_pipeline_mediamtx()
                else:
                    self._arm_direct_mode()
            except Exception as exc:
                log.error("Failed to build pipeline: %s", exc)
                self._error_msg = str(exc)
                self._running = False
                raise
            # The ancillary (captions + SCTE) decode runs in its own pipeline so a
            # missing caption element can't take down the A/V stream. Errors are
            # recorded in _data_error and surfaced via /data-status.
            if ancillary_flow_uuid:
                self._build_data_pipelines()

    def stop(self) -> None:
        with self._lock:
            self._teardown()
            self._video_flow_uuid = None
            self._audio_flow_uuid = None
            self._ancillary_flow_uuid = None
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
                "running":             self._running,
                "video_flow_uuid":     v,
                "audio_flow_uuid":     a,
                "ancillary_flow_uuid": self._ancillary_flow_uuid,
                "mode":                mode if self._running else "stopped",
                "use_mediamtx":        self._use_mediamtx,
                "viewers":             len(self._sessions),
                "error":               self._error_msg,
                "encoder":             dict(self._enc_settings),
            }

    def get_data_status(self) -> dict:
        """Latest decoded caption + most recent SCTE trigger, for fast UI polling."""
        with self._lock:
            return {
                "caption":             self._caption_text,
                "caption_ts":          self._caption_ts,
                "scte_ts":             self._scte_ts,
                "scte_count":          self._scte_count,
                "scte_event_id":       self._scte_event_id,
                "scte_splice_type":    self._scte_splice_type,
                "ancillary_flow_uuid": self._ancillary_flow_uuid,
                "error":               self._data_error,
            }

    # ── Shared encode helpers ──────────────────────────────────────────────────

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

    def _apply_encoder_settings(self, enc: Gst.Element) -> None:
        s = self._enc_settings
        enc.set_property("tune", s["tune"])                 # 4 = zerolatency
        enc.set_property("speed-preset", s["speed_preset"]) # 1 ultrafast, 2 superfast, 3 veryfast
        enc.set_property("bitrate", s["bitrate"])           # kbit/sec
        enc.set_property("key-int-max", s["key_int_max"])
        enc.set_property("intra-refresh", bool(s["intra_refresh"]))

    def _connect_bus(self, pipeline: Gst.Pipeline) -> None:
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_error)
        bus.connect("message::eos", self._on_eos)
        bus.connect("message::state-changed", self._on_state_changed)

    def _start_playing(self, pipeline: Gst.Pipeline) -> None:
        ret = pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            pipeline.set_state(Gst.State.NULL)
            raise RuntimeError("Pipeline failed to reach PLAYING state")

    # ── MediaMTX mode pipeline (webrtcbin → WHIP push) ─────────────────────────

    def _build_pipeline_mediamtx(self) -> None:
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

        self._connect_bus(pipeline)
        webrtcbin.connect("on-negotiation-needed", self._on_negotiation_needed)
        webrtcbin.connect("on-ice-candidate", self._on_ice_candidate)

        self._pipeline = pipeline
        self._webrtcbin = webrtcbin

        self._start_playing(pipeline)
        self._running = True
        mode = "video+audio" if v and a else "video" if v else "audio"
        log.info("Pipeline started (MediaMTX relay, mode: %s)", mode)

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

        self._apply_encoder_settings(elems["venc"])

        pay = elems["vpay"]
        pay.set_property("config-interval", -1)
        pay.set_property("pt", 96)

        self._link_elements(elems, ["mxlsrc-video", "vcaps", "vconv", "venc", "vparse", "vpay", "vqueue"])

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
            ("audioconvert",  "aconv"),
            ("audioresample", "aresample"),
            ("capsfilter",    "acaps"),
            ("opusenc",       "aenc"),
            ("rtpopuspay",    "apay"),
            ("queue",         "aqueue"),
        ]
        elems = self._make_and_add(pipeline, elements)

        elems["mxlsrc-audio"].set_property("audio-flow-id", flow_uuid)
        elems["mxlsrc-audio"].set_property("domain", domain)

        caps = Gst.Caps.from_string("audio/x-raw,layout=interleaved,channels=2")
        elems["acaps"].set_property("caps", caps)

        elems["apay"].set_property("pt", 97)

        self._link_elements(elems, ["mxlsrc-audio", "aconv", "aresample", "acaps", "aenc", "apay", "aqueue"])

        src_pad = elems["aqueue"].get_static_pad("src")
        sink_pad = webrtcbin.request_pad_simple("sink_%u")
        if not sink_pad:
            raise RuntimeError("Could not request audio sink pad from webrtcbin")
        if src_pad.link(sink_pad) != Gst.PadLinkReturn.OK:
            raise RuntimeError("Failed to link audio queue to webrtcbin")

    # ── Ancillary-data decode pipeline (captions + SCTE) ───────────────────────

    def _read_flow_framerate(self, flow_uuid: str) -> str:
        """Return the flow's grain rate as 'num/den' from flow_def.json. mxlsrc
        requires a downstream framerate to negotiate meta/x-st-2038 caps; the rate
        also paces CEA-608 decode. Defaults to 30000/1001 if it can't be read."""
        default = "30000/1001"
        if not self._domain_path:
            return default
        path = os.path.join(self._domain_path, f"{flow_uuid}.mxl-flow", "flow_def.json")
        try:
            with open(path) as f:
                data = json.load(f)
        except Exception:
            return default

        def find_rate(obj):
            if isinstance(obj, dict):
                r = obj.get("grain_rate")
                if isinstance(r, dict) and r.get("numerator") and r.get("denominator"):
                    return f"{r['numerator']}/{r['denominator']}"
                for v in obj.values():
                    got = find_rate(v)
                    if got:
                        return got
            return None

        return find_rate(data) or default

    def _build_data_pipelines(self) -> None:
        """Decode the single 'Ancillary Data' flow (captions + SCTE muxed). One
        pipeline tees the ST 2038 grains to a CEA-608 caption decoder and to a raw
        appsink that scans the ANC packets for SCTE-104. Assembled with
        Gst.parse_launch (delayed pad linking — st2038anctocc/ccconverter negotiate
        caps dynamically and can't be joined with a static Element.link())."""
        self._data_pipelines = []
        self._data_error = None
        if not self._ancillary_flow_uuid:
            return
        try:
            p = self._build_ancillary_pipeline(self._ancillary_flow_uuid)
            self._start_data_pipeline(p)
            self._data_pipelines.append(p)
            log.info("Ancillary decode pipeline started")
        except Exception as exc:
            log.warning("Ancillary decode pipeline failed: %s", exc)
            self._data_error = f"ancillary: {exc}"

    def _start_data_pipeline(self, pipeline: Gst.Pipeline) -> None:
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_data_error)
        ret = pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            pipeline.set_state(Gst.State.NULL)
            raise RuntimeError(f"{pipeline.get_name()} failed to reach PLAYING")

    def _build_ancillary_pipeline(self, flow_uuid: str) -> Gst.Pipeline:
        rate = self._read_flow_framerate(flow_uuid)
        # ONE linear caption pipeline (no tee — a tee with a second branch stalled
        # cea608tott and blocked all decode). SCTE-104 is parsed from a pad probe on
        # the raw ST 2038 grain at the capsfilter src, so there's no second branch.
        desc = (
            f"mxlsrc data-flow-id={flow_uuid} domain={self._domain_path} "
            f"! capsfilter name=anccaps caps=meta/x-st-2038,alignment=frame,framerate={rate} "
            f"! st2038anctocc ! ccconverter ! closedcaption/x-cea-608 "
            f"! cea608tott ! text/x-raw,format=utf8 "
            f"! appsink name=ccsink emit-signals=true sync=false max-buffers=1 drop=true"
        )
        pipeline = Gst.parse_launch(desc)
        pipeline.get_by_name("ccsink").connect("new-sample", self._on_caption_sample)
        # SCTE-104 detection: probe each raw grain before the caption decoder.
        anccaps = pipeline.get_by_name("anccaps")
        anccaps.get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, self._scte_grain_probe)
        return pipeline

    def _on_caption_sample(self, appsink: Gst.Element) -> Gst.FlowReturn:
        sample = appsink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if ok:
            try:
                text = bytes(info.data).decode("utf-8", "replace").strip()
            finally:
                buf.unmap(info)
            if text:  # ignore the blank keep-alive rows the producer sends
                with self._lock:
                    self._caption_text = text
                    self._caption_ts = time.time()
        return Gst.FlowReturn.OK

    def _scte_grain_probe(self, _pad: Gst.Pad, info: Gst.PadProbeInfo) -> Gst.PadProbeReturn:
        """Pad probe on the raw ST 2038 grain: parse each grain's ANC packets and act
        on a SCTE-104 packet (DID 0x41 / SDID 0x07). The muxed flow carries caption
        ANC every frame, so we can't treat 'non-empty grain' as a trigger — we must
        parse. Debounced (the marker rides a single grain)."""
        buf = info.get_buffer()
        if buf is None or buf.get_size() == 0:
            return Gst.PadProbeReturn.OK
        ok, minfo = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.PadProbeReturn.OK
        try:
            scte = find_scte104(bytes(minfo.data))
        finally:
            buf.unmap(minfo)
        if scte is not None:
            now = time.time()
            with self._lock:
                if self._scte_ts is None or now - self._scte_ts > 1.0:
                    self._scte_ts = now
                    self._scte_count += 1
                    self._scte_event_id = scte.get("splice_event_id")
                    self._scte_splice_type = scte.get("splice_insert_type")
                    log.info("SCTE-104 received: opID=0x%04x event_id=%s type=%s",
                             scte.get("opID", 0), self._scte_event_id, self._scte_splice_type)
        return Gst.PadProbeReturn.OK

    def _on_data_error(self, _bus: Gst.Bus, msg: Gst.Message) -> None:
        err, debug = msg.parse_error()
        log.error("Ancillary-data pipeline error: %s  debug: %s", err, debug)

    # ── Direct mode (one full mxlsrc→encode→webrtcbin pipeline per WHEP viewer) ─

    def _arm_direct_mode(self) -> None:
        # No shared pipeline: each WHEP viewer gets its own complete pipeline, built
        # on demand in add_whep_session(). Each goes NULL→PLAYING as a unit (the same
        # lifecycle as MediaMTX mode), which initialises webrtcbin's ICE correctly —
        # unlike adding a webrtcbin to an already-running pipeline.
        v = self._video_flow_uuid
        a = self._audio_flow_uuid
        self._sessions = {}
        self._running = True
        mode = "video+audio" if v and a else "video" if v else "audio"
        log.info("Direct WHEP mode armed (mode: %s) — a pipeline is built per viewer", mode)

    # ── Direct mode WHEP signalling (called from the FastAPI thread) ───────────

    def create_session_offer(self, public_host: str = "") -> tuple[str, str]:
        """Build a complete per-viewer pipeline and have webrtcbin CREATE THE OFFER
        (server-offers signalling). webrtcbin as offerer reliably negotiates H264 (the
        same role as MediaMTX mode); the browser then answers — browsers are robust
        answerers — which avoids webrtcbin's sendonly-answerer failures (a=inactive /
        wrong codec). `public_host` is the host the browser connected to (from the WHEP
        request) and is substituted into the offer's ICE host candidates so the address is
        reachable for both local and remote viewers. Returns (session_id, offer_sdp)."""
        with self._lock:
            if not self._running or self._use_mediamtx:
                raise RuntimeError("Direct WHEP is only available while a Direct-mode pipeline is running")
            sid = uuid.uuid4().hex
            v = self._video_flow_uuid
            a = self._audio_flow_uuid
            domain = self._domain_path

            pipeline = Gst.Pipeline.new(f"whep-{sid}")
            wb = Gst.ElementFactory.make("webrtcbin", f"wb-{sid}")
            if not wb:
                raise RuntimeError("Could not create webrtcbin")
            wb.set_property("bundle-policy", GstWebRTC.WebRTCBundlePolicy.MAX_BUNDLE)
            wb.set_property("stun-server", "")
            pipeline.add(wb)
            if v:
                self._add_video_branch(pipeline, wb, v, domain)
            if a:
                self._add_audio_branch(pipeline, wb, a, domain)

            bus = pipeline.get_bus()
            bus.add_signal_watch()
            bus.connect("message::error", self._on_session_error)

            session = {
                "pipeline": pipeline, "webrtcbin": wb, "ice": None, "public_host": public_host,
                "offer_event": threading.Event(), "offer_sdp": None, "offer_err": None,
            }
            # Let webrtcbin tell us when to negotiate — by then the media caps are ready.
            # Calling create-offer manually before caps negotiate yields an empty (no
            # m-line) offer. This mirrors the working MediaMTX-mode offerer path.
            wb.connect("on-negotiation-needed", lambda el: self._session_on_negotiation_needed(el, session))
            self._sessions[sid] = session

        try:
            ret = pipeline.set_state(Gst.State.PLAYING)
            if ret == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError("Viewer pipeline failed to reach PLAYING")
            if not session["offer_event"].wait(timeout=15):
                raise RuntimeError("Timed out building the WebRTC offer")
            if session["offer_err"]:
                raise RuntimeError(session["offer_err"])
        except Exception as exc:
            log.error("WHEP offer creation failed for %s: %s", sid, exc, exc_info=True)
            self.remove_whep_session(sid)
            raise
        log.info("WHEP viewer %s offer created (%d total)", sid, len(self._sessions))
        return sid, session["offer_sdp"]

    def apply_session_answer(self, sid: str, answer_sdp: str) -> None:
        """Apply the browser's SDP answer to a session's webrtcbin (step 2 of the
        server-offers exchange). After this, media flows to the browser."""
        with self._lock:
            session = self._sessions.get(sid)
        if session is None:
            raise RuntimeError(f"Unknown WHEP session {sid}")
        wb = session["webrtcbin"]
        res, sdpmsg = GstSdp.SDPMessage.new_from_text(answer_sdp)
        if res != GstSdp.SDPResult.OK or sdpmsg is None:
            raise RuntimeError("Could not parse WHEP answer SDP")
        answer = GstWebRTC.WebRTCSessionDescription.new(GstWebRTC.WebRTCSDPType.ANSWER, sdpmsg)
        p = Gst.Promise.new()
        wb.emit("set-remote-description", answer, p)
        p.wait()
        log.info("WHEP viewer %s answer applied — streaming", sid)

    def _on_session_error(self, _bus: Gst.Bus, msg: Gst.Message) -> None:
        err, debug = msg.parse_error()
        log.error("WHEP viewer pipeline error: %s  debug: %s", err, debug)

    def _session_on_negotiation_needed(self, wb: Gst.Element, session: dict) -> None:
        # Fired by webrtcbin once media caps are ready. Pin ICE ports here (the agent
        # exists and gathering hasn't started yet), then create the offer.
        try:
            session["ice"] = self._configure_ice_ports(wb)
            promise = Gst.Promise.new_with_change_func(
                lambda p: self._session_on_offer_created(p, wb, session)
            )
            wb.emit("create-offer", None, promise)
        except Exception as exc:
            log.error("session create-offer failed: %s", exc, exc_info=True)
            session["offer_err"] = str(exc)
            session["offer_event"].set()

    def _session_on_offer_created(self, promise: Gst.Promise, wb: Gst.Element, session: dict) -> None:
        try:
            reply = promise.get_reply()
            offer = reply.get_value("offer") if reply is not None else None
            if offer is None:
                raise RuntimeError("create-offer produced no offer")
            # Don't wait() inside a GLib callback; do the ICE wait on a worker thread.
            wb.emit("set-local-description", offer, Gst.Promise.new())
            threading.Thread(target=self._session_store_offer, args=(wb, session), daemon=True).start()
        except Exception as exc:
            log.error("session offer-created failed: %s", exc, exc_info=True)
            session["offer_err"] = str(exc)
            session["offer_event"].set()

    def _session_store_offer(self, wb: Gst.Element, session: dict) -> None:
        try:
            elapsed = 0.0
            while elapsed < _ICE_TIMEOUT:
                if wb.get_property("ice-gathering-state") == GstWebRTC.WebRTCICEGatheringState.COMPLETE:
                    break
                time.sleep(0.1)
                elapsed += 0.1
            local = wb.get_property("local-description")
            if local is None:
                raise RuntimeError("local-description is None after ICE gathering")
            session["offer_sdp"] = self._rewrite_candidates(local.sdp.as_text(), session.get("public_host", ""))
            log.info("WHEP offer SDP:\n%s", session["offer_sdp"])
        except Exception as exc:
            log.error("session store-offer failed: %s", exc, exc_info=True)
            session["offer_err"] = str(exc)
        finally:
            session["offer_event"].set()

    @staticmethod
    def _rewrite_candidates(sdp: str, host: str) -> str:
        """Rewrite the `typ host` ICE candidate addresses (and the c= line) to `host` — the
        address the browser connected to (from the WHEP request's Host header) — so the
        candidate is reachable whether the viewer is local or on another machine. No-op when
        host is empty."""
        if not host:
            return sdp
        out = []
        for line in sdp.splitlines():
            if line.startswith("a=candidate:"):
                f = line.split()
                # candidate:<foundation> <comp> <proto> <prio> <addr> <port> typ <type> ...
                if len(f) > 7 and f[6] == "typ" and f[7] == "host":
                    f[4] = host
                    line = " ".join(f)
            elif line.startswith("c=IN IP4 "):
                line = "c=IN IP4 " + host
            out.append(line)
        return "\r\n".join(out) + "\r\n"

    @staticmethod
    def _configure_ice_ports(wb: Gst.Element):
        """Pin webrtcbin's ICE UDP ports to the published range. Returns the ICE agent
        object so the caller can keep a reference for the session lifetime (dropping the
        Python wrapper mid-negotiation can over-unref and invalidate webrtcbin's ICE)."""
        if WEBRTC_ICE_PORT_MIN is None or WEBRTC_ICE_PORT_MAX is None:
            return None
        try:
            ice = wb.get_property("ice-agent")
            ice.set_property("min-rtp-port", WEBRTC_ICE_PORT_MIN)
            ice.set_property("max-rtp-port", WEBRTC_ICE_PORT_MAX)
            log.info("Pinned ICE UDP ports to %d-%d", WEBRTC_ICE_PORT_MIN, WEBRTC_ICE_PORT_MAX)
            return ice
        except Exception as exc:
            log.warning("Could not pin ICE port range (%s) — Direct mode may be unreachable when bridged", exc)
            return None

    def remove_whep_session(self, sid: str) -> None:
        with self._lock:
            session = self._sessions.pop(sid, None)
        if session is None:
            return
        self._teardown_session(session)
        log.info("WHEP viewer %s disconnected (%d remaining)", sid, len(self._sessions))

    @staticmethod
    def _teardown_session(session: dict) -> None:
        # Each viewer owns a complete pipeline; tearing it down is just NULL + drop refs.
        pipeline = session.get("pipeline")
        if pipeline is not None:
            pipeline.set_state(Gst.State.NULL)
            pipeline.get_state(5 * Gst.SECOND)
        session["ice"] = None

    # ── WHIP handshake (MediaMTX mode only) ────────────────────────────────────

    def _on_negotiation_needed(self, webrtcbin: Gst.Element) -> None:
        log.info("Negotiation needed — creating offer")
        try:
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
            webrtcbin.emit("set-local-description", offer, Gst.Promise.new())
            log.info("Local description set — waiting for ICE gathering (max %.0fs)", _ICE_TIMEOUT)
            threading.Thread(target=self._wait_ice_then_whip, args=(webrtcbin,), daemon=True).start()
        except Exception as exc:
            log.error("_on_offer_created failed: %s", exc, exc_info=True)

    def _wait_ice_then_whip(self, webrtcbin: Gst.Element) -> None:
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
        # trickle ICE not used; the full offer carries gathered candidates
        log.debug("ICE candidate [%d]: %s", mline_index, candidate)

    # ── Teardown ──────────────────────────────────────────────────────────────

    def _teardown(self) -> None:
        for p in self._data_pipelines:
            p.set_state(Gst.State.NULL)
            p.get_state(5 * Gst.SECOND)
        self._data_pipelines = []
        if self._pipeline is None and not self._sessions:
            self._running = False
            return
        log.info("Tearing down pipeline…")
        for session in list(self._sessions.values()):
            try:
                self._teardown_session(session)
            except Exception as exc:
                log.warning("Error tearing down session: %s", exc)
        self._sessions = {}
        if self._pipeline is not None:
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
