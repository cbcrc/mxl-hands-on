"""
GStreamer input selector for the MXL Input Selector.

Architecture:
  - 4 branches feed an input-selector element:
      branch 0 : always-black videotestsrc  (used when active_input is None)
      branch 1 : slot 1  – mxlsrc(flow_id) if connected, else black videotestsrc
      branch 2 : slot 2  – mxlsrc(flow_id) if connected, else black videotestsrc
      branch 3 : slot 3  – mxlsrc(flow_id) if connected, else black videotestsrc
  - input-selector.src → videoconvert → capsfilter(v210) → queue → mxlsink

Two-step selection:
  select_input(N)  – pre-select (highlights button in UI, no output change)
  take()           – switch output to the pre-selected input

At startup: active_input=None (output is black, branch 0 active).
The pipeline is rebuilt whenever a slot's connection status changes.
The active pad is restored after each rebuild.
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

_VIDEO_CAPS  = "video/x-raw,width=1920,height=1080,framerate=60/1"
_OUTPUT_CAPS = "video/x-raw,format=v210,width=1920,height=1080,framerate=60/1"


class GstSelector:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pipeline: Gst.Pipeline | None = None
        self._selector_elem: Gst.Element | None = None
        self._mxl_domain = os.getenv("MXL_DOMAIN", "/mxl-domain")
        self._output_flow_id: str | None = None

        # Slots 1-3: flow_id string when connected, None when not connected
        self._slots: dict[int, str | None] = {1: None, 2: None, 3: None}
        self._selected_input: int | None = None  # pre-selected (UI highlight)
        self._active_input: int | None = None    # currently at output (None = black)

        self._glib_loop = GLib.MainLoop()
        threading.Thread(target=self._glib_loop.run, daemon=True).start()

    # ── Public API ────────────────────────────────────────────────────────────

    def set_output_flow_id(self, flow_id: str) -> None:
        with self._lock:
            self._output_flow_id = flow_id
            log.info("Output flow ID set: %s", flow_id)
            self._rebuild_pipeline()

    def connect_input(self, slot: int, flow_id: str) -> None:
        """Called by NMOS bridge when a receiver IS-05 activation arrives."""
        with self._lock:
            log.info("Slot %d connected → flow %s", slot, flow_id)
            self._slots[slot] = flow_id
            self._rebuild_pipeline()

    def disconnect_input(self, slot: int) -> None:
        """Called by NMOS bridge when a receiver IS-05 deactivation arrives."""
        with self._lock:
            log.info("Slot %d disconnected", slot)
            self._slots[slot] = None
            # If the active output was this slot, fall back to black
            if self._active_input == slot:
                self._active_input = None
            self._rebuild_pipeline()

    def select_input(self, slot: int) -> None:
        """Pre-select an input (does not change output until take() is called)."""
        with self._lock:
            log.info("Pre-selected input: slot %d", slot)
            self._selected_input = slot

    def take(self) -> None:
        """Switch output to the pre-selected input."""
        with self._lock:
            if self._selected_input is None:
                log.warning("Take: no input pre-selected")
                return
            self._active_input = self._selected_input
            self._apply_active_pad()
            log.info("Take: output → slot %d", self._active_input)

    def get_status(self) -> dict:
        with self._lock:
            return {
                "selected_input": self._selected_input,
                "active_input":   self._active_input,
                "slots": {
                    str(k): {"connected": v is not None, "flow_id": v}
                    for k, v in self._slots.items()
                },
                "output_flow_id":  self._output_flow_id,
                "pipeline_running": self._pipeline is not None,
            }

    # ── Pipeline ─────────────────────────────────────────────────────────────

    def _teardown(self) -> None:
        """Stop and release the current pipeline. Caller must hold self._lock."""
        if self._pipeline is None:
            return
        log.info("Tearing down pipeline…")
        self._pipeline.set_state(Gst.State.NULL)
        self._pipeline.get_state(5 * Gst.SECOND)
        self._pipeline = None
        self._selector_elem = None
        gc.collect()
        log.info("Teardown complete")

    def _cleanup_flow_dir(self) -> None:
        """Remove the output flow directory so mxlsink can start fresh."""
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
        """Tear down the old pipeline and start a fresh one. Caller must hold lock."""
        if not self._output_flow_id:
            log.warning("Skipping pipeline build: output flow ID not yet known")
            return
        self._teardown()
        self._cleanup_flow_dir()
        try:
            self._build_pipeline()
        except Exception as exc:
            log.error("Failed to build pipeline: %s", exc)

    def _build_pipeline(self) -> None:
        """Construct and start the GStreamer pipeline. Caller must hold self._lock."""
        domain   = self._mxl_domain
        out_flow = self._output_flow_id

        pipeline = Gst.Pipeline.new("input-selector")
        selector = Gst.ElementFactory.make("input-selector", "selector")
        if not selector:
            raise RuntimeError("Could not create input-selector element")
        pipeline.add(selector)

        def _make_branch(branch_idx: int, flow_id: str | None) -> None:
            """Build one input branch and link it to selector.sink_{branch_idx}."""
            tag = f"b{branch_idx}"
            if flow_id:
                src = Gst.ElementFactory.make("mxlsrc", f"src_{tag}")
                src.set_property("flow-id", flow_id)
                src.set_property("domain",  domain)
                src.set_property("sync",    False)
            else:
                src = Gst.ElementFactory.make("videotestsrc", f"src_{tag}")
                src.set_property("pattern", 2)      # black
                src.set_property("is-live", True)

            conv  = Gst.ElementFactory.make("videoconvert", f"conv_{tag}")
            scale = Gst.ElementFactory.make("videoscale",   f"scale_{tag}")
            caps  = Gst.ElementFactory.make("capsfilter",   f"caps_{tag}")
            caps.set_property("caps", Gst.Caps.from_string(_VIDEO_CAPS))
            queue = Gst.ElementFactory.make("queue",        f"q_{tag}")

            for el in (src, conv, scale, caps, queue):
                pipeline.add(el)

            src.link(conv)
            conv.link(scale)
            scale.link(caps)
            caps.link(queue)

            sel_sink = selector.get_request_pad(f"sink_{branch_idx}")
            queue.get_static_pad("src").link(sel_sink)

        # Branch 0: permanent black (output when active_input is None)
        _make_branch(0, None)
        # Branches 1-3: the three input slots
        for slot in (1, 2, 3):
            _make_branch(slot, self._slots.get(slot))

        # ── Output branch ─────────────────────────────────────────────────────
        out_conv  = Gst.ElementFactory.make("videoconvert", "out_conv")
        out_caps  = Gst.ElementFactory.make("capsfilter",   "out_caps")
        out_caps.set_property("caps", Gst.Caps.from_string(_OUTPUT_CAPS))
        out_queue = Gst.ElementFactory.make("queue",        "out_queue")
        out_sink  = Gst.ElementFactory.make("mxlsink",      "out_sink")
        out_sink.set_property("flow-id", out_flow)
        out_sink.set_property("domain",  domain)
        out_sink.set_property("sync",    False)

        for el in (out_conv, out_caps, out_queue, out_sink):
            pipeline.add(el)
        selector.link(out_conv)
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

        self._pipeline     = pipeline
        self._selector_elem = selector

        self._apply_active_pad()
        self._patch_flow_def()

    def _apply_active_pad(self) -> None:
        """Point the selector at the correct branch. Caller must hold self._lock."""
        if self._selector_elem is None:
            return
        # None → branch 0 (permanent black)
        idx = self._active_input if self._active_input is not None else 0
        pad = self._selector_elem.get_static_pad(f"sink_{idx}")
        if pad:
            self._selector_elem.set_property("active-pad", pad)
            log.info("Selector active-pad → sink_%d", idx)
        else:
            log.warning("Could not find selector pad sink_%d", idx)

    def _patch_flow_def(self) -> None:
        if not self._output_flow_id:
            return
        path = os.path.join(
            self._mxl_domain,
            f"{self._output_flow_id}.mxl-flow",
            "flow_def.json",
        )
        try:
            with open(path) as f:
                data = json.load(f)
            data["label"]       = "Input Selector – Output"
            data["description"] = "MXL Input Selector video output"
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
            log.info("Patched flow_def.json for output")
        except Exception as exc:
            log.warning("Could not patch flow_def.json: %s", exc)

    def _on_error(self, _bus: Gst.Bus, msg: Gst.Message) -> None:
        err, debug = msg.parse_error()
        log.error("GStreamer error: %s  debug: %s", err, debug)

    def _on_eos(self, _bus: Gst.Bus, _msg: Gst.Message) -> None:
        log.info("End of stream")
