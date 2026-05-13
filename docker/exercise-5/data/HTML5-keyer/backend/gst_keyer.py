"""
GStreamer HTML5 Keyer pipeline.

Architecture:
  - Background branch:
      mxlsrc(flow_id)    if receiver connected, else
      videotestsrc(black) when no input flow is assigned
      → videoconvert → glupload → queue(leaky=none,4) → glvideomixer.sink_0
  - Overlay/keyer branch:
      cefsrc url="http://spx-server:5660/renderer/"
        → capsfilter(BGRA,1920x1080,60fps)
        → videorate → glupload → queue(leaky=drop-oldest,2) → glvideomixer.sink_1
  - Composition:
      glvideomixer → gldownload → videoconvert → capsfilter(v210) → queue → mxlsink
      (compositor used over glvideomixer so sync=False can be set on the
       CEF pad — glvideomixer's GstGLVideoMixerInput pads don't support it)

Key ON/OFF toggle:
  Dynamically set the alpha property on glvideomixer's sink_1 pad (cefsrc input).
  0.0 = key OFF (transparent overlay), 1.0 = key ON (fully visible overlay).
  The pipeline is NEVER rebuilt to toggle the key state — only the pad property changes.

Pipeline rebuilds happen only when the MXL receiver connection status changes
(new flow_id or disconnect).
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

# CEF renderer URL served by the SPX graphics server.
# Uses the Docker Compose service name so cefsrc can reach spx-server
# across the internal container network without any extra port mapping.
_CEF_URL = os.getenv("SPX_URL", "http://spx-server:5660/renderer/")

# Video format for MXL output (1080p60, v210)
_OUTPUT_CAPS = "video/x-raw,format=v210,width=1920,height=1080,framerate=60/1"

# CEF output caps — BGRA preserves the alpha channel for keying
_CEF_CAPS = "video/x-raw,format=BGRA,width=1920,height=1080,framerate=60/1"


class GstKeyer:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pipeline: Gst.Pipeline | None = None
        self._mixer_elem: Gst.Element | None = None
        self._cef_pad: Gst.Pad | None = None  # glvideomixer sink_1 (cefsrc)

        self._mxl_domain = os.getenv("MXL_DOMAIN", "/mxl-domain")
        self._output_flow_id: str | None = None
        self._input_flow_id: str | None = None  # None = no receiver connected

        self._key_enabled = False  # Key starts OFF by default

        self._glib_loop = GLib.MainLoop()
        threading.Thread(target=self._glib_loop.run, name="glib-main", daemon=True).start()

    # ── Public API ────────────────────────────────────────────────────────────

    def set_output_flow_id(self, flow_id: str) -> None:
        """Called once at startup after NMOS sender discovery."""
        with self._lock:
            self._output_flow_id = flow_id
            log.info("Output flow ID set: %s", flow_id)
            self._rebuild_pipeline()

    def connect_input(self, flow_id: str) -> None:
        """Called by NMOS bridge when the receiver IS-05 activation arrives."""
        with self._lock:
            log.info("Input connected → flow %s", flow_id)
            self._input_flow_id = flow_id
            self._rebuild_pipeline()

    def disconnect_input(self) -> None:
        """Called by NMOS bridge when the receiver IS-05 deactivation arrives."""
        with self._lock:
            log.info("Input disconnected")
            self._input_flow_id = None
            self._rebuild_pipeline()

    def set_key_state(self, enabled: bool) -> bool:
        """
        Toggle the key ON or OFF by adjusting the cefsrc pad alpha on glvideomixer.
        Does NOT rebuild the pipeline — seamless 1080p60 operation.
        Returns the new key state.
        """
        with self._lock:
            self._key_enabled = enabled
            self._apply_key_alpha()
            log.info("Key state → %s", "ON" if enabled else "OFF")
            return self._key_enabled

    def get_status(self) -> dict:
        with self._lock:
            return {
                "key_enabled":      self._key_enabled,
                "input_connected":  self._input_flow_id is not None,
                "input_flow_id":    self._input_flow_id,
                "output_flow_id":   self._output_flow_id,
                "pipeline_running": self._pipeline is not None,
            }

    # ── Alpha control ─────────────────────────────────────────────────────────

    def _apply_key_alpha(self) -> None:
        """Set the alpha on glvideomixer's cefsrc input pad. Caller must hold lock."""
        if self._cef_pad is None:
            return
        alpha = 1.0 if self._key_enabled else 0.0
        self._cef_pad.set_property("alpha", alpha)
        log.debug("glvideomixer sink_1 alpha → %.1f", alpha)

    # ── Pipeline lifecycle ────────────────────────────────────────────────────

    def _teardown(self) -> None:
        """Stop and release the current pipeline. Caller must hold lock."""
        if self._pipeline is None:
            return
        log.info("Tearing down pipeline…")
        self._pipeline.set_state(Gst.State.NULL)
        self._pipeline.get_state(5 * Gst.SECOND)
        self._pipeline   = None
        self._mixer_elem = None
        self._cef_pad    = None
        gc.collect()
        log.info("Pipeline teardown complete")

    def _cleanup_flow_dir(self) -> None:
        """Remove the output MXL flow directory so mxlsink can start fresh."""
        if not self._output_flow_id:
            return
        path = os.path.join(self._mxl_domain, f"{self._output_flow_id}.mxl-flow")
        if os.path.exists(path):
            try:
                shutil.rmtree(path)
                log.info("Removed stale output flow dir: %s", self._output_flow_id)
            except Exception as exc:
                log.warning("Could not remove flow dir: %s", exc)

    def _rebuild_pipeline(self) -> None:
        """Tear down old pipeline and build a fresh one. Caller must hold lock."""
        if not self._output_flow_id:
            log.info("Skipping rebuild: output flow ID not yet known")
            return
        self._teardown()
        self._cleanup_flow_dir()
        try:
            self._build_pipeline()
        except Exception as exc:
            log.error("Failed to build pipeline: %s", exc)

    def _build_pipeline(self) -> None:
        """Construct and start the GStreamer pipeline. Caller must hold lock."""
        domain   = self._mxl_domain
        out_flow = self._output_flow_id

        log.info(
            "Building pipeline  key=%s  input=%s",
            "ON" if self._key_enabled else "OFF",
            self._input_flow_id or "black",
        )

        pipeline = Gst.Pipeline.new("html5-keyer")

        # ── Background branch ─────────────────────────────────────────────────
        if self._input_flow_id:
            bg_src = Gst.ElementFactory.make("mxlsrc", "bg_src")
            if not bg_src:
                raise RuntimeError("Could not create mxlsrc element")
            bg_src.set_property("video-flow-id", self._input_flow_id)
            bg_src.set_property("domain", domain)
            log.info("  background: mxlsrc flow=%s", self._input_flow_id)
        else:
            bg_src = Gst.ElementFactory.make("videotestsrc", "bg_src")
            if not bg_src:
                raise RuntimeError("Could not create videotestsrc element")
            bg_src.set_property("pattern", 2)   # black
            bg_src.set_property("is-live", True)
            log.info("  background: videotestsrc (black)")

        # videoconvert bridges mxlsrc/videotestsrc output to a format
        # glvideomixer's sink pad can negotiate (glupload is not auto-inserted
        # in a manual pipeline without an explicit converter upstream).
        bg_conv   = Gst.ElementFactory.make("videoconvert", "bg_conv")
        bg_upload = Gst.ElementFactory.make("glupload",     "bg_upload")
        # No leaking on the background queue — every mxlsrc frame is unique.
        # leaky=2 was dropping frames when the GPU was slightly late, causing chop.
        bg_queue = Gst.ElementFactory.make("queue", "bg_queue")
        bg_queue.set_property("leaky", 0)
        bg_queue.set_property("max-size-buffers", 4)
        bg_queue.set_property("max-size-time", 0)
        bg_queue.set_property("max-size-bytes", 0)

        # ── CEF overlay branch ────────────────────────────────────────────────
        cef_src = Gst.ElementFactory.make("cefsrc", "cef_src")
        if not cef_src:
            raise RuntimeError("Could not create cefsrc element — is gst-cef plugin loaded?")
        cef_src.set_property("url", _CEF_URL)

        cef_caps_filter = Gst.ElementFactory.make("capsfilter", "cef_caps")
        cef_caps_filter.set_property(
            "caps", Gst.Caps.from_string(_CEF_CAPS)
        )

        # videorate ensures compositor always receives frames at exactly 60fps
        # by duplicating the last rendered graphic when CEF renders slower.
        cef_rate   = Gst.ElementFactory.make("videorate", "cef_rate")
        cef_upload = Gst.ElementFactory.make("glupload",  "cef_upload")

        cef_queue = Gst.ElementFactory.make("queue", "cef_queue")
        cef_queue.set_property("leaky", 2)           # drop oldest, keep newest
        cef_queue.set_property("max-size-buffers", 2)
        cef_queue.set_property("max-size-time", 0)
        cef_queue.set_property("max-size-bytes", 0)

        # ── Mixer ─────────────────────────────────────────────────────────────────
        mixer = Gst.ElementFactory.make("glvideomixer", "mixer")
        if not mixer:
            raise RuntimeError("Could not create glvideomixer element")

        # ── Output chain ──────────────────────────────────────────────────────────
        download  = Gst.ElementFactory.make("gldownload",   "download")
        out_conv  = Gst.ElementFactory.make("videoconvert", "out_conv")
        out_caps  = Gst.ElementFactory.make("capsfilter",   "out_caps")
        out_caps.set_property("caps", Gst.Caps.from_string(_OUTPUT_CAPS))
        out_queue = Gst.ElementFactory.make("queue",    "out_queue")
        out_queue.set_property("leaky", 2)
        out_queue.set_property("max-size-buffers", 2)
        out_queue.set_property("max-size-time", 0)
        out_queue.set_property("max-size-bytes", 0)
        out_sink  = Gst.ElementFactory.make("mxlsink",  "out_sink")
        if not out_sink:
            raise RuntimeError("Could not create mxlsink element")
        out_sink.set_property("flow-id", out_flow)
        out_sink.set_property("domain",  domain)
        out_sink.set_property("sync",    False)

        for el in (
            bg_src, bg_conv, bg_upload, bg_queue,
            cef_src, cef_caps_filter, cef_rate, cef_upload, cef_queue,
            mixer, download, out_conv, out_caps, out_queue, out_sink,
        ):
            pipeline.add(el)

        # ── Link background branch → mixer sink_0 ────────────────────────────
        bg_src.link(bg_conv)
        bg_conv.link(bg_upload)
        bg_upload.link(bg_queue)
        mixer_sink_0 = mixer.get_request_pad("sink_%u")
        bg_queue.get_static_pad("src").link(mixer_sink_0)
        log.info("  background → mixer pad %s", mixer_sink_0.get_name())

        # ── Link CEF branch → mixer sink_1 ───────────────────────────────────
        # videorate ensures glvideomixer always receives frames at exactly 60fps
        # by duplicating the last rendered CEF graphic when Chrome renders slower.
        cef_src.link(cef_caps_filter)
        cef_caps_filter.link(cef_rate)
        cef_rate.link(cef_upload)
        cef_upload.link(cef_queue)
        mixer_sink_1 = mixer.get_request_pad("sink_%u")
        cef_queue.get_static_pad("src").link(mixer_sink_1)
        log.info("  cef overlay → mixer pad %s", mixer_sink_1.get_name())

        # ── Link output chain ─────────────────────────────────────────────────
        mixer.link(download)
        download.link(out_conv)
        out_conv.link(out_caps)
        out_caps.link(out_queue)
        out_queue.link(out_sink)

        # ── Bus ───────────────────────────────────────────────────────────────
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_error)
        bus.connect("message::eos",   self._on_eos)

        ret = pipeline.set_state(Gst.State.PLAYING)
        log.info("Pipeline set_state(PLAYING) → %s", ret)

        self._pipeline   = pipeline
        self._mixer_elem = mixer
        self._cef_pad    = mixer_sink_1

        # Restore the current key alpha without rebuilding
        self._apply_key_alpha()
        self._patch_flow_def()

    def _patch_flow_def(self) -> None:
        """Patch flow_def.json label/description (mxlsink writes it asynchronously)."""
        if not self._output_flow_id:
            return
        flow_id = self._output_flow_id
        path = os.path.join(self._mxl_domain, f"{flow_id}.mxl-flow", "flow_def.json")

        def _try_patch() -> None:
            import time
            for attempt in range(20):
                try:
                    with open(path) as f:
                        data = json.load(f)
                    data["label"]       = "HTML5 Keyer – Output"
                    data["description"] = "MXL HTML5 Keyer composited video output"
                    with open(path, "w") as f:
                        json.dump(data, f, indent=2)
                    log.info("Patched flow_def.json (attempt %d)", attempt + 1)
                    return
                except FileNotFoundError:
                    time.sleep(0.5)
                except Exception as exc:
                    log.warning("Could not patch flow_def.json: %s", exc)
                    return
            log.warning("flow_def.json never appeared — patch skipped")

        threading.Thread(target=_try_patch, daemon=True).start()

    # ── Bus callbacks ─────────────────────────────────────────────────────────

    def _on_error(self, _bus: Gst.Bus, msg: Gst.Message) -> None:
        err, debug = msg.parse_error()
        log.error("GStreamer error: %s  debug: %s", err, debug)

    def _on_eos(self, _bus: Gst.Bus, _msg: Gst.Message) -> None:
        log.info("GStreamer end of stream")
