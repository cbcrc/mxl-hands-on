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

# Only constrain the pixel format so all branches are compatible with input-selector.
# Do NOT specify framerate/resolution — these are dictated by each source (mxlsrc reads
# them from the ring buffer; over-constraining causes caps renegotiation failure when
# input-selector switches the active pad and sends a RECONFIGURE upstream).
_VIDEO_CAPS = "video/x-raw,format=v210"


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
        self._slot_to_pad: dict[int, str] = {}   # slot → actual pad name assigned by input-selector

        self._glib_loop = GLib.MainLoop()
        threading.Thread(target=self._glib_loop.run, daemon=True).start()

    # ── Public API ────────────────────────────────────────────────────────────

    def set_output_flow_id(self, flow_id: str) -> None:
        with self._lock:
            self._output_flow_id = flow_id
            print(f"[GstSelector] Output flow ID set: {flow_id}", flush=True)
            self._rebuild_pipeline()

    def connect_input(self, slot: int, flow_id: str) -> None:
        """Called by NMOS bridge when a receiver IS-05 activation arrives."""
        with self._lock:
            print(f"[GstSelector] Slot {slot} connected → flow {flow_id}", flush=True)
            self._slots[slot] = flow_id
            self._rebuild_pipeline()

    def disconnect_input(self, slot: int) -> None:
        """Called by NMOS bridge when a receiver IS-05 deactivation arrives."""
        with self._lock:
            print(f"[GstSelector] Slot {slot} disconnected", flush=True)
            self._slots[slot] = None
            if self._active_input == slot:
                self._active_input = None
            self._rebuild_pipeline()

    def select_input(self, slot: int) -> None:
        """Pre-select an input (does not change output until take() is called)."""
        with self._lock:
            print(f"[GstSelector] Pre-selected input: slot {slot}", flush=True)
            self._selected_input = slot

    def take(self) -> None:
        """Switch output to the pre-selected input."""
        with self._lock:
            if self._selected_input is None:
                print("[GstSelector] Take: no input pre-selected", flush=True)
                return
            self._active_input = self._selected_input
            print(f"[GstSelector] Take: switching to slot {self._active_input}", flush=True)
            self._apply_active_pad()

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
        print("[GstSelector] Tearing down pipeline…", flush=True)
        self._pipeline.set_state(Gst.State.NULL)
        self._pipeline.get_state(5 * Gst.SECOND)
        self._pipeline     = None
        self._selector_elem = None
        self._slot_to_pad   = {}
        gc.collect()
        print("[GstSelector] Teardown complete", flush=True)

    def _cleanup_flow_dir(self) -> None:
        """Remove the output flow directory so mxlsink can start fresh."""
        if not self._output_flow_id:
            return
        path = os.path.join(self._mxl_domain, f"{self._output_flow_id}.mxl-flow")
        if os.path.exists(path):
            try:
                shutil.rmtree(path)
                print(f"[GstSelector] Removed stale output flow dir: {self._output_flow_id}", flush=True)
            except Exception as exc:
                print(f"[GstSelector] Could not remove flow dir: {exc}", flush=True)

    def _rebuild_pipeline(self) -> None:
        """Tear down the old pipeline and start a fresh one. Caller must hold lock."""
        if not self._output_flow_id:
            print("[GstSelector] Skipping rebuild: output flow ID not yet known", flush=True)
            return
        self._teardown()
        self._cleanup_flow_dir()
        try:
            self._build_pipeline()
        except Exception as exc:
            print(f"[GstSelector] Failed to build pipeline: {exc}", flush=True)

    def _build_pipeline(self) -> None:
        """Construct and start the GStreamer pipeline. Caller must hold self._lock."""
        domain   = self._mxl_domain
        out_flow = self._output_flow_id

        connected = {slot: fid for slot, fid in self._slots.items() if fid}
        print(f"[GstSelector] Building pipeline active_input={self._active_input} "
              f"connected={list(connected.keys())}", flush=True)

        pipeline = Gst.Pipeline.new("input-selector")
        selector = Gst.ElementFactory.make("input-selector", "selector")
        if not selector:
            raise RuntimeError("Could not create input-selector element")
        selector.set_property("sync-streams", False)
        pipeline.add(selector)

        def _make_branch(slot: int, flow_id: str) -> None:
            """Build one mxlsrc branch and link it to an input-selector sink pad."""
            tag = f"b{slot}"
            src = Gst.ElementFactory.make("mxlsrc", f"src_{tag}")
            src.set_property("video-flow-id", flow_id)
            src.set_property("domain",        domain)
            print(f"[GstSelector]   branch {slot}: mxlsrc flow={flow_id}", flush=True)

            conv  = Gst.ElementFactory.make("videoconvert", f"conv_{tag}")
            queue = Gst.ElementFactory.make("queue",        f"q_{tag}")
            queue.set_property("leaky", 1)            # drop oldest on overflow
            queue.set_property("max-size-buffers", 3)

            for el in (src, conv, queue):
                pipeline.add(el)

            src.link(conv)
            conv.link(queue)

            sel_sink = selector.get_request_pad("sink_%u")
            pad_name = sel_sink.get_name() if sel_sink else "NONE"
            self._slot_to_pad[slot] = pad_name
            print(f"[GstSelector]   slot {slot} → pad {pad_name}", flush=True)
            queue.get_static_pad("src").link(sel_sink)

        if not connected:
            # No connected slots yet: emit black via a simple videotestsrc (no selector)
            print("[GstSelector]   no connected slots — using black videotestsrc", flush=True)
            src = Gst.ElementFactory.make("videotestsrc", "src_black")
            src.set_property("pattern", 2)
            src.set_property("is-live", True)
            pipeline.add(src)
            # Link directly into the output chain below (reuse selector as pass-through)
            # Actually easier: link src directly, bypassing the selector entirely
            pipeline.remove(selector)
            selector = None

            out_sink = Gst.ElementFactory.make("mxlsink", "out_sink")
            out_sink.set_property("flow-id", out_flow)
            out_sink.set_property("domain",  domain)
            out_sink.set_property("sync",    False)
            out_conv = Gst.ElementFactory.make("videoconvert", "out_conv")
            for el in (out_conv, out_sink):
                pipeline.add(el)
            src.link(out_conv)
            out_conv.link(out_sink)
        else:
            # All connected branches are mxlsrc — identical caps, seamless switching
            for slot, fid in connected.items():
                _make_branch(slot, fid)

            out_conv  = Gst.ElementFactory.make("videoconvert", "out_conv")
            out_queue = Gst.ElementFactory.make("queue",        "out_queue")
            out_sink  = Gst.ElementFactory.make("mxlsink",      "out_sink")
            out_sink.set_property("flow-id", out_flow)
            out_sink.set_property("domain",  domain)
            out_sink.set_property("sync",    False)

            for el in (out_conv, out_queue, out_sink):
                pipeline.add(el)
            selector.link(out_conv)
            out_conv.link(out_queue)
            out_queue.link(out_sink)

        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_error)
        bus.connect("message::eos",   self._on_eos)

        ret = pipeline.set_state(Gst.State.PLAYING)
        print(f"[GstSelector] Pipeline set_state(PLAYING) → {ret}", flush=True)

        self._pipeline      = pipeline
        self._selector_elem = selector
        # _slot_to_pad was populated by _make_branch calls above — do NOT reset here

        self._apply_active_pad()
        self._patch_flow_def()

    def _apply_active_pad(self) -> None:
        """Point the selector at the correct branch. Caller must hold self._lock."""
        if self._selector_elem is None:
            return  # no selector (black/no-slots pipeline)
        connected = {slot for slot, fid in self._slots.items() if fid}
        idx = self._active_input if self._active_input in connected else None
        if idx is None:
            idx = min(connected) if connected else None
        if idx is None:
            return
        pad_name = self._slot_to_pad.get(idx)
        if not pad_name:
            print(f"[GstSelector] _apply_active_pad: no pad mapping for slot {idx}", flush=True)
            return
        pad = self._selector_elem.get_static_pad(pad_name)
        if pad is None:
            print(f"[GstSelector] _apply_active_pad: pad {pad_name} NOT FOUND", flush=True)
            return
        self._selector_elem.set_property("active-pad", pad)
        current = self._selector_elem.get_property("active-pad")
        current_name = current.get_name() if current else "None"
        print(f"[GstSelector] active-pad set to slot {idx} ({pad_name}), readback={current_name}", flush=True)

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
            print(f"[GstSelector] Patched flow_def.json for output", flush=True)
        except Exception as exc:
            print(f"[GstSelector] Could not patch flow_def.json: {exc}", flush=True)

    def _on_error(self, _bus: Gst.Bus, msg: Gst.Message) -> None:
        err, debug = msg.parse_error()
        print(f"[GstSelector] GStreamer error: {err}  debug: {debug}", flush=True)

    def _on_eos(self, _bus: Gst.Bus, _msg: Gst.Message) -> None:
        print("[GstSelector] End of stream", flush=True)
