"""
NMOS IS-05 bridge for the MXL Input Selector.

Responsibilities:
  1. Discover the single video sender → retrieve output flow ID for mxlsink.
  2. Discover the three video receivers (in stable order → slots 1/2/3).
  3. Poll each receiver's /active endpoint:
       - master_enable=true  + mxl_flow_id  →  call on_connect_input(slot, flow_id)
       - master_enable=false (was true)      →  call on_disconnect_input(slot)
       - re-activation (activation_time changed) → disconnect then reconnect

Receiver → slot mapping is determined once at discovery and kept stable for the
lifetime of the process (receivers sorted by UUID for determinism).
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


def _patch(url: str, body: dict, timeout: float = 2.0) -> bool:
    try:
        resp = requests.patch(url, json=body, timeout=timeout)
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        log.warning("PATCH error %s: %s", url, exc)
        return False


class NmosBridge:
    def __init__(
        self,
        nmos_base: str = "http://localhost:9530",
        poll_interval: float = 1.0,
        rediscover_every: int = 30,
        on_connect_input: Callable[[int, str], None] | None = None,
        on_disconnect_input: Callable[[int], None] | None = None,
    ) -> None:
        self._base = nmos_base.rstrip("/")
        self._conn = f"{self._base}/x-nmos/connection/v1.2"
        self._node = f"{self._base}/x-nmos/node/v1.3"
        self._poll_interval    = poll_interval
        self._rediscover_every = rediscover_every
        self._on_connect    = on_connect_input
        self._on_disconnect = on_disconnect_input

        # receiver UUID → slot number (1/2/3), set at discovery time
        self._receiver_slots: dict[str, int] = {}
        # receiver UUID → last known active state (None=unknown, True/False)
        self._active_state: dict[str, bool | None] = {}
        # receiver UUID → last seen activation_time (for re-activation detection)
        self._activation_times: dict[str, str | None] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def discover_output_flow_id(self) -> str | None:
        """Wait for MXL senders and return the flow ID of the first one (sorted by UUID)."""
        for attempt in range(30):
            ids = self._list_mxl_resources("senders")
            if ids:
                # Sort for determinism; always use the first sender as output
                ids_sorted = sorted(ids)
                sender = _fetch(f"{self._node}/senders/{ids_sorted[0]}")
                if sender:
                    flow_id = sender.get("flow_id")
                    if flow_id:
                        log.info("Output flow ID (sender %s): %s", ids_sorted[0], flow_id)
                        return flow_id
            log.info("Waiting for NMOS sender… (attempt %d)", attempt + 1)
            time.sleep(1)
        log.warning("Output flow ID not discovered after 30 s")
        return None

    def discover_receivers(self) -> list[str]:
        """Return receiver UUIDs sorted deterministically. Populates slot map."""
        for attempt in range(30):
            ids = self._list_mxl_resources("receivers")
            if len(ids) >= 3:
                break
            log.info("Waiting for 3 NMOS receivers… got %d (attempt %d)", len(ids), attempt + 1)
            time.sleep(1)
        else:
            log.warning("Only %d receiver(s) found after 30 s", len(ids))

        # Sort by IS-04 label (xv0 < xv1 < xv2) so slot 1=xv0, 2=xv1, 3=xv2
        def _label_key(rid: str) -> str:
            data = _fetch(f"{self._node}/receivers/{rid}")
            return (data or {}).get("label", rid)

        ids_sorted = sorted(ids, key=_label_key)
        self._receiver_slots = {rid: (i + 1) for i, rid in enumerate(ids_sorted)}
        self._active_state   = {rid: None for rid in ids_sorted}
        self._activation_times = {rid: None for rid in ids_sorted}
        log.info("Receiver → slot mapping: %s", self._receiver_slots)
        return ids_sorted

    def run(self) -> None:
        """Poll loop – blocks forever. Run in a daemon thread."""
        log.info("NMOS bridge polling %s", self._conn)
        polls = self._rediscover_every  # force initial discovery

        while True:
            if polls >= self._rediscover_every:
                ids = self._list_mxl_resources("receivers")
                # Add any newly discovered receivers; keep existing slot assignments
                for rid in sorted(ids):
                    if rid not in self._receiver_slots:
                        slot = len(self._receiver_slots) + 1
                        self._receiver_slots[rid] = slot
                        self._active_state[rid]   = None
                        self._activation_times[rid] = None
                        log.info("Discovered new receiver %s → slot %d", rid, slot)
                polls = 0

            for rid, slot in list(self._receiver_slots.items()):
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
                    log.info("Receiver slot %d: initial state %s", slot,
                             "ACTIVE" if master_enable else "INACTIVE")
                    # Trigger connect immediately if already active at startup
                    if master_enable and flow_id and self._on_connect:
                        try:
                            self._on_connect(slot, flow_id)
                        except Exception as exc:
                            log.error("on_connect_input error (initial): %s", exc)

                elif master_enable and not prev:
                    # Became active
                    log.info("Receiver slot %d became ACTIVE, flow_id=%s", slot, flow_id)
                    if flow_id and self._on_connect:
                        try:
                            self._on_connect(slot, flow_id)
                        except Exception as exc:
                            log.error("on_connect_input error: %s", exc)

                elif not master_enable and prev:
                    # Became inactive
                    log.info("Receiver slot %d became INACTIVE", slot)
                    if self._on_disconnect:
                        try:
                            self._on_disconnect(slot)
                        except Exception as exc:
                            log.error("on_disconnect_input error: %s", exc)

                elif master_enable and prev and activation_time and activation_time != prev_act_time:
                    # Re-activated with a possibly different flow
                    log.info("Receiver slot %d re-activated, new flow_id=%s", slot, flow_id)
                    if self._on_disconnect:
                        try:
                            self._on_disconnect(slot)
                        except Exception as exc:
                            log.error("on_disconnect_input error (re-activate): %s", exc)
                    if flow_id and self._on_connect:
                        try:
                            self._on_connect(slot, flow_id)
                        except Exception as exc:
                            log.error("on_connect_input error (re-activate): %s", exc)

                self._active_state[rid]    = master_enable
                self._activation_times[rid] = activation_time

            polls += 1
            time.sleep(self._poll_interval)

    # ── Helpers ───────────────────────────────────────────────────────────────

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
