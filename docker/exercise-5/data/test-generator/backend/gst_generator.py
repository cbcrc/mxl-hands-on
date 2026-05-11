"""
GStreamer test-signal generator for the MXL Test Generator.

Runs a live pipeline:
  videotestsrc → timeoverlay → textoverlay → videoconvert → mxlsink (video)
  audiotestsrc                              → audioconvert → mxlsink (audio)

The pipeline is kept running permanently (the generator is always "on").
Changing pattern, timecode or ident just updates element properties on the
fly — no pipeline restart required, so the MXL flow UUIDs stay constant.
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

# ── GStreamer enum values ─────────────────────────────────────────────────────
VIDEO_PATTERNS = {
    "smpte":            0,
    "snow":             1,
    "black":            2,
    "white":            3,
    "red":              4,
    "green":            5,
    "blue":             6,
    "checkers-1":       7,
    "checkers-2":       8,
    "checkers-4":       9,
    "checkers-8":       10,
    "circular":         11,
    "blink":            12,
    "smpte75":          13,
    "zone-plate":       14,
    "gamut":            15,
    "chroma-zone-plate":16,
    "solid-color":      17,
    "ball":             18,
    "smpte100":         19,
    "bar":              20,
    "pinwheel":         21,
    "spokes":           22,
    "gradient":         23,
    "smpte-rp-219":     25,
}

AUDIO_PATTERNS = {
    "sine":           0,
    "square":         1,
    "saw":            2,
    "triangle":       3,
    "silence":        4,
    "white-noise":    5,
    "pink-noise":     6,
    "sine-table":     7,
    "ticks":          8,
    "gaussian-noise": 9,
    "red-noise":      10,
    "blue-noise":     11,
    "violet-noise":   12,
}

DEFAULT_VIDEO_PATTERN = "smpte"
DEFAULT_AUDIO_PATTERN = "sine"
DEFAULT_AUDIO_FREQ    = 1000.0   # 1 kHz
DEFAULT_AUDIO_VOL     = 0.1      # ≈ -20 dBFS  (0 dBFS = 1.0)


class GstGenerator:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pipeline: Gst.Pipeline | None = None
        self._vsrc: Gst.Element | None = None
        self._timeoverlay: Gst.Element | None = None
        self._textoverlay: Gst.Element | None = None
        self._asrc: Gst.Element | None = None
        self._video_flow_id: str | None = None
        self._audio_flow_id: str | None = None
        self._mxl_domain: str = os.getenv("MXL_DOMAIN", "/mxl-domain")

        self._video_pattern: str = DEFAULT_VIDEO_PATTERN
        self._audio_pattern: str = DEFAULT_AUDIO_PATTERN
        self._timecode: bool = True
        self._ident: str = ""

        self._glib_loop = GLib.MainLoop()
        threading.Thread(target=self._glib_loop.run, daemon=True).start()

    # ── Public API ────────────────────────────────────────────────────────────

    def set_flow_ids(self, video_flow_id: str, audio_flow_id: str) -> None:
        with self._lock:
            self._video_flow_id = video_flow_id
            self._audio_flow_id = audio_flow_id
            log.info("Flow IDs set – video=%s  audio=%s", video_flow_id, audio_flow_id)
            if self._pipeline is None:
                self._build_pipeline()

    def set_video_pattern(self, pattern: str) -> None:
        with self._lock:
            if pattern not in VIDEO_PATTERNS:
                raise ValueError(f"Unknown video pattern: {pattern!r}")
            self._video_pattern = pattern
            if self._vsrc:
                self._vsrc.set_property("pattern", VIDEO_PATTERNS[pattern])
            log.info("Video pattern → %s", pattern)

    def set_audio_pattern(self, pattern: str) -> None:
        with self._lock:
            if pattern not in AUDIO_PATTERNS:
                raise ValueError(f"Unknown audio pattern: {pattern!r}")
            self._audio_pattern = pattern
            if self._asrc:
                self._asrc.set_property("wave", AUDIO_PATTERNS[pattern])
            log.info("Audio pattern → %s", pattern)

    def set_timecode(self, enabled: bool) -> None:
        with self._lock:
            self._timecode = enabled
            if self._timeoverlay:
                # Hide overlay by making text empty when disabled
                self._timeoverlay.set_property("silent", not enabled)
            log.info("Timecode overlay → %s", enabled)

    def set_ident(self, text: str) -> None:
        with self._lock:
            self._ident = text
            if self._textoverlay:
                self._textoverlay.set_property("text", text)
            log.info("Ident → %r", text)

    def get_status(self) -> dict:
        with self._lock:
            return {
                "state":         "playing" if self._pipeline else "idle",
                "video_pattern": self._video_pattern,
                "audio_pattern": self._audio_pattern,
                "timecode":      self._timecode,
                "ident":         self._ident,
                "video_flow_id": self._video_flow_id,
                "audio_flow_id": self._audio_flow_id,
                "mxl_domain":    self._mxl_domain,
            }

    def get_video_patterns(self) -> list[str]:
        return list(VIDEO_PATTERNS.keys())

    def get_audio_patterns(self) -> list[str]:
        return list(AUDIO_PATTERNS.keys())

    # ── Pipeline ──────────────────────────────────────────────────────────────

    def _build_pipeline(self) -> None:
        """Build and start the generator pipeline. Caller must hold self._lock."""
        domain = self._mxl_domain
        vid    = self._video_flow_id
        aud    = self._audio_flow_id

        pipeline = Gst.Pipeline.new("test-generator")

        # ── Video branch ──────────────────────────────────────────────────────
        vsrc = Gst.ElementFactory.make("videotestsrc",  "vsrc")
        vsrc.set_property("pattern",  VIDEO_PATTERNS[self._video_pattern])
        vsrc.set_property("is-live",  True)

        vcaps = Gst.ElementFactory.make("capsfilter", "vcaps")
        vcaps.set_property("caps", Gst.Caps.from_string(
            "video/x-raw,width=1920,height=1080,framerate=60/1"
        ))

        timeoverlay = Gst.ElementFactory.make("timeoverlay", "timeoverlay")
        timeoverlay.set_property("silent",        not self._timecode)
        timeoverlay.set_property("font-desc",     "Sans Bold 28")
        timeoverlay.set_property("halignment",    "right")
        timeoverlay.set_property("valignment",    "bottom")
        timeoverlay.set_property("shaded-background", True)

        textoverlay = Gst.ElementFactory.make("textoverlay", "textoverlay")
        textoverlay.set_property("text",          self._ident)
        textoverlay.set_property("font-desc",     "Sans Bold 36")
        textoverlay.set_property("halignment",    "center")
        textoverlay.set_property("valignment",    "top")
        textoverlay.set_property("shaded-background", True)

        vconv  = Gst.ElementFactory.make("videoconvert", "vconv")
        vqueue = Gst.ElementFactory.make("queue",        "vqueue")
        vsink  = Gst.ElementFactory.make("mxlsink",      "vsink")
        vsink.set_property("flow-id", vid)
        vsink.set_property("domain",  domain)

        for el in (vsrc, vcaps, timeoverlay, textoverlay, vconv, vqueue, vsink):
            pipeline.add(el)

        vsrc.link(vcaps)
        vcaps.link(timeoverlay)
        timeoverlay.link(textoverlay)
        textoverlay.link(vconv)
        vconv.link(vqueue)
        vqueue.link(vsink)

        # ── Audio branch ──────────────────────────────────────────────────────
        asrc = Gst.ElementFactory.make("audiotestsrc", "asrc")
        asrc.set_property("wave",    AUDIO_PATTERNS[self._audio_pattern])
        asrc.set_property("freq",    DEFAULT_AUDIO_FREQ)
        asrc.set_property("volume",  DEFAULT_AUDIO_VOL)
        asrc.set_property("is-live", True)

        acaps = Gst.ElementFactory.make("capsfilter", "acaps")
        acaps.set_property("caps", Gst.Caps.from_string(
            "audio/x-raw,format=S24LE,channels=2,rate=48000"
        ))

        aconv  = Gst.ElementFactory.make("audioconvert",  "aconv")
        aqueue = Gst.ElementFactory.make("queue",          "aqueue")
        asink  = Gst.ElementFactory.make("mxlsink",        "asink")
        asink.set_property("flow-id", aud)
        asink.set_property("domain",  domain)

        for el in (asrc, acaps, aconv, aqueue, asink):
            pipeline.add(el)

        asrc.link(acaps)
        acaps.link(aconv)
        aconv.link(aqueue)
        aqueue.link(asink)

        # ── Bus ───────────────────────────────────────────────────────────────
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_error)

        pipeline.set_state(Gst.State.PLAYING)
        ret, state, _ = pipeline.get_state(10 * Gst.SECOND)
        log.info("Pipeline PLAYING result: %s (state=%s)", ret, state)

        self._pipeline   = pipeline
        self._vsrc       = vsrc
        self._timeoverlay = timeoverlay
        self._textoverlay = textoverlay
        self._asrc       = asrc

        self._patch_flow_defs()

    def _patch_flow_defs(self) -> None:
        for flow_id, label in (
            (self._video_flow_id, "Test Generator – Video"),
            (self._audio_flow_id, "Test Generator – Audio"),
        ):
            if not flow_id:
                continue
            path = os.path.join(self._mxl_domain, f"{flow_id}.mxl-flow", "flow_def.json")
            try:
                with open(path) as f:
                    data = json.load(f)
                data["label"]       = label
                data["description"] = f"MXL {label} output"
                with open(path, "w") as f:
                    json.dump(data, f, indent=2)
                log.info("Patched flow_def.json: %s → %s", flow_id, label)
            except Exception as exc:
                log.warning("Could not patch flow_def.json for %s: %s", flow_id, exc)

    def _on_error(self, _bus: Gst.Bus, msg: Gst.Message) -> None:
        err, debug = msg.parse_error()
        log.error("GStreamer error: %s  debug: %s", err, debug)
