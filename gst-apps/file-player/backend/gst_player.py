# SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
# SPDX-License-Identifier: Apache-2.0
"""
GStreamer looping file player.

Pipeline (user-controlled start/stop):
  Source/decode: filesrc location=<path> → uridecodebin (dynamic pads)
  Video branch:  → queue → videoconvert → capsfilter(video/x-raw,format=v210)
                 → queue → mxlsink
  Audio branch:  → queue → audioconvert → audioresample
                 → capsfilter(audio/x-raw,format=F32LE,rate=48000,layout=interleaved)
                 → queue → mxlsink

Plays continuously: on EOS the pipeline issues a flushing seek to position 0.
Flow UUIDs are deterministic (UUID v5) derived from the group hint.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from typing import Optional
from urllib.parse import quote

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstPbutils", "1.0")
from gi.repository import GLib, Gst, GstPbutils

Gst.init(None)
log = logging.getLogger(__name__)

# Fixed namespace for UUID v5 derivation. Treat as immutable — changing it
# would orphan all previously written flow directories on every domain.
_MXL_PLAYER_NS = uuid.UUID("9d2e4f1a-7b6c-4d8e-9f0a-1b2c3d4e5f60")

MEDIA_ROOT = "/home/file"


def _file_uri(path: str) -> str:
    return "file://" + quote(path, safe="/")


def _resolve_media(filename: str) -> str:
    """Resolve a filename against MEDIA_ROOT, rejecting path traversal."""
    if not filename:
        raise ValueError("empty filename")
    full = os.path.realpath(os.path.join(MEDIA_ROOT, filename))
    root = os.path.realpath(MEDIA_ROOT)
    if not (full == root or full.startswith(root + os.sep)):
        raise ValueError(f"path escapes media root: {filename!r}")
    if not os.path.isfile(full):
        raise ValueError(f"file not found: {filename!r}")
    return full


def probe_file(filename: str) -> dict:
    """Probe a media file with GstPbutils.Discoverer."""
    full = _resolve_media(filename)
    discoverer = GstPbutils.Discoverer.new(15 * Gst.SECOND)
    info = discoverer.discover_uri(_file_uri(full))

    streams: list[dict] = []
    has_video = False
    has_audio = False

    for vinfo in info.get_video_streams():
        has_video = True
        caps = vinfo.get_caps()
        codec = GstPbutils.pb_utils_get_codec_description(caps) if caps else "unknown"
        fr_num = vinfo.get_framerate_num()
        fr_den = vinfo.get_framerate_denom() or 1
        framerate = f"{fr_num}/{fr_den}" if fr_num else "unknown"
        streams.append({
            "type":      "video",
            "codec":     codec,
            "width":     vinfo.get_width(),
            "height":    vinfo.get_height(),
            "framerate": framerate,
        })

    for ainfo in info.get_audio_streams():
        has_audio = True
        caps = ainfo.get_caps()
        codec = GstPbutils.pb_utils_get_codec_description(caps) if caps else "unknown"
        streams.append({
            "type":        "audio",
            "codec":       codec,
            "sample_rate": ainfo.get_sample_rate(),
            "channels":    ainfo.get_channels(),
        })

    return {"has_video": has_video, "has_audio": has_audio, "streams": streams}


class GstPlayer:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pipeline: Optional[Gst.Pipeline] = None
        self._running = False
        self._gen = 0  # cancellation token for stale patch threads

        self._config: Optional[dict] = None
        self._file: Optional[str] = None
        self._streams: Optional[dict] = None
        self._flow_uuids: dict[str, str] = {}

        # Element refs kept for dynamic pad linking
        self._vbranch_sink_pad = None  # sink pad on first element of video branch
        self._abranch_sink_pad = None  # sink pad on first element of audio branch

        self._glib_loop = GLib.MainLoop()
        threading.Thread(target=self._glib_loop.run, daemon=True).start()

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self, config: dict) -> dict:
        with self._lock:
            if self._running:
                raise RuntimeError("Pipeline is already running")

            filename = config["file"]
            full = _resolve_media(filename)
            streams = probe_file(filename)

            video_active = bool(config.get("video", {}).get("active")) and streams["has_video"]
            audio_active = bool(config.get("audio", {}).get("active")) and streams["has_audio"]
            if not (video_active or audio_active):
                raise RuntimeError(
                    "Nothing to play: file has no matching active streams"
                )

            self._config = config
            self._file = filename
            self._streams = streams
            self._gen += 1
            gen = self._gen
            self._flow_uuids = self._generate_uuids(config, video_active, audio_active)
            self._build_pipeline(full, config, video_active, audio_active)
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
            self._file = None
            self._streams = None
            self._config = None

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def get_status(self) -> dict:
        with self._lock:
            return {
                "running":    self._running,
                "file":       self._file,
                "flow_uuids": dict(self._flow_uuids),
                "streams":    self._streams,
                "grouphint":  (self._config or {}).get("grouphint"),
            }

    # ── Pipeline ──────────────────────────────────────────────────────────────

    def _generate_uuids(
        self, config: dict, video_active: bool, audio_active: bool
    ) -> dict[str, str]:
        grouphint = config.get("grouphint", "Clip-Player")
        uuids: dict[str, str] = {}
        if video_active:
            uuids["video"] = str(uuid.uuid5(_MXL_PLAYER_NS, f"{grouphint}:video"))
        if audio_active:
            uuids["audio"] = str(uuid.uuid5(_MXL_PLAYER_NS, f"{grouphint}:audio"))
        return uuids

    def _teardown_pipeline(self) -> None:
        if self._pipeline is None:
            return
        self._pipeline.set_state(Gst.State.NULL)
        self._pipeline.get_state(5 * Gst.SECOND)
        self._pipeline = None
        self._vbranch_sink_pad = None
        self._abranch_sink_pad = None
        import gc
        gc.collect()

    def _build_pipeline(
        self, full_path: str, config: dict, video_active: bool, audio_active: bool
    ) -> None:
        domain = config["domain"]
        pipeline = Gst.Pipeline.new("file-player")

        src = Gst.ElementFactory.make("uridecodebin", "src")
        src.set_property("uri", _file_uri(full_path))
        pipeline.add(src)

        if video_active:
            self._vbranch_sink_pad = self._build_video_branch(
                pipeline, self._flow_uuids["video"], domain
            )
        if audio_active:
            self._abranch_sink_pad = self._build_audio_branch(
                pipeline, self._flow_uuids["audio"], domain
            )

        src.connect("pad-added", self._on_pad_added)

        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error",   self._on_error)
        bus.connect("message::warning", self._on_warning)
        bus.connect("message::eos",     self._on_eos)

        pipeline.set_state(Gst.State.PLAYING)
        ret, state, _ = pipeline.get_state(10 * Gst.SECOND)
        log.info("Pipeline state: %s (ret=%s)", state, ret)

        self._pipeline = pipeline

    def _build_video_branch(
        self, pipeline: Gst.Pipeline, flow_uuid: str, domain: str
    ) -> Gst.Pad:
        vqueue_in  = Gst.ElementFactory.make("queue",       "vqueue_in")
        vconv      = Gst.ElementFactory.make("videoconvert", "vconv")
        vcaps      = Gst.ElementFactory.make("capsfilter",  "vcaps")
        vcaps.set_property("caps", Gst.Caps.from_string("video/x-raw,format=v210"))
        vqueue_out = Gst.ElementFactory.make("queue",       "vqueue_out")
        vsink      = Gst.ElementFactory.make("mxlsink",     "vsink")
        vsink.set_property("flow-id", flow_uuid)
        vsink.set_property("domain", domain)

        for el in (vqueue_in, vconv, vcaps, vqueue_out, vsink):
            pipeline.add(el)
        vqueue_in.link(vconv)
        vconv.link(vcaps)
        vcaps.link(vqueue_out)
        vqueue_out.link(vsink)

        for el in (vqueue_in, vconv, vcaps, vqueue_out, vsink):
            el.sync_state_with_parent()

        return vqueue_in.get_static_pad("sink")

    def _build_audio_branch(
        self, pipeline: Gst.Pipeline, flow_uuid: str, domain: str
    ) -> Gst.Pad:
        aqueue_in  = Gst.ElementFactory.make("queue",        "aqueue_in")
        aconv      = Gst.ElementFactory.make("audioconvert", "aconv")
        aresample  = Gst.ElementFactory.make("audioresample","aresample")
        acaps      = Gst.ElementFactory.make("capsfilter",   "acaps")
        acaps.set_property(
            "caps",
            Gst.Caps.from_string(
                "audio/x-raw,format=F32LE,layout=interleaved,rate=48000"
            ),
        )
        aqueue_out = Gst.ElementFactory.make("queue",        "aqueue_out")
        asink      = Gst.ElementFactory.make("mxlsink",      "asink")
        asink.set_property("flow-id", flow_uuid)
        asink.set_property("domain", domain)

        for el in (aqueue_in, aconv, aresample, acaps, aqueue_out, asink):
            pipeline.add(el)
        aqueue_in.link(aconv)
        aconv.link(aresample)
        aresample.link(acaps)
        acaps.link(aqueue_out)
        aqueue_out.link(asink)

        for el in (aqueue_in, aconv, aresample, acaps, aqueue_out, asink):
            el.sync_state_with_parent()

        return aqueue_in.get_static_pad("sink")

    # ── Bus / signal callbacks ────────────────────────────────────────────────

    def _on_pad_added(self, _src: Gst.Element, pad: Gst.Pad) -> None:
        caps = pad.get_current_caps() or pad.query_caps(None)
        if not caps or caps.get_size() == 0:
            return
        media = caps.get_structure(0).get_name()

        if media.startswith("video/") and self._vbranch_sink_pad is not None:
            sink = self._vbranch_sink_pad
        elif media.startswith("audio/") and self._abranch_sink_pad is not None:
            sink = self._abranch_sink_pad
        else:
            return

        if sink.is_linked():
            return
        result = pad.link(sink)
        if result != Gst.PadLinkReturn.OK:
            log.warning("Pad link failed (%s): %s", media, result)

    def _on_eos(self, _bus: Gst.Bus, _msg: Gst.Message) -> None:
        if self._pipeline is None:
            return
        log.info("EOS received — seeking to 0 to loop")
        self._pipeline.seek_simple(
            Gst.Format.TIME,
            Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
            0,
        )

    def _on_error(self, _bus: Gst.Bus, msg: Gst.Message) -> None:
        err, debug = msg.parse_error()
        log.error("GStreamer error: %s  debug: %s", err, debug)

    def _on_warning(self, _bus: Gst.Bus, msg: Gst.Message) -> None:
        warn, debug = msg.parse_warning()
        log.warning("GStreamer warning: %s  debug: %s", warn, debug)

    # ── Flow_def.json patching ────────────────────────────────────────────────

    def _patch_flow_defs(self, config: dict, uuids: dict, gen: int) -> None:
        domain = config["domain"]
        grouphint = config.get("grouphint", "Clip-Player")
        flows = [
            ("video", "Video", config.get("video", {})),
            ("audio", "Audio", config.get("audio", {})),
        ]
        for key, role, flow_cfg in flows:
            flow_uuid = uuids.get(key)
            if not flow_uuid:
                continue
            path = os.path.join(domain, f"{flow_uuid}.mxl-flow", "flow_def.json")
            deadline = time.monotonic() + 15
            while not os.path.exists(path):
                if time.monotonic() > deadline:
                    log.warning("Timeout waiting for flow_def.json: %s", path)
                    break
                if self._gen != gen:
                    return
                time.sleep(0.5)
            if not os.path.exists(path):
                continue
            try:
                with open(path) as f:
                    data = json.load(f)
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
