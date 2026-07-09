# SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
# SPDX-License-Identifier: Apache-2.0
"""
GStreamer test-signal generator.

Pipeline (user-controlled start/stop):
  Video:   videotestsrc → capsfilter(res+fps) → timeoverlay → textoverlay
                        → videoconvert → capsfilter(v210) → queue → mxlsink
  Audio 1: audiotestsrc → audioconvert → capsfilter(F32LE,Nch,48k) → queue → mxlsink
  Audio 2: audiotestsrc → audioconvert → capsfilter(F32LE,Nch,48k) → queue → mxlsink

Live-adjustable: video pattern, timecode, ident, audio pattern, audio level.
Flow UUIDs are deterministic (UUID v5) derived from the group hint — restarting
the pipeline with the same group hint reuses the same UUIDs, while changing
the group hint produces a different set.
"""

from __future__ import annotations

import collections
import json
import logging
import os
import struct
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

# Fixed namespace for UUID v5 derivation.  Changing this constant would
# invalidate all previously written flow directories, so treat it as immutable.
_MXL_TGEN_NS = uuid.UUID("7a4b2c1d-e5f6-4a7b-8c9d-0e1f2a3b4c5d")


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


# ── Ancillary-data (ST 2038 / captions / SCTE) helpers ──────────────────────────
#
# Closed captions and SCTE-104 share ONE `video/smpte291` MXL data flow ("Ancillary
# Data"): the caption CEA-608 ANC (DID 0x61) and the SCTE-104 ANC (DID 0x41) are
# combined per frame (the caption grain's bytes + the SCTE ANC packet appended in
# Python — see _on_caption_to_anc) and written through a single `mxlsink`. This mirrors
# a real VANC space carrying multiple ANC packet types.
#
# DID/SDID 0x41/0x07 is the SMPTE ST 2010 registration for SCTE-104 in VANC. The
# ST 2038 ANC bit layout (see dmf-mxl/.../format/data.rs::st2038_anc_packet_from_ancillary_meta)
# is: 6 zero bits, C, line(11), offset(12), DID(10), SDID(10), data_count(10),
# data_count user-data words(10 each), checksum(10), then 1-bit padding to a byte.
SCTE_DID = 0x41
SCTE_SDID = 0x07

# How often a caption row is pushed into tttocea608 (one wrapped line per tick,
# cycling for the roll-up scroll). CEA-608 only carries ~2 chars/frame, so a full
# 32-char row needs ~0.5s of frames to transmit; pushing faster makes the tail of
# one row collide with the head of the next. 2s gives ample headroom and a
# readable scroll speed.
CAPTION_PUSH_MS = 2000

# A SCTE trigger rides exactly ONE grain. mxlsrc's data path reads grains
# sequentially (create_data: frame_counter+1 each call, get_complete_grain blocks),
# so on a host that keeps up it reads every grain and a single-frame marker round-trips
# and parses reliably — verified end-to-end through mxlsink→MXL→mxlsrc. (It only skips
# grains if the consumer falls behind the wall-clock head, which doesn't happen for
# cheap ANC data on a capable host.) The consumer still debounces (1s) defensively.


class _BitWriter:
    """Minimal MSB-first bit writer for assembling an ST 2038 ANC packet."""

    def __init__(self) -> None:
        self._acc = 0
        self._nbits = 0
        self._out = bytearray()

    def write(self, value: int, bits: int) -> None:
        for i in range(bits - 1, -1, -1):
            self._acc = (self._acc << 1) | ((value >> i) & 1)
            self._nbits += 1
            if self._nbits == 8:
                self._out.append(self._acc & 0xFF)
                self._acc = 0
                self._nbits = 0

    def pad_ones_to_byte(self) -> None:
        while self._nbits != 0:
            self._acc = (self._acc << 1) | 1
            self._nbits += 1
            if self._nbits == 8:
                self._out.append(self._acc & 0xFF)
                self._acc = 0
                self._nbits = 0

    def to_bytes(self) -> bytes:
        return bytes(self._out)


def chunk_caption(text: str, width: int = 32) -> list[str]:
    """Word-wrap ``text`` into lines of at most ``width`` characters.

    CEA-608 rows are a hard 32 characters; longer text is truncated by the
    encoder (this is why a long single line showed clipped on the player). We
    split on word boundaries, hard-splitting any word longer than a row.
    """
    lines: list[str] = []
    cur = ""
    for word in text.split():
        while len(word) > width:
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(word[:width])
            word = word[width:]
        if not cur:
            cur = word
        elif len(cur) + 1 + len(word) <= width:
            cur += " " + word
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


