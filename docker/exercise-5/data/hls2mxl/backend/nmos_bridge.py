"""
NMOS IS-05 bridge for the MXL Test Generator.

Identical in structure to the file-player's nmos_bridge.py but adapted for
the test-generator: the generator is always "on" so activate/deactivate just
update the NMOS sender active state rather than starting/stopping a pipeline.
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
        nmos_base: str = "http://localhost:9520",
        poll_interval: float = 1.0,
        rediscover_every: int = 30,
        on_activate: Callable | None = None,
        on_deactivate: Callable | None = None,
    ) -> None:
        self._base = nmos_base.rstrip("/")
        self._conn = f"{self._base}/x-nmos/connection/v1.2"
        self._node = f"{self._base}/x-nmos/node/v1.3"
        self._poll_interval   = poll_interval
        self._rediscover_every = rediscover_every
        self._on_activate     = on_activate
        self._on_deactivate   = on_deactivate

        self._active_state: dict[str, bool | None] = {}
        self._flow_ids: dict[str, str] = {}

    def set_senders_active(self, active: bool) -> None:
        for key in list(self._active_state):
            rid = key.removeprefix("senders/")
            url  = f"{self._conn}/single/senders/{rid}/staged"
            body = {"master_enable": active, "activation": {"mode": "activate_immediate"}}
            if _patch(url, body):
                log.info("Set sender %s master_enable=%s", rid, active)
                self._active_state[key] = active

    def discover_flow_ids(self) -> dict[str, str]:
        for attempt in range(30):
            ids = self._list_mxl_senders()
            if ids:
                break
            log.info("Waiting for NMOS node… (attempt %d)", attempt + 1)
            time.sleep(1)
        else:
            log.warning("NMOS node not reachable after 30 s – no flow IDs discovered")
            return {}

        result: dict[str, str] = {}
        for rid in ids:
            sender = _fetch(f"{self._node}/senders/{rid}")
            if not sender:
                continue
            flow_id = sender.get("flow_id")
            if not flow_id:
                continue
            flow = _fetch(f"{self._node}/flows/{flow_id}")
            if not flow:
                continue
            fmt = flow.get("format", "")
            if "video" in fmt:
                result["video"] = flow_id
                log.info("Discovered video flow ID: %s", flow_id)
            elif "audio" in fmt:
                result["audio"] = flow_id
                log.info("Discovered audio flow ID: %s", flow_id)

        self._flow_ids = result
        return result

    def run(self) -> None:
        log.info("NMOS bridge starting (polling %s)", self._conn)
        polls = self._rediscover_every

        while True:
            if polls >= self._rediscover_every:
                resources = self._list_mxl_senders()
                self._active_state = {
                    f"senders/{rid}": self._active_state.get(f"senders/{rid}")
                    for rid in resources
                }
                polls = 0

            for key in list(self._active_state):
                data = _fetch(f"{self._conn}/single/{key}/active")
                if data is None:
                    continue

                master_enable = data.get("master_enable", False)
                prev = self._active_state[key]

                if prev is None:
                    log.info("%s initial state: %s", key, "ACTIVE" if master_enable else "INACTIVE")
                elif master_enable and not prev:
                    log.info("%s became ACTIVE", key)
                    if self._on_activate:
                        try:
                            self._on_activate()
                        except Exception as exc:
                            log.error("on_activate error: %s", exc)
                elif not master_enable and prev:
                    log.info("%s became INACTIVE", key)
                    if self._on_deactivate:
                        try:
                            self._on_deactivate()
                        except Exception as exc:
                            log.error("on_deactivate error: %s", exc)

                self._active_state[key] = master_enable

            polls += 1
            time.sleep(self._poll_interval)

    def _list_mxl_senders(self) -> list[str]:
        data = _fetch(f"{self._conn}/single/senders")
        if not data:
            return []
        ids = []
        for entry in data:
            rid   = entry.rstrip("/")
            ttype = _fetch(f"{self._conn}/single/senders/{rid}/transporttype")
            if ttype == MXL_TRANSPORT:
                ids.append(rid)
        return ids
