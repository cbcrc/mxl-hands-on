"""
GStreamer test-signal generator.

Pipeline (user-controlled start/stop):
  Video:   videotestsrc → capsfilter(res+fps) → timeoverlay → textoverlay
                        → videoconvert → capsfilter(v210) → queue → mxlsink
  Audio 1: audiotestsrc → audioconvert → capsfilter(F32LE,Nch,48k) → queue → mxlsink
  Audio 2: audiotestsrc → audioconvert → capsfilter(F32LE,Nch,48k) → queue → mxlsink

Live-adjustable: video pattern, timecode, ident, audio pattern, audio level.
Channel-count changes trigger a full pipeline rebuild with fresh UUIDs.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst

Gst.init(None)
log = logging.getLogger(__name__)


VIDEO_PATTERNS: dict[str, int] = {
    "100% bars":        19,
    "SMPTE 75%":        13,
    "SMPTE":             0,
    "Snow":              1,
    "Black":             2,
    "White":             3,
    "Red":               4,
    "Green":             5,
    "Blue":              6,
    "Checkers 1":        7,
    "Checkers 2":        8,
    "Checkers 4":        9,
    "Checkers 8":       10,
    "Circular":         11,
    "Blink":            12,
    "Zone Plate":       14,
    "Gamut":            15,
    "Chroma Zone Plate":16,
    "Solid Color":      17,
    "Ball":             18,
    "Bar":              20,
    "Pinwheel":         21,
    "Spokes":           22,
    "Gradient":         23,
    "SMPTE RP-219":     25,
}

AUDIO_PATTERNS: dict[str, int] = {
    "1 kHz tone":     0,
    "Square":         1,
    "Saw":            2,
    "Triangle":       3,
    "Silence":        4,
    "White Noise":    5,
    "Pink Noise":     6,
    "Sine Table":     7,
    "Ticks":          8,
    "Gaussian Noise": 9,
    "Red Noise":      10,
    "Blue Noise":     11,
    "Violet Noise":   12,
}

RESOLUTIONS: dict[str, tuple[int, int]] = {
    "1280x720":  (1280, 720),
    "1920x1080": (1920, 1080),
    "3840x2160": (3840, 2160),
}

FRAMERATES: dict[str, tuple[int, int]] = {
    "24":    (24,    1),
    "25":    (25,    1),
    "29.97": (30000, 1001),
    "30":    (30,    1),
    "50":    (50,    1),
    "59.94": (60000, 1001),
    "60":    (60,    1),
}


def db_to_linear(db: float) -> float:
    return 10 ** (db / 20)


@dataclass
class AudioState:
    pattern: str = "1 kHz tone"
    channels: int = 2
    level_db: float = -20.0
    src: object = field(default=None, repr=False)


class GstGenerator:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pipeline: Optional[Gst.Pipeline] = None
        self._running = False
        self._gen = 0  # incremented on each pipeline start; used to cancel stale patch threads

        self._config: Optional[dict] = None
        self._flow_uuids: dict[str, str] = {}

        # Video state
        self._video_pattern = "100% bars"
        self._timecode = True
        self._ident = ""
        self._resolution = "1920x1080"
        self._framerate = "30"

        # Audio state
        self._audio1 = AudioState()
        self._audio2 = AudioState()

        # Element refs for live property changes
        self._vsrc = None
        self._timeoverlay = None
        self._textoverlay = None

        self._glib_loop = GLib.MainLoop()
        threading.Thread(target=self._glib_loop.run, daemon=True).start()

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self, config: dict) -> dict:
        with self._lock:
            if self._running:
                raise RuntimeError("Pipeline is already running")
            self._config = config
            self._resolution = config.get("resolution", "1920x1080")
            self._framerate = config.get("framerate", "30")
            self._audio1.channels = config.get("audio1", {}).get("channels", 2)
            self._audio2.channels = config.get("audio2", {}).get("channels", 2)
            self._gen += 1
            gen = self._gen
            self._flow_uuids = self._generate_uuids(config)
            self._build_pipeline(config)
            self._running = True
            uuids = dict(self._flow_uuids)

        threading.Thread(
            target=self._patch_flow_defs,
            args=(config, uuids, gen),
            daemon=True,
        ).start()
        return uuids

    def stop(self) -> None:
        with self._lock:
            self._teardown_pipeline()
            self._running = False
            self._flow_uuids = {}

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def set_video_pattern(self, pattern: str) -> None:
        if pattern not in VIDEO_PATTERNS:
            raise ValueError(f"Unknown video pattern: {pattern!r}")
        with self._lock:
            self._video_pattern = pattern
            if self._vsrc:
                self._vsrc.set_property("pattern", VIDEO_PATTERNS[pattern])

    def set_timecode(self, enabled: bool) -> None:
        with self._lock:
            self._timecode = enabled
            if self._timeoverlay:
                self._timeoverlay.set_property("silent", not enabled)

    def set_ident(self, text: str) -> None:
        with self._lock:
            self._ident = text
            if self._textoverlay:
                self._textoverlay.set_property("text", text)

    def set_audio_pattern(self, flow: int, pattern: str) -> None:
        if pattern not in AUDIO_PATTERNS:
            raise ValueError(f"Unknown audio pattern: {pattern!r}")
        state = self._audio1 if flow == 1 else self._audio2
        with self._lock:
            state.pattern = pattern
            if state.src:
                state.src.set_property("wave", AUDIO_PATTERNS[pattern])

    def set_audio_level(self, flow: int, db: float) -> None:
        db = max(-60.0, min(0.0, db))
        db = round(db * 2) / 2
        state = self._audio1 if flow == 1 else self._audio2
        with self._lock:
            state.level_db = db
            if state.src:
                state.src.set_property("volume", db_to_linear(db))

    def get_audio_level(self, flow: int) -> float:
        state = self._audio1 if flow == 1 else self._audio2
        with self._lock:
            return state.level_db

    def get_status(self) -> dict:
        with self._lock:
            return {
                "running": self._running,
                "flow_uuids": dict(self._flow_uuids),
                "video": {
                    "pattern":    self._video_pattern,
                    "timecode":   self._timecode,
                    "ident":      self._ident,
                    "resolution": self._resolution,
                    "framerate":  self._framerate,
                },
                "audio1": {
                    "pattern":  self._audio1.pattern,
                    "channels": self._audio1.channels,
                    "level_db": self._audio1.level_db,
                },
                "audio2": {
                    "pattern":  self._audio2.pattern,
                    "channels": self._audio2.channels,
                    "level_db": self._audio2.level_db,
                },
            }

    def get_patterns(self) -> dict:
        return {
            "video": list(VIDEO_PATTERNS.keys()),
            "audio": list(AUDIO_PATTERNS.keys()),
        }

    def get_options(self) -> dict:
        return {
            "resolutions": list(RESOLUTIONS.keys()),
            "framerates":  list(FRAMERATES.keys()),
        }

    # ── Pipeline ──────────────────────────────────────────────────────────────

    def _generate_uuids(self, config: dict) -> dict[str, str]:
        uuids: dict[str, str] = {}
        if config.get("video", {}).get("active"):
            uuids["video"] = str(uuid.uuid4())
        if config.get("audio1", {}).get("active"):
            uuids["audio1"] = str(uuid.uuid4())
        if config.get("audio2", {}).get("active"):
            uuids["audio2"] = str(uuid.uuid4())
        return uuids

    def _teardown_pipeline(self) -> None:
        if self._pipeline is None:
            return
        self._pipeline.set_state(Gst.State.NULL)
        self._pipeline.get_state(5 * Gst.SECOND)
        self._pipeline = None
        self._vsrc = None
        self._timeoverlay = None
        self._textoverlay = None
        self._audio1.src = None
        self._audio2.src = None
        import gc
        gc.collect()

    def _build_pipeline(self, config: dict) -> None:
        domain = config["domain"]
        w, h = RESOLUTIONS.get(self._resolution, (1920, 1080))
        fn, fd = FRAMERATES.get(self._framerate, (30, 1))

        pipeline = Gst.Pipeline.new("test-generator")

        # ── Video branch ──────────────────────────────────────────────────────
        if config.get("video", {}).get("active"):
            vsrc = Gst.ElementFactory.make("videotestsrc", "vsrc")
            vsrc.set_property("pattern", VIDEO_PATTERNS[self._video_pattern])
            vsrc.set_property("is-live", True)

            vcaps_src = Gst.ElementFactory.make("capsfilter", "vcaps_src")
            vcaps_src.set_property(
                "caps",
                Gst.Caps.from_string(
                    f"video/x-raw,width={w},height={h},framerate={fn}/{fd}"
                ),
            )

            timeoverlay = Gst.ElementFactory.make("timeoverlay", "timeoverlay")
            timeoverlay.set_property("silent", not self._timecode)
            timeoverlay.set_property("font-desc", "Sans Bold 28")
            timeoverlay.set_property("halignment", "right")
            timeoverlay.set_property("valignment", "bottom")
            timeoverlay.set_property("shaded-background", True)

            textoverlay = Gst.ElementFactory.make("textoverlay", "textoverlay")
            textoverlay.set_property("text", self._ident)
            textoverlay.set_property("font-desc", "Sans Bold 36")
            textoverlay.set_property("halignment", "center")
            textoverlay.set_property("valignment", "top")
            textoverlay.set_property("shaded-background", True)

            vconv = Gst.ElementFactory.make("videoconvert", "vconv")

            vcaps_sink = Gst.ElementFactory.make("capsfilter", "vcaps_sink")
            vcaps_sink.set_property(
                "caps", Gst.Caps.from_string("video/x-raw,format=v210")
            )

            vqueue = Gst.ElementFactory.make("queue", "vqueue")

            vsink = Gst.ElementFactory.make("mxlsink", "vsink")
            vsink.set_property("flow-id", self._flow_uuids["video"])
            vsink.set_property("domain", domain)

            for el in (vsrc, vcaps_src, timeoverlay, textoverlay, vconv, vcaps_sink, vqueue, vsink):
                pipeline.add(el)

            vsrc.link(vcaps_src)
            vcaps_src.link(timeoverlay)
            timeoverlay.link(textoverlay)
            textoverlay.link(vconv)
            vconv.link(vcaps_sink)
            vcaps_sink.link(vqueue)
            vqueue.link(vsink)

            self._vsrc = vsrc
            self._timeoverlay = timeoverlay
            self._textoverlay = textoverlay

        # ── Audio Flow 1 ──────────────────────────────────────────────────────
        if config.get("audio1", {}).get("active"):
            self._build_audio_branch(pipeline, 1, self._flow_uuids["audio1"], domain)

        # ── Audio Flow 2 ──────────────────────────────────────────────────────
        if config.get("audio2", {}).get("active"):
            self._build_audio_branch(pipeline, 2, self._flow_uuids["audio2"], domain)

        # ── Bus ───────────────────────────────────────────────────────────────
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_error)
        bus.connect("message::warning", self._on_warning)

        pipeline.set_state(Gst.State.PLAYING)
        ret, state, _ = pipeline.get_state(10 * Gst.SECOND)
        log.info("Pipeline state: %s (ret=%s)", state, ret)

        self._pipeline = pipeline

    def _build_audio_branch(
        self, pipeline: Gst.Pipeline, flow_num: int, flow_uuid: str, domain: str
    ) -> None:
        state = self._audio1 if flow_num == 1 else self._audio2
        s = str(flow_num)

        asrc = Gst.ElementFactory.make("audiotestsrc", f"asrc{s}")
        asrc.set_property("wave", AUDIO_PATTERNS[state.pattern])
        asrc.set_property("freq", 1000.0)
        asrc.set_property("volume", db_to_linear(state.level_db))
        asrc.set_property("is-live", True)

        aconv = Gst.ElementFactory.make("audioconvert", f"aconv{s}")

        acaps = Gst.ElementFactory.make("capsfilter", f"acaps{s}")
        acaps.set_property(
            "caps",
            Gst.Caps.from_string(
                f"audio/x-raw,format=F32LE,channels={state.channels},rate=48000,layout=interleaved"
            ),
        )

        aqueue = Gst.ElementFactory.make("queue", f"aqueue{s}")

        asink = Gst.ElementFactory.make("mxlsink", f"asink{s}")
        asink.set_property("flow-id", flow_uuid)
        asink.set_property("domain", domain)

        for el in (asrc, aconv, acaps, aqueue, asink):
            pipeline.add(el)

        asrc.link(aconv)
        aconv.link(acaps)
        acaps.link(aqueue)
        aqueue.link(asink)

        state.src = asrc

    def _patch_flow_defs(self, config: dict, uuids: dict, gen: int) -> None:
        domain = config["domain"]
        grouphint = config.get("grouphint", "Test-Generator")
        flows = [
            ("video",  config.get("video",  {})),
            ("audio1", config.get("audio1", {})),
            ("audio2", config.get("audio2", {})),
        ]
        for key, flow_cfg in flows:
            if not flow_cfg.get("active"):
                continue
            flow_uuid = uuids.get(key)
            if not flow_uuid:
                continue
            path = os.path.join(domain, f"{flow_uuid}.mxl-flow", "flow_def.json")
            # Poll up to 15 s for mxlsink to write the file
            deadline = time.monotonic() + 15
            while not os.path.exists(path):
                if time.monotonic() > deadline:
                    log.warning("Timeout waiting for flow_def.json: %s", path)
                    break
                if self._gen != gen:
                    return  # pipeline restarted; abort
                time.sleep(0.5)
            if not os.path.exists(path):
                continue
            try:
                with open(path) as f:
                    data = json.load(f)
                data["grouphint"]   = grouphint
                data["description"] = flow_cfg.get("description", "")
                data["label"]       = flow_cfg.get("label", "")
                with open(path, "w") as f:
                    json.dump(data, f, indent=2)
                log.info("Patched flow_def.json: %s (%s)", key, flow_uuid)
            except Exception as exc:
                log.warning("Could not patch flow_def.json for %s: %s", key, exc)

    def _on_error(self, _bus: Gst.Bus, msg: Gst.Message) -> None:
        err, debug = msg.parse_error()
        log.error("GStreamer error: %s  debug: %s", err, debug)

    def _on_warning(self, _bus: Gst.Bus, msg: Gst.Message) -> None:
        warn, debug = msg.parse_warning()
        log.warning("GStreamer warning: %s  debug: %s", warn, debug)
