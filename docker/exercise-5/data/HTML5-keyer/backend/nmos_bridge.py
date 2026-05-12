"""
NMOS IS-05 bridge for the MXL HTML5 Keyer.

Responsibilities:
  1. Discover the single video sender → retrieve output flow ID for mxlsink.
  2. Discover the single video receiver.
  3. Poll the receiver's /active endpoint:
       - master_enable=true  + mxl_flow_id  →  call on_connect(flow_id)
       - master_enable=false (was true)      →  call on_disconnect()
       - re-activation (activation_time changed) → disconnect then reconnect
"""

from __future__ import annotations

import logging
import time
from typing import Callable

import requests

log = logging.getLogger(__name__)

MXL_TRANSPORT = "urn:x-nmos:transport:mxl"


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
        nmos_base: str = "http://localhost:9540",
        poll_interval: float = 1.0,
        rediscover_every: int = 30,
        on_connect: Callable[[str], None] | None = None,
        on_disconnect: Callable[[], None] | None = None,
    ) -> None:
        self._base = nmos_base.rstrip("/")
        self._conn = f"{self._base}/x-nmos/connection/v1.2"
        self._node = f"{self._base}/x-nmos/node/v1.3"
        self._poll_interval    = poll_interval
        self._rediscover_every = rediscover_every
        self._on_connect    = on_connect
        self._on_disconnect = on_disconnect

        # receiver UUID → last known master_enable (None = unknown)
        self._receiver_id:    str | None = None
        self._active_state:   bool | None = None
        self._activation_time: str | None = None

    # ── Public API ────────────────────────────────────────────────────────────

    def discover_output_flow_id(self) -> str | None:
        """Wait for the MXL sender and return its flow ID."""
        for attempt in range(30):
            ids = self._list_mxl_resources("senders")
            if ids:
                sender_id = sorted(ids)[0]
                sender = _fetch(f"{self._node}/senders/{sender_id}")
                if sender:
                    flow_id = sender.get("flow_id")
                    if flow_id:
                        log.info("Output flow ID (sender %s): %s", sender_id, flow_id)
                        return flow_id
            log.info("Waiting for NMOS sender… (attempt %d)", attempt + 1)
            time.sleep(1)
        log.warning("Output flow ID not discovered after 30 s")
        return None

    def discover_receiver(self) -> str | None:
        """Return the receiver UUID. Waits up to 30 s."""
        for attempt in range(30):
            ids = self._list_mxl_resources("receivers")
            if ids:
                rid = sorted(ids)[0]
                self._receiver_id  = rid
                self._active_state = None
                self._activation_time = None
                log.info("Discovered receiver: %s", rid)
                return rid
            log.info("Waiting for NMOS receiver… (attempt %d)", attempt + 1)
            time.sleep(1)
        log.warning("No receiver found after 30 s")
        return None

    def run(self) -> None:
        """Poll loop – blocks forever. Run in a daemon thread."""
        log.info("NMOS bridge polling %s", self._conn)
        polls = self._rediscover_every  # force initial discovery

        while True:
            # Periodic re-discovery (in case the receiver UUID changes)
            if polls >= self._rediscover_every:
                ids = self._list_mxl_resources("receivers")
                if ids and self._receiver_id not in ids:
                    self._receiver_id  = sorted(ids)[0]
                    self._active_state = None
                    self._activation_time = None
                    log.info("Re-discovered receiver: %s", self._receiver_id)
                polls = 0

            if self._receiver_id:
                self._poll_receiver(self._receiver_id)

            polls += 1
            time.sleep(self._poll_interval)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _poll_receiver(self, rid: str) -> None:
        data = _fetch(f"{self._conn}/single/receivers/{rid}/active")
        if data is None:
            return

        master_enable   = data.get("master_enable", False)
        tp0             = (data.get("transport_params") or [{}])[0]
        flow_id         = tp0.get("mxl_flow_id") or None
        activation_time = data.get("activation", {}).get("activation_time")
        prev            = self._active_state
        prev_act_time   = self._activation_time

        if prev is None:
            log.info("Receiver initial state: %s", "ACTIVE" if master_enable else "INACTIVE")
            if master_enable and flow_id and self._on_connect:
                try:
                    self._on_connect(flow_id)
                except Exception as exc:
                    log.error("on_connect error (initial): %s", exc)

        elif master_enable and not prev:
            log.info("Receiver became ACTIVE, flow_id=%s", flow_id)
            if flow_id and self._on_connect:
                try:
                    self._on_connect(flow_id)
                except Exception as exc:
                    log.error("on_connect error: %s", exc)

        elif not master_enable and prev:
            log.info("Receiver became INACTIVE")
            if self._on_disconnect:
                try:
                    self._on_disconnect()
                except Exception as exc:
                    log.error("on_disconnect error: %s", exc)

        elif (
            master_enable and prev
            and activation_time
            and activation_time != prev_act_time
        ):
            log.info("Receiver re-activated, new flow_id=%s", flow_id)
            if self._on_disconnect:
                try:
                    self._on_disconnect()
                except Exception as exc:
                    log.error("on_disconnect error (re-activate): %s", exc)
            if flow_id and self._on_connect:
                try:
                    self._on_connect(flow_id)
                except Exception as exc:
                    log.error("on_connect error (re-activate): %s", exc)

        self._active_state    = master_enable
        self._activation_time = activation_time

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