# SCTE-104 splice_insert_type values (SCTE-104 §10.3.x).
SCTE104_SPLICE_START_NORMAL = 0x01


def build_scte104_message(event_id: int, splice_type: int = SCTE104_SPLICE_START_NORMAL,
                          message_number: int = 0) -> bytes:
    """A conformant SCTE-104 `multiple_operation_message` carrying a single
    `splice_request_data` op (opID 0x0101), as carried in VANC per SMPTE ST 2010.

    Layout (big-endian): reserved(0xFFFF), messageSize, protocol_version,
    AS_index, message_number, DPI_PID_index, SCTE35_protocol_version,
    timestamp.time_type(=0, no timestamp), num_ops(=1), then the op:
    opID(0x0101), data_length, splice_request_data{ splice_insert_type,
    splice_event_id, unique_program_id, pre_roll_time, brk_duration, avail_num,
    avails_expected, auto_return_flag }.
    """
    splice_request = (
        bytes([splice_type & 0xFF])
        + struct.pack(">I", event_id & 0xFFFFFFFF)  # splice_event_id
        + struct.pack(">H", 0)   # unique_program_id
        + struct.pack(">H", 0)   # pre_roll_time (ms)
        + struct.pack(">H", 0)   # brk_duration (1/10 s)
        + bytes([0])             # avail_num
        + bytes([0])             # avails_expected
        + bytes([0])             # auto_return_flag
    )  # 14 bytes
    op = struct.pack(">H", 0x0101) + struct.pack(">H", len(splice_request)) + splice_request

    body = (
        bytes([0])               # protocol_version
        + bytes([0])             # AS_index
        + bytes([message_number & 0xFF])
        + struct.pack(">H", 0)   # DPI_PID_index
        + bytes([0])             # SCTE35_protocol_version
        + bytes([0])             # timestamp.time_type = 0 (none)
        + bytes([1])             # num_ops
        + op
    )
    message_size = 4 + len(body)  # reserved(2) + messageSize(2) + body
    return struct.pack(">H", 0xFFFF) + struct.pack(">H", message_size) + body


