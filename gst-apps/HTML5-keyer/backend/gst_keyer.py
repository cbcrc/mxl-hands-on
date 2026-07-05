"""
GStreamer MXL HTML5 Keyer.

Pipeline (user-controlled start/stop):
  Background branch — live MXL video input:
    mxlsrc(video-flow-id) → capsfilter(v210) → videoconvert
      → capsfilter(BGRA, WxH, num/den) → queue(leaky) → compositor.sink_0

  CEF overlay branch — Chromium-rendered HTML5 graphic:
    cefsrc(url) → capsfilter(BGRA, WxH, num/den) → videorate
      → queue(leaky) → compositor.sink_1

  Output branch:
    compositor.src → videoconvert
      → capsfilter(v210, WxH, num/den, interlace-mode)
      → queue(leaky) → mxlsink(flow-id, domain, sync=false)

Key toggle is a runtime change of the `alpha` property on compositor.sink_1
(0.0 = OFF, 1.0 = ON).  `sync=false` is set on that same pad so the mixer
composites the CEF buffer as soon as it arrives, regardless of the live MXL
clock alignment.

The output flow UUID is deterministic (UUID v5) derived from the group hint.
"""

from __future__ import annotations

import gc
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst

Gst.init(None)
log = logging.getLogger(__name__)

# Fixed namespace for UUID v5 derivation.  Treat as immutable — changing it
# would orphan all previously written flow directories on every domain.
_MXL_KEYER_NS = uuid.UUID("e7f4a23c-9b85-5d1a-8c2e-1f3a6b5d4e7c")


# ── Flow format helpers ───────────────────────────────────────────────────────


