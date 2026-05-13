# SPDX-FileCopyrightText: 2025 Contributors to the Media eXchange Layer project.
# SPDX-License-Identifier: Apache-2.0
"""
NMOS IS-05 bridge for the MXL-to-WebRTC gateway.

Responsibilities:
  1. Discover video receiver and audio receiver (sorted by label: xa, xv).
  2. Differentiate them by format: urn:x-nmos:format:video or urn:x-nmos:format:audio.
  3. Poll /active endpoints:
     - master_enable=true  + mxl_flow_id → call on_connect_video/audio(flow_id)
     - master_enable=false (was true)    → call on_disconnect_video/audio()
     - re-activation (activation_time changed) → disconnect then reconnect
"""

from __future__ import annotations

import logging
import time
from typing import Callable

import requests

log = logging.getLogger(__name__)

MXL_TRANSPORT = "urn:x-nmos:transport:mxl"
FORMAT_VIDEO   = "urn:x-nmos:format:video"
FORMAT_AUDIO   = "urn:x-nmos:format:audio"


def _fetch(url: str, timeout: float = 2.0):
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        log.debug("HTTP error %s: %s", url, exc)
        return None


class NmosBridge:
    def __init__(
        self,
        nmos_base: str = "http://localhost:9550",
        poll_interval: float = 1.0,
        rediscover_every: int = 30,
        on_connect_video: Callable[[str], None] | None = None,
        on_connect_audio: Callable[[str], None] | None = None,
        on_disconnect_video: Callable[[], None] | None = None,
        on_disconnect_audio: Callable[[], None] | None = None,
    ) -> None:
        self._base = nmos_base.rstrip("/")
        self._conn = f"{self._base}/x-nmos/connection/v1.2"
        self._node = f"{self._base}/x-nmos/node/v1.3"
        self._poll_interval    = poll_interval
        self._rediscover_every = rediscover_every
        self._on_connect_video    = on_connect_video
        self._on_connect_audio    = on_connect_audio
        self._on_disconnect_video = on_disconnect_video
        self._on_disconnect_audio = on_disconnect_audio

        # receiver UUID → "video" or "audio"
        self._receiver_format: dict[str, str] = {}
        # receiver UUID → last known active state
        self._active_state: dict[str, bool | None] = {}
        # receiver UUID → last seen activation_time
        self._activation_times: dict[str, str | None] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def discover_receivers(self) -> None:
        """Wait for 2 MXL receivers and classify them as video or audio."""
        for attempt in range(30):
            ids = self._list_mxl_resources("receivers")
            if len(ids) >= 2:
                break
            log.info("Waiting for 2 NMOS receivers… got %d (attempt %d)", len(ids), attempt + 1)
            time.sleep(1)
        else:
            log.warning("Only %d MXL receiver(s) found after 30 s", len(ids))

        for rid in ids:
            receiver = _fetch(f"{self._node}/receivers/{rid}")
            if not receiver:
                continue
            fmt = receiver.get("format", "")
            if FORMAT_VIDEO in fmt:
                self._receiver_format[rid] = "video"
                log.info("Video receiver: %s (label=%s)", rid, receiver.get("label"))
            elif FORMAT_AUDIO in fmt:
                self._receiver_format[rid] = "audio"
                log.info("Audio receiver: %s (label=%s)", rid, receiver.get("label"))
            else:
                log.warning("Receiver %s has unknown format: %s – skipping", rid, fmt)

        self._active_state     = {rid: None for rid in self._receiver_format}
        self._activation_times = {rid: None for rid in self._receiver_format}

    def run(self) -> None:
        """Poll loop – blocks forever. Run in a daemon thread."""
        log.info("NMOS bridge polling %s", self._conn)
        polls = self._rediscover_every  # trigger immediate rediscover

        while True:
            if polls >= self._rediscover_every:
                ids = self._list_mxl_resources("receivers")
                for rid in ids:
                    if rid not in self._receiver_format:
                        receiver = _fetch(f"{self._node}/receivers/{rid}")
                        if not receiver:
                            continue
                        fmt = receiver.get("format", "")
                        if FORMAT_VIDEO in fmt:
                            self._receiver_format[rid] = "video"
                        elif FORMAT_AUDIO in fmt:
                            self._receiver_format[rid] = "audio"
                        else:
                            continue
                        self._active_state[rid]     = None
                        self._activation_times[rid] = None
                        log.info("Discovered new receiver %s (%s)", rid, self._receiver_format[rid])
                polls = 0

            for rid, fmt in list(self._receiver_format.items()):
                data = _fetch(f"{self._conn}/single/receivers/{rid}/active")
                if data is None:
                    continue

                master_enable   = data.get("master_enable", False)
                prev            = self._active_state.get(rid)
                tp0             = (data.get("transport_params") or [{}])[0]
                flow_id         = tp0.get("mxl_flow_id") or None
                activation_time = data.get("activation", {}).get("activation_time")
                prev_act_time   = self._activation_times.get(rid)

                if prev is None:
                    log.info("Receiver %s (%s): initial state %s", rid, fmt,
                             "ACTIVE" if master_enable else "INACTIVE")
                    if master_enable and flow_id:
                        self._fire_connect(fmt, flow_id)

                elif master_enable and not prev:
                    log.info("Receiver %s (%s) became ACTIVE, flow_id=%s", rid, fmt, flow_id)
                    if flow_id:
                        self._fire_connect(fmt, flow_id)

                elif not master_enable and prev:
                    log.info("Receiver %s (%s) became INACTIVE", rid, fmt)
                    self._fire_disconnect(fmt)

                elif master_enable and prev and activation_time and activation_time != prev_act_time:
                    log.info("Receiver %s (%s) re-activated, new flow_id=%s", rid, fmt, flow_id)
                    self._fire_disconnect(fmt)
                    if flow_id:
                        self._fire_connect(fmt, flow_id)

                self._active_state[rid]     = master_enable
                self._activation_times[rid] = activation_time

            polls += 1
            time.sleep(self._poll_interval)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _fire_connect(self, fmt: str, flow_id: str) -> None:
        cb = self._on_connect_video if fmt == "video" else self._on_connect_audio
        if cb:
            try:
                cb(flow_id)
            except Exception as exc:
                log.error("on_connect_%s error: %s", fmt, exc)

    def _fire_disconnect(self, fmt: str) -> None:
        cb = self._on_disconnect_video if fmt == "video" else self._on_disconnect_audio
        if cb:
            try:
                cb()
            except Exception as exc:
                log.error("on_disconnect_%s error: %s", fmt, exc)

    def _list_mxl_resources(self, rtype: str) -> list[str]:
        data = _fetch(f"{self._conn}/single/{rtype}")
        if not data:
            return []
        ids = []
        for entry in data:
            rid   = entry.rstrip("/")
            ttype = _fetch(f"{self._conn}/single/{rtype}/{rid}/transporttype")
            if ttype == MXL_TRANSPORT:
                ids.append(rid)
        return ids
