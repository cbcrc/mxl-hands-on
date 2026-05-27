"""
GStreamer MXL Input Selector.

Pipeline (user-controlled start/stop):
  Three input branches feed into a single `input-selector` element whose
  source pad drives one `mxlsink`.  Each input branch is either:
    - a real MXL flow:   mxlsrc(video-flow-id) → capsfilter(v210)
                         → queue → input-selector.sink_N
    - a black-fill slot: videotestsrc pattern=black is-live=true
                         → capsfilter(v210, W, H, fps) → queue → input-selector.sink_N

Output: input-selector.src → capsfilter(v210) → queue → mxlsink(flow-id, domain)

Live switching is performed by changing the input-selector "active-pad" property.
All three branches stay in PLAYING at all times so switching is seamless.

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

# Fixed namespace for UUID v5 derivation. Treat as immutable — changing it
# would orphan all previously written flow directories on every domain.
_MXL_SELECTOR_NS = uuid.UUID("c4b18e9f-2d31-5a4f-8e6b-7c9d0a1b2c3d")

NUM_INPUTS = 3


# ── Flow format helpers ───────────────────────────────────────────────────────


def read_flow_format(domain_path: str, flow_uuid: str) -> dict:
    """Load flow_def.json and return the fields used for format validation."""
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
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise KeyError(f"flow_def.json missing required field: {exc}") from exc


def _fmt_slot_summary(fmt: Optional[dict]) -> str:
    if fmt is None:
        return "—"
    gr = fmt["grain_rate"]
    return f'{fmt["frame_width"]}x{fmt["frame_height"]} @ {gr["numerator"]}/{gr["denominator"]} {fmt["interlace_mode"]}'


def validate_inputs(
    domain_path: str, input_flow_uuids: list[Optional[str]]
) -> tuple[Optional[dict], list[str], list[Optional[dict]]]:
    """
    Read flow_def.json for each non-None UUID and verify that all selected
    inputs share the same frame_width, frame_height, grain_rate, interlace_mode.

    Returns (common_format, errors, per_slot_formats).
      common_format     — format dict of the first non-None input, or None.
      errors            — list of human-readable mismatch descriptions.
      per_slot_formats  — list of length NUM_INPUTS, each either a format dict or None.
    """
    per_slot: list[Optional[dict]] = [None] * NUM_INPUTS
    errors: list[str] = []

    for idx, flow_uuid in enumerate(input_flow_uuids):
        if not flow_uuid:
            continue
        try:
            per_slot[idx] = read_flow_format(domain_path, flow_uuid)
        except (FileNotFoundError, KeyError) as exc:
            errors.append(f"Input {idx + 1}: could not read flow format — {exc}")

    selected = [(i, f) for i, f in enumerate(per_slot) if f is not None]
    if not selected:
        return None, errors, per_slot

    ref_idx, ref_fmt = selected[0]
    for idx, fmt in selected[1:]:
        diffs = []
        if fmt["frame_width"] != ref_fmt["frame_width"] or fmt["frame_height"] != ref_fmt["frame_height"]:
            diffs.append(
                f'raster {fmt["frame_width"]}x{fmt["frame_height"]} ≠ '
                f'{ref_fmt["frame_width"]}x{ref_fmt["frame_height"]}'
            )
        if fmt["grain_rate"] != ref_fmt["grain_rate"]:
            diffs.append(
                f'grain_rate {fmt["grain_rate"]["numerator"]}/{fmt["grain_rate"]["denominator"]} ≠ '
                f'{ref_fmt["grain_rate"]["numerator"]}/{ref_fmt["grain_rate"]["denominator"]}'
            )
        if fmt["interlace_mode"] != ref_fmt["interlace_mode"]:
            diffs.append(f'interlace_mode {fmt["interlace_mode"]} ≠ {ref_fmt["interlace_mode"]}')
        if diffs:
            errors.append(
                f"Input {idx + 1} differs from Input {ref_idx + 1}: " + ", ".join(diffs)
            )

    return (ref_fmt if not errors else None), errors, per_slot


def _interlace_mode_to_caps(mode: str) -> str:
    """Map MXL interlace_mode string to GStreamer interlace-mode caps value."""
    m = (mode or "progressive").lower()
    if m == "progressive":
        return "progressive"
    return "interleaved"  # interlaced_tff / interlaced_bff


# ── Pipeline ──────────────────────────────────────────────────────────────────


class GstSelector:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pipeline: Optional[Gst.Pipeline] = None
        self._selector: Optional[Gst.Element] = None
        self._sink_pads: dict[int, Gst.Pad] = {}  # slot index → request pad on input-selector
        self._slot_kinds: list[str] = []          # "mxl" or "black" per slot
        self._running = False
        self._error_msg: Optional[str] = None
        self._gen = 0

        self._domain_path: Optional[str] = None
        self._input_flow_uuids: list[Optional[str]] = [None] * NUM_INPUTS
        self._output_flow_uuid: Optional[str] = None
        self._common_format: Optional[dict] = None
        self._grouphint: Optional[str] = None
        self._description: Optional[str] = None
        self._label: Optional[str] = None
        self._active_input: int = 0

        self._glib_loop = GLib.MainLoop()
        threading.Thread(target=self._glib_loop.run, daemon=True).start()

    # ── Public API ────────────────────────────────────────────────────────────

    def start(
        self,
        domain_path: str,
        input_flow_uuids: list[Optional[str]],
        grouphint: str,
        description: str,
        label: str,
    ) -> dict:
        """
        Build and start the pipeline.  Raises ValueError on bad config or
        mismatched input formats.  The ValueError's .args[0] is a dict with
        keys "detail", "errors", "per_slot" suitable for direct serialisation.
        """
        if len(input_flow_uuids) != NUM_INPUTS:
            raise ValueError({"detail": f"expected {NUM_INPUTS} input slots", "errors": [], "per_slot": []})

        common, errors, per_slot = validate_inputs(domain_path, input_flow_uuids)
        if errors or common is None:
            per_slot_strings = [_fmt_slot_summary(f) for f in per_slot]
            if common is None and not errors:
                errors = ["At least one MXL input flow must be selected (cannot derive output format from black-only inputs)."]
            raise ValueError({
                "detail":   "Input formats do not match",
                "errors":   errors,
                "per_slot": per_slot_strings,
            })

        with self._lock:
            self._teardown()
            self._gen += 1
            gen = self._gen
            self._domain_path      = domain_path
            self._input_flow_uuids = list(input_flow_uuids)
            self._common_format    = common
            self._grouphint        = grouphint
            self._description      = description
            self._label            = label
            self._active_input     = 0
            self._output_flow_uuid = str(
                uuid.uuid5(_MXL_SELECTOR_NS, f"{grouphint}:video")
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

    def stop(self) -> None:
        with self._lock:
            self._teardown()
            self._input_flow_uuids = [None] * NUM_INPUTS
            self._common_format    = None
            self._output_flow_uuid = None
            self._domain_path      = None
            self._grouphint        = None
            self._description      = None
            self._label            = None

    def set_active_input(self, slot: int) -> None:
        with self._lock:
            if not self._running or self._selector is None:
                raise RuntimeError("Pipeline is not running")
            if slot < 0 or slot >= NUM_INPUTS:
                raise ValueError(f"slot must be in 0..{NUM_INPUTS - 1}")
            pad = self._sink_pads.get(slot)
            if pad is None:
                raise RuntimeError(f"No sink pad for slot {slot}")
            self._selector.set_property("active-pad", pad)
            self._active_input = slot
            log.info("Active input switched to slot %d (%s)", slot, self._slot_kinds[slot])

    def get_status(self) -> dict:
        with self._lock:
            return {
                "running":           self._running,
                "domain_path":       self._domain_path,
                "input_flow_uuids":  list(self._input_flow_uuids),
                "slot_kinds":        list(self._slot_kinds) if self._slot_kinds else [
                    ("mxl" if u else "black") for u in self._input_flow_uuids
                ],
                "active_input":      self._active_input if self._running else None,
                "output_flow_uuid":  self._output_flow_uuid,
                "format":            self._common_format,
                "grouphint":         self._grouphint,
                "description":       self._description,
                "label":             self._label,
                "error":             self._error_msg,
            }

    # ── Pipeline construction ─────────────────────────────────────────────────

    def _build_pipeline(self) -> None:
        assert self._common_format is not None
        assert self._output_flow_uuid is not None
        assert self._domain_path is not None

        fmt = self._common_format
        pipeline = Gst.Pipeline.new("input-selector-pipeline")

        selector = Gst.ElementFactory.make("input-selector", "selector")
        if not selector:
            raise RuntimeError(
                "Could not create input-selector — is gstreamer1.0-plugins-bad installed?"
            )
        pipeline.add(selector)

        # Output branch: selector.src → capsfilter(v210) → queue → mxlsink
        out_caps  = Gst.ElementFactory.make("capsfilter", "out_caps")
        out_caps.set_property("caps", Gst.Caps.from_string("video/x-raw,format=v210"))
        out_queue = Gst.ElementFactory.make("queue", "out_queue")
        out_sink  = Gst.ElementFactory.make("mxlsink", "out_sink")
        if not out_sink:
            raise RuntimeError(
                "Could not create mxlsink — is libgstmxl.so installed in the plugin path?"
            )
        out_sink.set_property("flow-id", self._output_flow_uuid)
        out_sink.set_property("domain", self._domain_path)
        for el in (out_caps, out_queue, out_sink):
            pipeline.add(el)
        if not selector.link(out_caps):
            raise RuntimeError("Failed to link input-selector → out_caps")
        if not out_caps.link(out_queue):
            raise RuntimeError("Failed to link out_caps → out_queue")
        if not out_queue.link(out_sink):
            raise RuntimeError("Failed to link out_queue → mxlsink")

        # Input branches
        self._sink_pads = {}
        self._slot_kinds = []
        for idx in range(NUM_INPUTS):
            flow_uuid = self._input_flow_uuids[idx]
            sink_pad = selector.request_pad_simple("sink_%u")
            if sink_pad is None:
                raise RuntimeError(f"Could not request sink pad on input-selector for slot {idx}")
            self._sink_pads[idx] = sink_pad
            if flow_uuid:
                self._build_mxl_input(pipeline, idx, flow_uuid, sink_pad)
                self._slot_kinds.append("mxl")
            else:
                self._build_black_input(pipeline, idx, fmt, sink_pad)
                self._slot_kinds.append("black")

        # Initial active input = slot 0
        selector.set_property("active-pad", self._sink_pads[0])

        # Bus watch
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error",   self._on_error)
        bus.connect("message::warning", self._on_warning)

        self._pipeline = pipeline
        self._selector = selector
        self._active_input = 0

        ret = pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            pipeline.set_state(Gst.State.NULL)
            raise RuntimeError("Pipeline failed to reach PLAYING state")
        pipeline.get_state(10 * Gst.SECOND)

        self._running = True
        log.info(
            "Pipeline started — output UUID %s, slots %s, active=slot 0",
            self._output_flow_uuid,
            self._slot_kinds,
        )

    def _build_mxl_input(
        self, pipeline: Gst.Pipeline, idx: int, flow_uuid: str, sink_pad: Gst.Pad
    ) -> None:
        src = Gst.ElementFactory.make("mxlsrc", f"mxlsrc_{idx}")
        if not src:
            raise RuntimeError("Could not create mxlsrc — is libgstmxl.so installed in the plugin path?")
        src.set_property("video-flow-id", flow_uuid)
        src.set_property("domain", self._domain_path)

        caps = Gst.ElementFactory.make("capsfilter", f"vcaps_{idx}")
        caps.set_property("caps", Gst.Caps.from_string("video/x-raw,format=v210"))
        queue = Gst.ElementFactory.make("queue", f"vqueue_{idx}")

        for el in (src, caps, queue):
            pipeline.add(el)
        if not src.link(caps):
            raise RuntimeError(f"Failed to link mxlsrc → capsfilter for slot {idx}")
        if not caps.link(queue):
            raise RuntimeError(f"Failed to link capsfilter → queue for slot {idx}")
        src_pad = queue.get_static_pad("src")
        if src_pad.link(sink_pad) != Gst.PadLinkReturn.OK:
            raise RuntimeError(f"Failed to link queue → input-selector for slot {idx}")

    def _build_black_input(
        self, pipeline: Gst.Pipeline, idx: int, fmt: dict, sink_pad: Gst.Pad
    ) -> None:
        src = Gst.ElementFactory.make("videotestsrc", f"black_{idx}")
        if not src:
            raise RuntimeError("Could not create videotestsrc")
        src.set_property("pattern", 2)        # 2 = black
        src.set_property("is-live", True)

        gr = fmt["grain_rate"]
        interlace = _interlace_mode_to_caps(fmt["interlace_mode"])
        # Note: switching to a black-fill slot at runtime is disabled in the UI
        # because caps and timing differences with the live mxlsrc branches trip
        # "Internal data stream error" in input-selector. The black-fill source
        # is still wired in here so the pipeline graph is complete and so that
        # an unused slot doesn't have to be specially handled.
        caps_str = (
            f'video/x-raw,format=v210,'
            f'width={fmt["frame_width"]},height={fmt["frame_height"]},'
            f'framerate={gr["numerator"]}/{gr["denominator"]},'
            f'interlace-mode={interlace}'
        )
        caps = Gst.ElementFactory.make("capsfilter", f"bcaps_{idx}")
        caps.set_property("caps", Gst.Caps.from_string(caps_str))

        conv = Gst.ElementFactory.make("videoconvert", f"bconv_{idx}")
        queue = Gst.ElementFactory.make("queue", f"bqueue_{idx}")

        for el in (src, conv, caps, queue):
            pipeline.add(el)
        if not src.link(conv):
            raise RuntimeError(f"Failed to link videotestsrc → videoconvert for slot {idx}")
        if not conv.link(caps):
            raise RuntimeError(f"Failed to link videoconvert → capsfilter for slot {idx}")
        if not caps.link(queue):
            raise RuntimeError(f"Failed to link capsfilter → queue for slot {idx}")
        src_pad = queue.get_static_pad("src")
        if src_pad.link(sink_pad) != Gst.PadLinkReturn.OK:
            raise RuntimeError(f"Failed to link black-fill queue → input-selector for slot {idx}")

    # ── Teardown ──────────────────────────────────────────────────────────────

    def _teardown(self) -> None:
        if self._pipeline is None:
            self._running = False
            self._sink_pads = {}
            self._slot_kinds = []
            self._selector = None
            return
        log.info("Tearing down pipeline…")
        self._pipeline.set_state(Gst.State.NULL)
        self._pipeline.get_state(5 * Gst.SECOND)
        self._pipeline = None
        self._selector = None
        self._sink_pads = {}
        self._slot_kinds = []
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