def read_flow_format(domain_path: str, flow_uuid: str) -> dict:
    """Load flow_def.json and return the fields used to drive output caps."""
    path = Path(domain_path) / f"{flow_uuid}.mxl-flow" / "flow_def.json"
    if not path.exists():
        raise FileNotFoundError(f"flow_def.json not found: {path}")
    data = json.loads(path.read_text())
    try:
        return {
            "frame_width":    int(data["frame_width"]),
            "frame_height":   int(data["frame_height"]),
            "grain_rate":     {
                "numerator":   int(data["grain_rate"]["numerator"]),
                "denominator": int(data["grain_rate"]["denominator"]),
            },
            "interlace_mode": str(data.get("interlace_mode", "progressive")),
            "colorspace":     str(data.get("colorspace", "BT709")),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise KeyError(f"flow_def.json missing required field: {exc}") from exc


def _interlace_mode_to_caps(mode: str) -> str:
    """Map MXL interlace_mode string to GStreamer interlace-mode caps value."""
    m = (mode or "progressive").lower()
    if m == "progressive":
        return "progressive"
    return "interleaved"  # interlaced_tff / interlaced_bff


def _colorspace_to_colorimetry(colorspace: str) -> str:
    """Map MXL colorspace string to GStreamer colorimetry caps value."""
    cs = (colorspace or "BT709").upper()
    if cs in ("BT601", "BT.601", "SMPTE170M"):
        return "bt601"
    return "bt709"  # default for broadcast video


# ── Pipeline ──────────────────────────────────────────────────────────────────


class GstKeyer:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pipeline: Optional[Gst.Pipeline] = None
        self._compositor: Optional[Gst.Element] = None
        self._cef_pad: Optional[Gst.Pad] = None
        self._bg_pad: Optional[Gst.Pad] = None
        self._running = False
        self._error_msg: Optional[str] = None
        self._gen = 0

        self._mode: Optional[str] = None           # "key" | "prompt"
        self._audio_flow_uuid: Optional[str] = None
        self._audio_cb = None                       # callable(bytes) for prompt audio
        self._domain_path: Optional[str] = None
        self._input_flow_uuid: Optional[str] = None
        self._html5_url: Optional[str] = None
        self._output_flow_uuid: Optional[str] = None
        self._format: Optional[dict] = None
        self._grouphint: Optional[str] = None
        self._description: Optional[str] = None
        self._label: Optional[str] = None
        self._key_on: bool = False

        self._glib_loop = GLib.MainLoop()
        threading.Thread(target=self._glib_loop.run, daemon=True).start()

    # ── Public API ────────────────────────────────────────────────────────────

    def start(
        self,
        domain_path: str,
        input_flow_uuid: str,
        html5_url: str,
        grouphint: str,
        description: str,
        label: str,
    ) -> dict:
        """
        Build and start the pipeline.  Raises ValueError on a missing or
        malformed input flow_def.json.  The ValueError's .args[0] is a dict
        with keys "detail", "errors" suitable for direct serialisation.
        """
        try:
            fmt = read_flow_format(domain_path, input_flow_uuid)
        except (FileNotFoundError, KeyError) as exc:
            raise ValueError({
                "detail":          "Input flow format could not be read",
                "errors":          [str(exc)],
                "input_flow_uuid": input_flow_uuid,
            })

        with self._lock:
            self._teardown()
            self._gen += 1
            gen = self._gen
            self._mode             = "key"
            self._audio_flow_uuid  = None
            self._audio_cb         = None
            self._domain_path      = domain_path
            self._input_flow_uuid  = input_flow_uuid
            self._html5_url        = html5_url
            self._format           = fmt
            self._grouphint        = grouphint
            self._description      = description
            self._label            = label
            self._key_on           = False
            self._output_flow_uuid = str(
                uuid.uuid5(_MXL_KEYER_NS, f"{grouphint}:video")
            )
            self._error_msg = None
            try:
                self._build_pipeline()
            except Exception as exc:
                log.error("Failed to build pipeline: %s", exc, exc_info=True)
                self._error_msg = str(exc)
                self._running = False
                self._teardown()
                raise RuntimeError(str(exc)) from exc

            output_uuid = self._output_flow_uuid
            domain = self._domain_path

        # Patch flow_def.json after mxlsink writes it (background thread)
        threading.Thread(
            target=self._patch_flow_def,
            args=(domain, output_uuid, grouphint, description, label, gen),
            daemon=True,
        ).start()

        return self.get_status()

    def start_prompt(
        self,
        domain_path: str,
        audio_flow_uuid: Optional[str],
        width: int,
        height: int,
        num: int,
        den: int,
        html5_url: str,
        grouphint: str,
        description: str,
        label: str,
        audio_cb=None,
    ) -> dict:
        """
        Build and start the teleprompter pipeline: a black background at the
        chosen raster/rate, the internally-hosted teleprompter graphic keyed on
        top (alpha fixed at 1.0), and an optional MXL audio branch tapped into
        ``audio_cb`` for server-side voice tracking.  Geometry comes from the
        caller's preset — there is no input flow to read.
        """
        fmt = {
            "frame_width":    int(width),
            "frame_height":   int(height),
            "grain_rate":     {"numerator": int(num), "denominator": int(den)},
            "interlace_mode": "progressive",
            "colorspace":     "BT709",
        }

        with self._lock:
            self._teardown()
            self._gen += 1
            gen = self._gen
            self._mode             = "prompt"
            self._domain_path      = domain_path
            self._input_flow_uuid  = None
            self._audio_flow_uuid  = audio_flow_uuid or None
            self._audio_cb         = audio_cb
            self._html5_url        = html5_url
            self._format           = fmt
            self._grouphint        = grouphint
            self._description      = description
            self._label            = label
            self._key_on           = False
            self._output_flow_uuid = str(
                uuid.uuid5(_MXL_KEYER_NS, f"{grouphint}:video")
            )
            self._error_msg = None
            try:
                self._build_prompt_pipeline()
            except Exception as exc:
                log.error("Failed to build prompt pipeline: %s", exc, exc_info=True)
                self._error_msg = str(exc)
                self._running = False
                self._teardown()
                raise RuntimeError(str(exc)) from exc

            output_uuid = self._output_flow_uuid
            domain = self._domain_path

        threading.Thread(
            target=self._patch_flow_def,
            args=(domain, output_uuid, grouphint, description, label, gen),
            daemon=True,
        ).start()

        return self.get_status()

    def stop(self) -> None:
        with self._lock:
            self._teardown()
            self._mode             = None
            self._audio_flow_uuid  = None
            self._audio_cb         = None
            self._input_flow_uuid  = None
            self._html5_url        = None
            self._format           = None
            self._output_flow_uuid = None
            self._domain_path      = None
            self._grouphint        = None
            self._description      = None
            self._label            = None

    def set_key(self, on: bool) -> None:
        with self._lock:
            if not self._running or self._cef_pad is None:
                raise RuntimeError("Pipeline is not running")
            if self._mode == "prompt":
                raise RuntimeError("Key toggle not available in prompt mode")
            self._cef_pad.set_property("alpha", 1.0 if on else 0.0)
            self._key_on = bool(on)
            log.info("Key set to %s (alpha=%.1f)", "ON" if on else "OFF", 1.0 if on else 0.0)

    def get_status(self) -> dict:
        with self._lock:
            return {
                "running":           self._running,
                "mode":              self._mode,
                "domain_path":       self._domain_path,
                "input_flow_uuid":   self._input_flow_uuid,
                "audio_flow_uuid":   self._audio_flow_uuid,
                "html5_url":         self._html5_url,
                "output_flow_uuid":  self._output_flow_uuid,
                "format":            self._format,
                "grouphint":         self._grouphint,
                "description":       self._description,
                "label":             self._label,
                "key_on":            self._key_on if self._running else False,
                "error":             self._error_msg,
            }

    # ── Pipeline construction ─────────────────────────────────────────────────

    def _build_pipeline(self) -> None:
        """Keying mode: live MXL video background + CEF overlay (alpha OFF)."""
        assert self._format is not None
        assert self._output_flow_uuid is not None
        assert self._domain_path is not None
        assert self._input_flow_uuid is not None
        assert self._html5_url is not None

        fmt = self._format
        gr = fmt["grain_rate"]
        W, H = fmt["frame_width"], fmt["frame_height"]
        num, den = gr["numerator"], gr["denominator"]
        interlace = _interlace_mode_to_caps(fmt["interlace_mode"])
        colorimetry = _colorspace_to_colorimetry(fmt.get("colorspace", "BT709"))

        out_caps_str = (
            f"video/x-raw,format=v210,width={W},height={H},"
            f"framerate={num}/{den},colorimetry={colorimetry}"
        )

        pipeline = Gst.Pipeline.new("html5-keyer-pipeline")
        compositor = self._make_compositor(pipeline)
        self._add_output_branch(pipeline, compositor, out_caps_str)

        # ── Background branch (compositor.sink_0) ─────────────────────────────
        # mxlsrc → videoconvert → BGRA → queue → compositor.sink_0
        #
        # mxlsrc emits a malformed `interlace-mode` caps field (it includes
        # JSON quote marks in the string value: `"progressive"` instead of
        # `progressive`).  videoconvert parses interlace-mode strictly and
        # rejects the caps with NOT_NEGOTIATED.  We install a pad probe on
        # mxlsrc.src that rewrites the CAPS event in-place before the next
        # element ever sees it.  This is more reliable than capssetter, which
        # we observed silently leaving the malformed field untouched.
        bg_src = Gst.ElementFactory.make("mxlsrc", "bg_src")
        if not bg_src:
            raise RuntimeError("Could not create mxlsrc — is libgstmxl.so installed in the plugin path?")
        bg_src.set_property("video-flow-id", self._input_flow_uuid)
        bg_src.set_property("domain", self._domain_path)

        bg_src_pad = bg_src.get_static_pad("src")
        bg_src_pad.add_probe(
            Gst.PadProbeType.EVENT_DOWNSTREAM,
            self._mxlsrc_caps_fix_probe,
        )

        bg_conv = Gst.ElementFactory.make("videoconvert", "bg_conv")

        bg_bgra_caps = Gst.ElementFactory.make("capsfilter", "bg_bgra_caps")
        bg_bgra_caps.set_property("caps", Gst.Caps.from_string("video/x-raw,format=BGRA"))

        bg_queue = self._make_leaky_queue("bg_queue")

        for el in (bg_src, bg_conv, bg_bgra_caps, bg_queue):
            pipeline.add(el)
        if not bg_src.link(bg_conv):
            raise RuntimeError("Failed to link bg mxlsrc → videoconvert")
        if not bg_conv.link(bg_bgra_caps):
            raise RuntimeError("Failed to link bg videoconvert → BGRA caps")
        if not bg_bgra_caps.link(bg_queue):
            raise RuntimeError("Failed to link bg BGRA caps → queue")

        self._link_bg_queue(compositor, bg_queue)

        # CEF overlay — key OFF on start (operator enables explicitly)
        self._add_cef_branch(pipeline, compositor, self._html5_url, W, H, num, den, 0.0)

        self._start_pipeline(pipeline, compositor)
        log.info(
            "Keyer started — input=%s, url=%s, output=%s, format=%dx%d @ %d/%d %s",
            self._input_flow_uuid, self._html5_url, self._output_flow_uuid,
            W, H, num, den, interlace,
        )

    def _build_prompt_pipeline(self) -> None:
        """Prompt mode: black background + teleprompter overlay (alpha fixed ON)."""
        assert self._format is not None
        assert self._output_flow_uuid is not None
        assert self._domain_path is not None
        assert self._html5_url is not None

        fmt = self._format
        gr = fmt["grain_rate"]
        W, H = fmt["frame_width"], fmt["frame_height"]
        num, den = gr["numerator"], gr["denominator"]

        out_caps_str = (
            f"video/x-raw,format=v210,width={W},height={H},"
            f"framerate={num}/{den},colorimetry=bt709"
        )

        pipeline = Gst.Pipeline.new("html5-prompter-pipeline")
        compositor = self._make_compositor(pipeline)
        self._add_output_branch(pipeline, compositor, out_caps_str)

        # ── Background branch (compositor.sink_0): synthetic black picture ────
        bg_src = Gst.ElementFactory.make("videotestsrc", "bg_src")
        if not bg_src:
            raise RuntimeError("Could not create videotestsrc — is gstreamer1.0-plugins-base installed?")
        bg_src.set_property("pattern", 2)        # 2 = solid black
        bg_src.set_property("is-live", True)

        bg_conv = Gst.ElementFactory.make("videoconvert", "bg_conv")
        bg_caps = Gst.ElementFactory.make("capsfilter", "bg_caps")
        bg_caps.set_property("caps", Gst.Caps.from_string(
            f"video/x-raw,format=BGRA,width={W},height={H},"
            f"framerate={num}/{den},interlace-mode=progressive"
        ))
        bg_queue = self._make_leaky_queue("bg_queue")

        for el in (bg_src, bg_conv, bg_caps, bg_queue):
            pipeline.add(el)
        if not bg_src.link(bg_conv):
            raise RuntimeError("Failed to link videotestsrc → videoconvert")
        if not bg_conv.link(bg_caps):
            raise RuntimeError("Failed to link bg videoconvert → BGRA caps")
        if not bg_caps.link(bg_queue):
            raise RuntimeError("Failed to link bg BGRA caps → queue")

        self._link_bg_queue(compositor, bg_queue)

        # Teleprompter overlay — alpha fixed at 1.0 (always visible).
        self._add_cef_branch(pipeline, compositor, self._html5_url, W, H, num, den, 1.0)

        # Optional audio branch for server-side voice tracking.
        if self._audio_flow_uuid:
            self._add_audio_branch(pipeline, self._audio_flow_uuid, self._domain_path)

        self._start_pipeline(pipeline, compositor)
        log.info(
            "Prompter started — audio=%s, url=%s, output=%s, format=%dx%d @ %d/%d",
            self._audio_flow_uuid, self._html5_url, self._output_flow_uuid,
            W, H, num, den,
        )

    # ── Shared pipeline-construction helpers ──────────────────────────────────

    def _make_compositor(self, pipeline: Gst.Pipeline) -> Gst.Element:
        compositor = Gst.ElementFactory.make("compositor", "mixer")
        if not compositor:
            raise RuntimeError(
                "Could not create compositor — is gstreamer1.0-plugins-base installed?"
            )
        # IMPORTANT: do NOT set ignore-inactive-pads here.  CEF (sink_1) takes
        # 1-3 s to spin up Chromium and deliver its first frame.  With
        # ignore-inactive-pads=true the compositor's source task fires an
        # aggregate at PLAYING while base_time is still 0 (so the deadline is
        # already "in the past"), races ahead of the CEF pad before its
        # serialized caps/allocation-query have been consumed, and the aggregate
        # thread then deadlocks on those un-consumed events — the compositor
        # produces no output for the whole session and mxl-info-gui reports the
        # output flow at the epoch (~56 years).  With the property left at its
        # default (False) the compositor instead WAITS for CEF's first buffer,
        # consuming its caps/query while waiting — so there is no deadlock.
        pipeline.add(compositor)
        return compositor

    def _add_output_branch(
        self, pipeline: Gst.Pipeline, compositor: Gst.Element, out_caps_str: str
    ) -> None:
        # ── Output branch: compositor.src → pts_fix → videoconvert → caps(v210) → queue → mxlsink ──
        #
        # The compositor converts all input PTSes to running time (ns elapsed
        # since the pipeline started).  mxlsink's render_video computes the
        # grain index as:  mxl_pts = buffer.pts + (mxl_now − clock.time()).
        # Re-adding pipeline.base_time() to each buffer PTS after the compositor
        # makes buffer.pts an absolute pipeline-clock timestamp again, so
        # mxl_pts ≈ mxl_now and grains land at the correct TAI index instead of
        # near the epoch (otherwise mxl-info-gui shows latency ≈ 56 years).
        #
        # The output capsfilter pins colorimetry so the two CAPS events the
        # compositor emits (before/after CEF joins) are identical from mxlsink's
        # perspective; otherwise GstBaseSink calls set_caps twice and mxlsink
        # aborts with "another active writer".
        pts_fix = Gst.ElementFactory.make("identity", "pts_fix")
        if not pts_fix:
            raise RuntimeError("Could not create identity element for PTS fix")
        pts_fix.set_property("signal-handoffs", True)
        pipeline.add(pts_fix)

        def _on_pts_fix_handoff(element, buf):
            base_time = pipeline.get_base_time()
            if not base_time or base_time == Gst.CLOCK_TIME_NONE:
                return
            if buf.pts == Gst.CLOCK_TIME_NONE:
                return
            buf.pts += base_time
            if buf.dts != Gst.CLOCK_TIME_NONE:
                buf.dts += base_time

        pts_fix.connect("handoff", _on_pts_fix_handoff)

        out_conv  = Gst.ElementFactory.make("videoconvert", "out_conv")
        out_caps  = Gst.ElementFactory.make("capsfilter",   "out_caps")
        out_caps.set_property("caps", Gst.Caps.from_string(out_caps_str))
        out_queue = self._make_leaky_queue("out_queue")
        out_sink  = Gst.ElementFactory.make("mxlsink", "out_sink")
        if not out_sink:
            raise RuntimeError(
                "Could not create mxlsink — is libgstmxl.so installed in the plugin path?"
            )
        out_sink.set_property("flow-id", self._output_flow_uuid)
        out_sink.set_property("domain", self._domain_path)
        out_sink.set_property("sync", False)

        for el in (out_conv, out_caps, out_queue, out_sink):
            pipeline.add(el)
        if not compositor.link(pts_fix):
            raise RuntimeError("Failed to link compositor → pts_fix")
        if not pts_fix.link(out_conv):
            raise RuntimeError("Failed to link pts_fix → out_conv")
        if not out_conv.link(out_caps):
            raise RuntimeError("Failed to link out_conv → out_caps")
        if not out_caps.link(out_queue):
            raise RuntimeError("Failed to link out_caps → out_queue")
        if not out_queue.link(out_sink):
            raise RuntimeError("Failed to link out_queue → mxlsink")

    def _link_bg_queue(self, compositor: Gst.Element, bg_queue: Gst.Element) -> None:
        bg_pad = compositor.request_pad_simple("sink_%u")
        if bg_pad is None:
            raise RuntimeError("Could not request background sink pad on compositor")
        bg_pad.set_property("zorder", 0)
        bg_pad.set_property("xpos", 0)
        bg_pad.set_property("ypos", 0)
        if bg_queue.get_static_pad("src").link(bg_pad) != Gst.PadLinkReturn.OK:
            raise RuntimeError("Failed to link bg queue → compositor.sink_0")
        self._bg_pad = bg_pad

    def _add_cef_branch(
        self,
        pipeline: Gst.Pipeline,
        compositor: Gst.Element,
        url: str,
        W: int,
        H: int,
        num: int,
        den: int,
        alpha: float,
    ) -> None:
        # ── CEF overlay branch (compositor.sink_1) ────────────────────────────
        # cefsrc outputs video/x-raw directly — cefdemux is NOT needed and must
        # NOT be used (its dynamic pad re-emits CAPS downstream, causing a second
        # set_caps on mxlsink → "another active writer").  cefsrc renders at an
        # integer fps; videorate re-paces it to the exact MXL grain rate so the
        # mixer always sees a steady cadence.
        cef_fps = max(1, round(num / den))

        cef_src = Gst.ElementFactory.make("cefsrc", "cef_src")
        if not cef_src:
            raise RuntimeError(
                "Could not create cefsrc — is gst-plugin-cef (libgstcef.so) installed in the plugin path?"
            )
        cef_src.set_property("url", url)

        cef_src_caps = Gst.ElementFactory.make("capsfilter", "cef_src_caps")
        cef_src_caps.set_property("caps", Gst.Caps.from_string(
            f"video/x-raw,format=BGRA,width={W},height={H},framerate={cef_fps}/1"
        ))

        cef_rate = Gst.ElementFactory.make("videorate", "cef_rate")
        cef_rate_caps = Gst.ElementFactory.make("capsfilter", "cef_rate_caps")
        cef_rate_caps.set_property(
            "caps", Gst.Caps.from_string(f"video/x-raw,format=BGRA,framerate={num}/{den}")
        )

        cef_queue = self._make_leaky_queue("cef_queue")

        for el in (cef_src, cef_src_caps, cef_rate, cef_rate_caps, cef_queue):
            pipeline.add(el)
        if not cef_src.link(cef_src_caps):
            raise RuntimeError("Failed to link cefsrc → src caps")
        if not cef_src_caps.link(cef_rate):
            raise RuntimeError("Failed to link cef src caps → videorate")
        if not cef_rate.link(cef_rate_caps):
            raise RuntimeError("Failed to link cef videorate → rate caps")
        if not cef_rate_caps.link(cef_queue):
            raise RuntimeError("Failed to link cef rate caps → queue")

        cef_pad = compositor.request_pad_simple("sink_%u")
        if cef_pad is None:
            raise RuntimeError("Could not request CEF sink pad on compositor")
        cef_pad.set_property("zorder", 1)
        cef_pad.set_property("xpos", 0)
        cef_pad.set_property("ypos", 0)
        cef_pad.set_property("alpha", alpha)
        try:
            cef_pad.set_property("sync", False)   # CEF runs on system clock; MXL on live clock
        except Exception as exc:
            log.warning("compositor.sink_1.sync not available: %s", exc)
        if cef_queue.get_static_pad("src").link(cef_pad) != Gst.PadLinkReturn.OK:
            raise RuntimeError("Failed to link cef queue → compositor.sink_1")
        self._cef_pad = cef_pad

    def _add_audio_branch(self, pipeline: Gst.Pipeline, flow_uuid: str, domain: str) -> None:
        # mxlsrc(audio-flow-id) → audioconvert → audioresample
        #   → caps(S16LE, mono, 16 kHz) → appsink   (Vosk wants 16k mono PCM)
        # Independent branch — no link to compositor/mxlsink.  appsink runs with
        # sync=false so the live MXL audio clock never stalls the video output.
        a_src = Gst.ElementFactory.make("mxlsrc", "audio_src")
        if not a_src:
            raise RuntimeError("Could not create mxlsrc for audio")
        a_src.set_property("audio-flow-id", flow_uuid)
        a_src.set_property("domain", domain)

        a_conv = Gst.ElementFactory.make("audioconvert",  "audio_conv")
        a_res  = Gst.ElementFactory.make("audioresample", "audio_resample")
        a_caps = Gst.ElementFactory.make("capsfilter",    "audio_caps")
        a_caps.set_property("caps", Gst.Caps.from_string(
            "audio/x-raw,format=S16LE,channels=1,rate=16000"
        ))
        a_sink = Gst.ElementFactory.make("appsink", "audio_sink")
        if not a_sink:
            raise RuntimeError("Could not create appsink — is gstreamer1.0-plugins-base installed?")
        a_sink.set_property("emit-signals", True)
        a_sink.set_property("sync", False)
        a_sink.set_property("max-buffers", 10)
        a_sink.set_property("drop", True)
        a_sink.connect("new-sample", self._on_audio_sample)

        for el in (a_src, a_conv, a_res, a_caps, a_sink):
            pipeline.add(el)
        if not a_src.link(a_conv):
            raise RuntimeError("Failed to link audio mxlsrc → audioconvert")
        if not a_conv.link(a_res):
            raise RuntimeError("Failed to link audioconvert → audioresample")
        if not a_res.link(a_caps):
            raise RuntimeError("Failed to link audioresample → caps")
        if not a_caps.link(a_sink):
            raise RuntimeError("Failed to link audio caps → appsink")

    def _on_audio_sample(self, sink) -> int:
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        buf = sample.get_buffer()
        ok, mapinfo = buf.map(Gst.MapFlags.READ)
        if ok:
            try:
                cb = self._audio_cb
                if cb is not None:
                    cb(bytes(mapinfo.data))
            except Exception:
                log.exception("audio sample callback failed")
            finally:
                buf.unmap(mapinfo)
        return Gst.FlowReturn.OK

    def _start_pipeline(self, pipeline: Gst.Pipeline, compositor: Gst.Element) -> None:
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error",   self._on_error)
        bus.connect("message::warning", self._on_warning)

        self._pipeline   = pipeline
        self._compositor = compositor

        ret = pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            pipeline.set_state(Gst.State.NULL)
            raise RuntimeError("Pipeline failed to reach PLAYING state")
        pipeline.get_state(10 * Gst.SECOND)
        self._running = True

    @staticmethod
    def _mxlsrc_caps_fix_probe(pad: Gst.Pad, info: Gst.PadProbeInfo) -> int:
        """
        Pad probe on mxlsrc.src that rewrites the CAPS event when the Rust
        plugin includes JSON quote marks in the `interlace-mode` string value
        (e.g. `"progressive"` instead of `progressive`).  Without this fix,
        downstream `videoconvert` rejects negotiation as not-negotiated.
        """
        try:
            event = info.get_event()
            if event is None or event.type != Gst.EventType.CAPS:
                return Gst.PadProbeReturn.OK
            caps = event.parse_caps()
            if caps is None or caps.get_size() == 0:
                return Gst.PadProbeReturn.OK
            s = caps.get_structure(0)
            val = s.get_string("interlace-mode")
            if not val or not (len(val) >= 2 and val[0] == '"' and val[-1] == '"'):
                return Gst.PadProbeReturn.OK
            clean = val[1:-1]
            new_caps = caps.copy()
            new_caps.get_structure(0).set_value("interlace-mode", clean)
            log.info(
                "Rewrote mxlsrc caps: interlace-mode '%s' → '%s'", val, clean
            )
            # Replace the event in the probe info so downstream sees the fixed caps
            info.data = Gst.Event.new_caps(new_caps)
            return Gst.PadProbeReturn.OK
        except Exception:
            log.exception("mxlsrc caps-fix probe failed")
            return Gst.PadProbeReturn.OK

    @staticmethod
    def _make_leaky_queue(name: str) -> Gst.Element:
        """Live-pipeline queue: drop oldest, never accumulate stale frames."""
        q = Gst.ElementFactory.make("queue", name)
        if not q:
            raise RuntimeError(f"Could not create queue '{name}'")
        # 2 = downstream (drop oldest)
        q.set_property("leaky", 2)
        q.set_property("max-size-buffers", 2)
        q.set_property("max-size-time", 0)
        q.set_property("max-size-bytes", 0)
        return q

    # ── Teardown ──────────────────────────────────────────────────────────────

    def _teardown(self) -> None:
        if self._pipeline is None:
            self._running = False
            self._compositor = None
            self._cef_pad = None
            self._bg_pad  = None
            return
        log.info("Tearing down pipeline…")
        self._pipeline.set_state(Gst.State.NULL)
        # CEF helper processes take a moment to exit; allow extra time
        self._pipeline.get_state(10 * Gst.SECOND)
        self._pipeline = None
        self._compositor = None
        self._cef_pad = None
        self._bg_pad  = None
        self._running = False
        gc.collect()
        log.info("Teardown complete")

    # ── Bus callbacks ─────────────────────────────────────────────────────────

    def _on_error(self, _bus: Gst.Bus, msg: Gst.Message) -> None:
        err, debug = msg.parse_error()
        log.error("GStreamer error: %s  debug: %s", err, debug)
        with self._lock:
            self._error_msg = str(err)

    def _on_warning(self, _bus: Gst.Bus, msg: Gst.Message) -> None:
        warn, debug = msg.parse_warning()
        log.warning("GStreamer warning: %s  debug: %s", warn, debug)

    # ── flow_def.json patching ────────────────────────────────────────────────

    def _patch_flow_def(
        self,
        domain_path: str,
        flow_uuid: str,
        grouphint: str,
        description: str,
        label: str,
        gen: int,
    ) -> None:
        path = Path(domain_path) / f"{flow_uuid}.mxl-flow" / "flow_def.json"
        deadline = time.monotonic() + 15
        while not path.exists():
            if time.monotonic() > deadline:
                log.warning("Timeout waiting for flow_def.json: %s", path)
                return
            if self._gen != gen:
                return
            time.sleep(0.5)
        try:
            data = json.loads(path.read_text())
            full_grouphint = f"{grouphint}:Video"
            data["grouphint"]   = full_grouphint
            data["description"] = description
            data["label"]       = label
            if isinstance(data.get("tags"), dict):
                data["tags"]["urn:x-nmos:tag:grouphint/v1.0"] = [full_grouphint]
            path.write_text(json.dumps(data, indent=2))
            log.info("Patched flow_def.json for output flow %s", flow_uuid)
        except Exception as exc:
            log.warning("Could not patch flow_def.json: %s", exc)
