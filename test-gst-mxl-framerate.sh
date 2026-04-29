#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025 Contributors to the Media eXchange Layer project.
# SPDX-License-Identifier: Apache-2.0
#
# Test the GStreamer MXL plugin (mxlsink/mxlsrc) for multiple frame rates.
#
# Unlike the resolution test (one flow at a time), this test starts ALL writers
# simultaneously so every flow is present in the domain at the same time.
# Readers then verify each flow one by one while all writers are still running.
#
# Frame rates tested: 24p, 25p, 29.97p, 30p, 50p, 59.94p, 60p
#
# Usage (normally called via docker compose):
#   ./test-gst-mxl-framerate.sh [MXL_DOMAIN]
#
# MXL_DOMAIN defaults to /Volumes/mxl

set -euo pipefail

DOMAIN="${1:-/Volumes/mxl}"

# Fixed resolution used for all frame rate tests (width x height)
VIDEO_WIDTH=1280
VIDEO_HEIGHT=720

# How many buffers the reader must receive successfully to call a test PASSED.
NUM_BUFFERS_READ=30
# Seconds to wait after starting ALL writers before beginning to read,
# giving every writer time to create and publish its flow.
WRITERS_STARTUP_SLEEP=5

# ---------------------------------------------------------------------------
# Frame rate definitions.
# 29.97 = 30000/1001  and  59.94 = 60000/1001  (SMPTE drop-frame equivalents)
# ---------------------------------------------------------------------------
NAMES=(     "24p"   "25p"   "29.97p"      "30p"   "50p"   "59.94p"       "60p"  )
FRAMERATES=("24/1"  "25/1"  "30000/1001"  "30/1"  "50/1"  "60000/1001"   "60/1" )

FLOW_IDS=()
WRITER_PIDS=()
PASS=0
FAIL=0

# ---------------------------------------------------------------------------
# Generate a random UUID from the kernel without relying on uuidgen.
# ---------------------------------------------------------------------------
new_uuid() {
    cat /proc/sys/kernel/random/uuid
}

# ---------------------------------------------------------------------------
# purge_all_flows: remove every flow directory created by this run.
# Flow directories are named {flow-id}.mxl-flow per the MXL domain layout.
# ---------------------------------------------------------------------------
purge_all_flows() {
    for fid in "${FLOW_IDS[@]:-}"; do
        [ -z "${fid}" ] && continue
        local flow_dir="${DOMAIN}/${fid}.mxl-flow"
        if [ -d "${flow_dir}" ]; then
            rm -rf "${flow_dir}"
            echo "  (flow ${fid} removed from domain)"
        fi
    done
}

# ---------------------------------------------------------------------------
# cleanup: kill all background writers and purge all flows.
# Called on EXIT, INT, and TERM so the domain is always left clean.
# ---------------------------------------------------------------------------
cleanup() {
    echo ""
    echo "Stopping all writers..."
    for pid in "${WRITER_PIDS[@]:-}"; do
        [ -n "${pid}" ] && kill "${pid}" 2>/dev/null || true
    done
    for pid in "${WRITER_PIDS[@]:-}"; do
        [ -n "${pid}" ] && wait "${pid}" 2>/dev/null || true
    done
    echo "Purging flows from domain..."
    purge_all_flows
}
trap cleanup EXIT INT TERM

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
echo ""
echo "MXL GStreamer Frame Rate Test Suite"
echo "Domain    : ${DOMAIN}"
echo "Format    : v210  ${VIDEO_WIDTH}x${VIDEO_HEIGHT}"
echo "Frame rates: ${NAMES[*]}"

# --- Phase 1: Start ALL writers simultaneously ------------------------------
echo ""
echo "--- Phase 1: Starting all writers ---"
for i in "${!NAMES[@]}"; do
    local_flow_id="$(new_uuid)"
    FLOW_IDS+=("${local_flow_id}")

    gst-launch-1.0 -q \
        videotestsrc pattern=smpte \
        ! "video/x-raw,width=${VIDEO_WIDTH},height=${VIDEO_HEIGHT},framerate=${FRAMERATES[$i]}" \
        ! videoconvert \
        ! "video/x-raw,format=v210" \
        ! queue \
        ! mxlsink flow-id="${local_flow_id}" domain="${DOMAIN}" \
        &
    WRITER_PIDS+=($!)
    echo "  [${NAMES[$i]}]  ${FRAMERATES[$i]} fps  →  flow: ${local_flow_id}"
done

echo ""
echo "Waiting ${WRITERS_STARTUP_SLEEP}s for all flows to be established..."
sleep "${WRITERS_STARTUP_SLEEP}"

# Verify every writer is still alive before reading
echo ""
echo "Checking all writers are alive..."
all_alive=true
for i in "${!NAMES[@]}"; do
    if ! kill -0 "${WRITER_PIDS[$i]}" 2>/dev/null; then
        echo "  ERROR: writer for ${NAMES[$i]} died during startup — aborting"
        all_alive=false
    fi
done
if [ "${all_alive}" = false ]; then
    exit 1
fi
echo "  All ${#NAMES[@]} writers are running."

# --- Phase 2: Read and verify each flow one by one --------------------------
echo ""
echo "--- Phase 2: Verifying each flow ---"
for i in "${!NAMES[@]}"; do
    name="${NAMES[$i]}"
    framerate="${FRAMERATES[$i]}"
    flow_id="${FLOW_IDS[$i]}"

    echo ""
    echo "========================================================"
    echo " Verifying ${name} (${framerate} fps)"
    echo " flow: ${flow_id}"
    echo "========================================================"

    # The capsfilter enforces both the format and the exact framerate fraction.
    # If mxlsrc reports a different framerate the pipeline cannot negotiate
    # and gst-launch exits non-zero → FAIL.
    reader_exit=0
    gst-launch-1.0 -q \
        mxlsrc video-flow-id="${flow_id}" domain="${DOMAIN}" \
        ! "video/x-raw,format=v210,framerate=${framerate}" \
        ! fakesink num-buffers="${NUM_BUFFERS_READ}" sync=false \
        || reader_exit=$?

    if [ "${reader_exit}" -eq 0 ]; then
        echo "PASS: ${name} (${framerate} fps)"
        PASS=$((PASS + 1))
    else
        echo "FAIL: ${name} (${framerate} fps) — reader exited with code ${reader_exit}"
        FAIL=$((FAIL + 1))
    fi
done

# --- Summary ----------------------------------------------------------------
TOTAL=$((PASS + FAIL))
echo ""
echo "========================================================"
echo " Results: ${PASS}/${TOTAL} passed"
echo "========================================================"

# cleanup trap handles writers + domain purge on exit
if [ "${FAIL}" -gt 0 ]; then
    exit 1
fi
exit 0
