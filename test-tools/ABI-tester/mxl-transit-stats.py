#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
# SPDX-License-Identifier: Apache-2.0
"""Transit-time statistics from ABI-tester NDJSON event logs.

Reads the files written by ABI_TESTER_LOG_FILE and reports, per (file, lane, call),
the distribution of `stamp.transit_ms` -- the reader's wall clock minus the writer's
mxlGetTime() stamped into the payload.

Usage:
    ./mxl-transit-stats.py logs/transit-local.ndjson
    ./mxl-transit-stats.py logs/transit-local.ndjson logs/transit-remote.ndjson
    ./mxl-transit-stats.py --lane B logs/*.ndjson

Only reader events with `verify_stamp: true` carry a stamp, so writer lanes simply
report no samples. Stdlib only -- it runs on the far host with nothing installed.
"""

import argparse
import json
import sys
from collections import Counter, defaultdict


def percentile(sorted_values, fraction):
    """Linear-interpolated percentile, the same convention as numpy's default."""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]

    position = fraction * (len(sorted_values) - 1)
    low = int(position)
    high = min(low + 1, len(sorted_values) - 1)
    weight = position - low
    return sorted_values[low] * (1.0 - weight) + sorted_values[high] * weight


class Group:
    """One (file, lane, call) population."""

    def __init__(self):
        self.transit = []
        self.age = []
        self.indices = []
        self.statuses = Counter()
        self.mismatched = 0
        self.no_stamp = 0

    def add(self, event):
        # A step that never reached the ABI has no "status"; it has "ok": false and
        # an "error". Both belong in the tally, under a name that says which.
        self.statuses[event.get("status", "ok" if event.get("ok") else "step-error")] += 1

        if not event.get("ok"):
            return

        if "index" in event:
            self.indices.append(event["index"])
        if "age_ms" in event:
            self.age.append(event["age_ms"])

        stamp = event.get("stamp")
        if not isinstance(stamp, dict) or not stamp.get("valid"):
            self.no_stamp += 1
            return
        if stamp.get("matches") is False:
            self.mismatched += 1
        if "transit_ms" in stamp:
            self.transit.append(stamp["transit_ms"])

    @property
    def read_ok(self):
        return self.statuses.get("MXL_STATUS_OK", 0) + self.statuses.get("ok", 0)

    @property
    def read_failed(self):
        return sum(self.statuses.values()) - self.read_ok


def line(label, values):
    if not values:
        return "  %-11s (no samples)" % label
    values = sorted(values)
    return "  %-11s min %8.3f  p10 %8.3f  p50 %8.3f  p90 %8.3f  max %8.3f   mean %8.3f" % (
        label,
        values[0],
        percentile(values, 0.10),
        percentile(values, 0.50),
        percentile(values, 0.90),
        values[-1],
        sum(values) / len(values),
    )


def report(key, group):
    path, lane, call = key
    print("%s  lane %s  %s" % (path, lane, call))
    print("  %-11s %d ok, %d failed" % ("calls", group.read_ok, group.read_failed))

    if group.read_failed:
        failures = ", ".join(
            "%s x%d" % (name, count)
            for name, count in group.statuses.most_common()
            if name not in ("MXL_STATUS_OK", "ok")
        )
        print("  %-11s %s" % ("", failures))

    if group.indices:
        span = max(group.indices) - min(group.indices) + 1
        missing = span - len(set(group.indices))
        print(
            "  %-11s %d..%d, %d read, %d index%s not read"
            % (
                "indices",
                min(group.indices),
                max(group.indices),
                len(group.indices),
                missing,
                "" if missing == 1 else "es",
            )
        )

    if group.no_stamp or group.mismatched:
        print(
            "  %-11s %d without a valid stamp, %d stamped with another index"
            % ("stamps", group.no_stamp, group.mismatched)
        )

    print(line("transit_ms", group.transit))
    print(line("age_ms", group.age))
    print()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("files", nargs="+", metavar="FILE", help="NDJSON event log(s)")
    parser.add_argument("--lane", action="append", help="keep only this lane; repeatable")
    args = parser.parse_args()

    groups = defaultdict(Group)
    malformed = 0

    for path in args.files:
        with open(path) as handle:
            for text in handle:
                text = text.strip()
                if not text:
                    continue
                try:
                    event = json.loads(text)
                except json.JSONDecodeError:
                    malformed += 1   # a torn last line: the flusher was mid-write
                    continue

                lane = event.get("lane", "?")
                if args.lane and lane not in args.lane:
                    continue
                if "stamp" not in event and "age_ms" not in event:
                    continue         # writer steps, setCursor, instance creation

                groups[(path, lane, event.get("call", "?"))].add(event)

    if malformed:
        print("warning: %d unparseable line(s) skipped\n" % malformed, file=sys.stderr)

    if not groups:
        print("no reader events found; was verify_stamp set on the read steps?",
              file=sys.stderr)
        return 1

    for key in sorted(groups):
        report(key, groups[key])

    # A negative transit is physically impossible, so it proves the two hosts' clocks
    # disagree -- and a positive one from the same pair is offset by the same amount.
    if any(g.transit and min(g.transit) < 0 for g in groups.values()):
        print("note: a negative transit_ms means the reader's clock is behind the "
              "writer's. Cross-host numbers are only transit if both ends are "
              "disciplined to the same PTP/TAI source.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