def build_scte104_anc_packet(event_id: int,
                             splice_type: int = SCTE104_SPLICE_START_NORMAL,
                             message_number: int = 0) -> bytes:
    """Wrap a SCTE-104 message in one ST 2038 ANC packet (DID 0x41 / SDID 0x07).

    Each message byte goes in the low 8 bits of a 10-bit user-data word. Parity
    and checksum bits are left zero — they are not validated on the read side, and
    the consumer reconstructs the message from the low 8 bits of each word.
    """
    msg = build_scte104_message(event_id, splice_type, message_number)
    w = _BitWriter()
    w.write(0, 6)              # leading zero bits
    w.write(0, 1)             # C (luma channel)
    w.write(0, 11)            # line number
    w.write(0, 12)            # horizontal offset
    w.write(SCTE_DID, 10)     # DID
    w.write(SCTE_SDID, 10)    # SDID
    w.write(len(msg), 10)     # data_count (== message length, ≤255)
    for b in msg:
        w.write(b, 10)        # user-data word (low 8 bits = message byte)
    w.write(0, 10)            # checksum (not validated on the read side)
    w.pad_ones_to_byte()
    return w.to_bytes()


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
        # Captions/SCTE each live in their OWN pipeline: their appsrc-fed branches
        # otherwise perturb the shared clock/base-time at startup and corrupt the
        # first-buffer time mapping of the video/audio mxlsinks (epoch grains).
        self._data_pipelines: list[Gst.Pipeline] = []
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

        # Ancillary-data state (captions + SCTE share ONE "Ancillary Data" flow)
        self._ancillary_active = False
        self._caption_text = ""
        self._caption_chunks: list[str] = []   # text wrapped to <=32-char rows
        self._caption_chunk_idx = 0            # cycles for the scroll
        self._scte_count = 0          # number of triggers fired this run (for status)
        self._scte_last_ts: Optional[float] = None  # epoch seconds of last trigger
        self._scte_event_id = 0       # SCTE-104 splice_event_id, incremented per trigger
        self._fn, self._fd = 30, 1    # framerate, set per start

        # Element refs for live property changes
        self._vsrc = None
        self._timeoverlay = None
        self._textoverlay = None
        self._ccsrc = None            # appsrc feeding tttocea608 (caption text)
        self._ancsrc = None           # appsrc feeding the output Ancillary Data flow
        self._caption_pts = 0         # ns, monotonic per-push timestamp (text appsrc)
        self._anc_pts = 0             # ns, monotonic per-grain timestamp (output appsrc)
        self._caption_dur_ns = 0      # ns, frame-locked caption buffer duration (set per start)
        self._scte_pending_event_id: Optional[int] = None  # event_id to append to the NEXT grain (one-shot)
        # Frame-paced ANC playout (decouples SCTE injection from the bursty caption
        # harvest). The harvest callback APPENDS caption-ANC bytes to this FIFO; a
        # playout thread pops ONE grain per frame and pushes it (appending a pending
        # SCTE marker), so a trigger rides the next grain (~1 frame) instead of waiting
        # for the next ~CAPTION_PUSH_MS harvest burst. See _anc_playout.
        self._anc_fifo: collections.deque = collections.deque()
        self._anc_last_cc: Optional[bytes] = None  # last grain bytes, repeated on (rare) underflow
        self._anc_cushion = 0         # grains to buffer before steady playout (one burst; set per start)

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
            self._fn, self._fd = FRAMERATES.get(self._framerate, (30, 1))
            self._audio1.channels = config.get("audio1", {}).get("channels", 2)
            self._audio2.channels = config.get("audio2", {}).get("channels", 2)
            self._ancillary_active = bool(config.get("ancillary", {}).get("active"))
            self._caption_text = ""
            self._caption_chunks = []
            self._caption_chunk_idx = 0
            self._scte_count = 0
            self._scte_last_ts = None
            self._scte_event_id = 0
            self._caption_pts = 0
            self._anc_pts = 0
            self._scte_pending_event_id = None
            self._anc_fifo.clear()
            self._anc_last_cc = None
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

    def set_caption(self, text: str) -> None:
        with self._lock:
            self._caption_text = text
            self._caption_chunks = chunk_caption(text) if text.strip() else []
            self._caption_chunk_idx = 0

    def trigger_scte(self) -> None:
        with self._lock:
            if not self._ancillary_active or self._ccsrc is None:
                raise RuntimeError("Ancillary Data flow is not active")
            # One user trigger = one event = ONE grain. The reader reads every grain,
            # so a single-frame marker is sufficient (verified end-to-end).
            self._scte_count += 1
            self._scte_event_id += 1
            self._scte_last_ts = time.time()
            self._scte_pending_event_id = self._scte_event_id
            log.info("SCTE-104 trigger queued (event_id=%d, single grain) on flow %s",
                     self._scte_event_id, self._flow_uuids.get("ancillary"))

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
                "ancillary": {
                    "active": self._ancillary_active,
                },
                "captions": {
                    "text": self._caption_text,
                },
                "scte": {
                    "trigger_count":   self._scte_count,
                    "last_trigger_ts": self._scte_last_ts,
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
        grouphint = config.get("grouphint", "Test-Generator")
        uuids: dict[str, str] = {}
        if config.get("video", {}).get("active"):
            uuids["video"]  = str(uuid.uuid5(_MXL_TGEN_NS, f"{grouphint}:video"))
        if config.get("audio1", {}).get("active"):
            uuids["audio1"] = str(uuid.uuid5(_MXL_TGEN_NS, f"{grouphint}:audio1"))
        if config.get("audio2", {}).get("active"):
            uuids["audio2"] = str(uuid.uuid5(_MXL_TGEN_NS, f"{grouphint}:audio2"))
        if config.get("ancillary", {}).get("active"):
            uuids["ancillary"] = str(uuid.uuid5(_MXL_TGEN_NS, f"{grouphint}:ancillary"))
        return uuids

    def _teardown_pipeline(self) -> None:
        for dp in self._data_pipelines:
            dp.set_state(Gst.State.NULL)
            dp.get_state(5 * Gst.SECOND)
        self._data_pipelines = []
        if self._pipeline is None:
            return
        self._pipeline.set_state(Gst.State.NULL)
        self._pipeline.get_state(5 * Gst.SECOND)
        self._pipeline = None
        self._vsrc = None
        self._timeoverlay = None
        self._textoverlay = None
        self._ccsrc = None
        self._ancsrc = None
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

        # ── Bus (A/V pipeline) ──────────────────────────────────────────────────
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_error)
        bus.connect("message::warning", self._on_warning)

        pipeline.set_state(Gst.State.PLAYING)
        ret, state, _ = pipeline.get_state(10 * Gst.SECOND)
        log.info("Pipeline state: %s (ret=%s)", state, ret)

        self._pipeline = pipeline

        # ── Ancillary data (captions + SCTE-104 on ONE flow) ────────────────────
        # Two SEPARATE pipelines, both kept out of the A/V pipeline (their appsrc-fed,
        # manually-timestamped branches otherwise perturb the shared clock/base-time
        # at startup and epoch the video/audio mxlsinks):
        #
        #   harvest:  appsrc(text) → tttocea608 → ccconverter → cctost2038anc → caps
        #             → appsink(ccsink)            [harvests per-frame caption ANC bytes]
        #   output:   appsrc(ancsrc) → queue → mxlsink   [the proven appsrc→mxlsink form]
        #
        # The ccsink callback (_on_caption_to_anc) pushes each caption grain's bytes to
        # ancsrc 1:1 (preserving the per-frame CEA-608 cadence so it decodes), appending
        # a SCTE-104 ANC packet on a triggered frame. Building the output grain fresh
        # from bytes sidesteps the PyGObject limits that broke a pad probe (in-flight
        # buffers aren't writable and info.data can't be replaced). Separate pipelines
        # keep the output appsrc→mxlsink identical to the proven SCTE-only flow, so it
        # doesn't epoch the way a single combined pipeline did.
        #
        # We deliberately do NOT block on get_state(): start() holds self._lock across
        # _build_pipeline and the push callbacks also take it, so blocking would stall
        # preroll and push the first grain at the epoch.
        self._data_pipelines = []
        gen = self._gen

        if self._ancillary_active:
            # Frame-lock the caption cadence: a text buffer must span a WHOLE number
            # of frames, else (e.g. 2000ms = 119.88 frames at 59.94) every boundary is
            # ~1 grain off and the latency rolls. Exact rational duration (ONE rounded
            # division; frames*round(period) reintroduces ~40µs/buffer drift).
            frames = max(1, round(CAPTION_PUSH_MS / 1000 * self._fn / self._fd))
            self._caption_dur_ns = round(frames * Gst.SECOND * self._fd / self._fn)
            interval_ms = max(1, round(self._caption_dur_ns / Gst.MSECOND))
            # The harvest emits each text buffer's `frames` grains in an instant burst;
            # the playout drains at frame rate, so it must buffer one full burst before
            # starting or it underflows at the trough before the next burst.
            self._anc_cushion = frames

            hp, op = self._build_ancillary_pipelines(self._flow_uuids["ancillary"], domain)
            self._connect_data_bus(hp)
            self._connect_data_bus(op)
            self._data_pipelines += [op, hp]   # output first so it's ready for pushes
            op.set_state(Gst.State.PLAYING)
            hp.set_state(Gst.State.PLAYING)
            GLib.timeout_add(interval_ms, self._push_caption, gen)
            # Frame-paced playout of the harvested ANC grains (see _anc_playout). Runs
            # on its own thread, tied to `gen` so a restart/stop cancels it.
            threading.Thread(target=self._anc_playout, args=(gen,), daemon=True).start()
            log.info("Started ancillary-data pipelines (captions harvest + paced output)")

    def _connect_data_bus(self, pipeline: Gst.Pipeline) -> None:
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_error)
        bus.connect("message::warning", self._on_warning)

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

    def _build_ancillary_pipelines(
        self, flow_uuid: str, domain: str
    ) -> tuple[Gst.Pipeline, Gst.Pipeline]:
        """Build the harvest + output pipelines for the Ancillary Data flow.

        harvest:  appsrc(text) → tttocea608 → ccconverter → cctost2038anc → caps
                  → appsink(ccsink)   [emits one ST 2038 caption ANC grain per frame]
        output:   appsrc(ancsrc) → queue → mxlsink   [proven appsrc→mxlsink form]

        ccsink's new-sample callback (_on_caption_to_anc) forwards each caption grain's
        bytes to ancsrc 1:1, appending a SCTE-104 ANC packet when a trigger is pending.
        Returns (harvest, output).
        """
        fn, fd = self._fn, self._fd
        anc_caps = f"meta/x-st-2038,alignment=frame,framerate={fn}/{fd}"

        # ── Harvest pipeline ────────────────────────────────────────────────────
        harvest = Gst.Pipeline.new("test-generator-anc-harvest")
        ccsrc = Gst.ElementFactory.make("appsrc", "ccsrc")
        ccsrc.set_property("caps", Gst.Caps.from_string("text/x-raw,format=utf8"))
        ccsrc.set_property("format", Gst.Format.TIME)
        ccsrc.set_property("is-live", True)
        ccsrc.set_property("do-timestamp", False)

        tttocea608 = Gst.ElementFactory.make("tttocea608", "tttocea608")
        # pop-on: each caption is loaded off-screen and flipped complete, so a single
        # ≤32-char wrapped chunk round-trips cleanly (roll-up smears ~2 chars at each
        # carriage-return boundary). We cycle one chunk per tick (see _push_caption).
        tttocea608.set_property("mode", "pop-on")

        ccconv = Gst.ElementFactory.make("ccconverter", "ccconv")
        cccaps = Gst.ElementFactory.make("capsfilter", "cccaps")
        cccaps.set_property(
            "caps", Gst.Caps.from_string(f"closedcaption/x-cea-608,framerate={fn}/{fd}")
        )
        cctoanc = Gst.ElementFactory.make("cctost2038anc", "cctoanc")
        ccanccaps = Gst.ElementFactory.make("capsfilter", "ccanccaps")
        ccanccaps.set_property("caps", Gst.Caps.from_string(anc_caps))

        ccsink = Gst.ElementFactory.make("appsink", "ccsink")
        ccsink.set_property("emit-signals", True)
        # sync=false: the harvest delivers each text buffer's `frames` caption grains in
        # an instant BURST (appsink sync=true does NOT pace this chain — tttocea608's
        # output timestamps don't gate the sink). De-bursting is done instead by the
        # frame-paced playout thread (_anc_playout), which reclocks the FIFO to frame rate.
        ccsink.set_property("sync", False)
        # Deliver EVERY caption grain in order (no drop) — dropping frames corrupts
        # the per-frame CEA-608 control-code stream.
        ccsink.set_property("max-buffers", 0)
        ccsink.set_property("drop", False)
        ccsink.connect("new-sample", self._on_caption_to_anc)

        for el in (ccsrc, tttocea608, ccconv, cccaps, cctoanc, ccanccaps, ccsink):
            harvest.add(el)
        ccsrc.link(tttocea608)
        tttocea608.link(ccconv)
        ccconv.link(cccaps)
        cccaps.link(cctoanc)
        cctoanc.link(ccanccaps)
        ccanccaps.link(ccsink)

        # ── Output pipeline ─────────────────────────────────────────────────────
        output = Gst.Pipeline.new("test-generator-anc-output")
        ancsrc = Gst.ElementFactory.make("appsrc", "ancsrc")
        ancsrc.set_property("caps", Gst.Caps.from_string(anc_caps))
        ancsrc.set_property("format", Gst.Format.TIME)
        ancsrc.set_property("is-live", True)
        ancsrc.set_property("do-timestamp", False)
        ancqueue = Gst.ElementFactory.make("queue", "ancqueue")
        ancsink = Gst.ElementFactory.make("mxlsink", "ancsink")
        ancsink.set_property("flow-id", flow_uuid)
        ancsink.set_property("domain", domain)
        for el in (ancsrc, ancqueue, ancsink):
            output.add(el)
        ancsrc.link(ancqueue)
        ancqueue.link(ancsink)

        self._ccsrc = ccsrc
        self._ancsrc = ancsrc
        return harvest, output

    # ── Feed callbacks ──────────────────────────────────────────────────────────

    def _push_caption(self, gen: int) -> bool:
        """GLib timer: push the next wrapped caption row into the CEA-608 chain
        (blank when idle, so the flow keeps producing per-frame CC ANC grains)."""
        with self._lock:
            if self._gen != gen or self._ccsrc is None:
                return False  # pipeline restarted/stopped — cancel timer
            if self._caption_chunks:
                chunk = self._caption_chunks[self._caption_chunk_idx % len(self._caption_chunks)]
                self._caption_chunk_idx += 1
            else:
                chunk = " "  # blank keep-alive row; the consumer ignores it
            ccsrc = self._ccsrc
            # Frame-locked duration (whole number of frames; see _build_pipeline) so
            # the caption grains stay aligned with the flow's grain rate — a fixed
            # 2000ms drifts at fractional rates like 59.94 and rolls the latency.
            dur = self._caption_dur_ns
            pts = self._caption_pts
            self._caption_pts += dur
        data = chunk.encode("utf-8")
        buf = Gst.Buffer.new_allocate(None, len(data), None)
        buf.fill(0, data)
        buf.pts = pts
        buf.duration = dur
        ccsrc.emit("push-buffer", buf)
        return True

    def _on_caption_to_anc(self, appsink: "Gst.Element") -> "Gst.FlowReturn":
        """harvest appsink → FIFO. tttocea608 emits each text buffer's `frames` caption
        ANC grains in an instant burst; we just queue their BYTES here (in order). The
        frame-paced _anc_playout thread reclocks them to frame rate and pushes to ancsrc,
        appending a pending SCTE marker at emit time — so a trigger rides the next grain
        (~1 frame) instead of waiting for the next harvest burst (~CAPTION_PUSH_MS)."""
        sample = appsink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        buf = sample.get_buffer()
        ok, minfo = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.OK
        try:
            cc_bytes = bytes(minfo.data)
        finally:
            buf.unmap(minfo)
        with self._lock:
            # Bounded (leaky): if playout ever lags production the oldest grain is dropped,
            # capping caption latency. With frame-locked rates this never fills.
            if len(self._anc_fifo) >= 2 * max(1, self._anc_cushion):
                self._anc_fifo.popleft()
            self._anc_fifo.append(cc_bytes)
        return Gst.FlowReturn.OK

    def _anc_playout(self, gen: int) -> None:
        """Frame-paced playout of harvested ANC grains → ancsrc → mxlsink.

        Pops ONE grain per frame at a monotonic cadence (independent of the bursty
        harvest and of any GStreamer clock sync), appending a pending SCTE-104 packet at
        emit time. A startup cushion of one harvest burst keeps the FIFO from underflowing
        at the trough before the next burst; on a (rare) underflow the last grain is
        repeated and PTS still advances, so mxlsink never ratchets latency. The output
        grain carries its OWN monotonic per-grain PTS (the harvested PTS can be invalid
        early → mxlsink epoch)."""
        frame_s = self._fd / self._fn
        frame_ns = round(Gst.SECOND * self._fd / self._fn)
        # Wait for the cushion to fill (or a stop/restart) before steady playout.
        while self._gen == gen:
            with self._lock:
                ready = len(self._anc_fifo) >= max(1, self._anc_cushion)
            if ready:
                break
            time.sleep(0.005)
        next_t = time.monotonic()
        while self._gen == gen:
            event_id = None
            with self._lock:
                ancsrc = self._ancsrc
                if ancsrc is None:
                    break
                if self._anc_fifo:
                    cc_bytes = self._anc_fifo.popleft()
                    self._anc_last_cc = cc_bytes
                else:
                    cc_bytes = self._anc_last_cc  # underflow: repeat last (usually padding)
                if cc_bytes is not None:
                    pts = self._anc_pts
                    self._anc_pts += frame_ns
                    if self._scte_pending_event_id is not None:
                        event_id = self._scte_pending_event_id
                        self._scte_pending_event_id = None
            if cc_bytes is not None:
                out = cc_bytes + build_scte104_anc_packet(event_id) if event_id is not None else cc_bytes
                obuf = Gst.Buffer.new_allocate(None, len(out), None)
                obuf.fill(0, out)
                obuf.pts = pts
                obuf.duration = frame_ns
                ancsrc.emit("push-buffer", obuf)
            next_t += frame_s
            dt = next_t - time.monotonic()
            if dt > 0:
                time.sleep(dt)
            elif dt < -0.5:
                next_t = time.monotonic()  # fell far behind (e.g. scheduling stall); resync

    def _patch_flow_defs(self, config: dict, uuids: dict, gen: int) -> None:
        domain = config["domain"]
        grouphint = config.get("grouphint", "Test-Generator")
        flows = [
            ("video",     config.get("video",     {})),
            ("audio1",    config.get("audio1",    {})),
            ("audio2",    config.get("audio2",    {})),
            ("ancillary", config.get("ancillary", {})),
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
                role = {
                    "video":     "Video",
                    "audio1":    "Audio",
                    "audio2":    "Audio",
                    "ancillary": "Ancillary Data",
                }.get(key, "Audio")
                full_grouphint = f"{grouphint}:{role}"
                data["grouphint"]   = full_grouphint
                data["description"] = flow_cfg.get("description", "")
                data["label"]       = flow_cfg.get("label", "")
                if isinstance(data.get("tags"), dict):
                    data["tags"]["urn:x-nmos:tag:grouphint/v1.0"] = [full_grouphint]
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
