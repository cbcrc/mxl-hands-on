#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025 Contributors to the Media eXchange Layer project.
# SPDX-License-Identifier: Apache-2.0
#
# Test the GStreamer MXL plugin (mxlsink/mxlsrc) for several video resolutions.
# For each resolution:
#   1. A videotestsrc pipeline writes frames into an MXL flow via mxlsink.
#   2. A mxlsrc pipeline reads the flow back and verifies the caps match
#      the expected resolution using a capsfilter. If caps negotiation fails,
#      the resolution is wrong and the test is marked as FAILED.
#   3. After the test (pass or fail) the flow directory is removed from the
#      MXL domain so the domain does not accumulate stale data across runs.
#      Flow data lives at {domain}/{flow-id}/ per the MXL flowinfo spec.
#
# Usage (normally called via docker compose):
#   ./test-gst-mxl-resolution.sh [MXL_DOMAIN]
#
# MXL_DOMAIN defaults to /Volumes/mxl

set -euo pipefail

DOMAIN="${1:-/Volumes/mxl}"
FRAMERATE="25/1"
# How many buffers the reader must successfully receive to call a test PASSED.
NUM_BUFFERS_READ=30
# Seconds to wait after starting the writer before starting the reader,
# giving the writer time to create and publish the flow.
WRITER_STARTUP_SLEEP=3

PASS=0
FAIL=0
WRITER_PID=""
# Track every flow ID created so the EXIT trap can purge any that remain
# if the script is interrupted mid-test.
CREATED_FLOW_IDS=()

# ---------------------------------------------------------------------------
# purge_flow <flow_id>
#   Removes the flow directory from the domain.  Both writer and reader must
#   have exited before this is called so no advisory locks remain on the files.
# ---------------------------------------------------------------------------
purge_flow() {
    local flow_id="$1"
    local flow_dir="${DOMAIN}/${flow_id}.mxl-flow"
    if [ -d "${flow_dir}" ]; then
        rm -rf "${flow_dir}"
        echo "  (flow ${flow_id} removed from domain)"
    fi
}

# ---------------------------------------------------------------------------
# Cleanup: kill any still-running writer and purge all flows created so far.
# Called on EXIT, INT, and TERM so the domain is always left clean.
# ---------------------------------------------------------------------------
cleanup() {
    if [ -n "${WRITER_PID}" ]; then
        kill "${WRITER_PID}" 2>/dev/null || true
        wait "${WRITER_PID}" 2>/dev/null || true
        WRITER_PID=""
    fi
    for fid in "${CREATED_FLOW_IDS[@]:-}"; do
        [ -n "${fid}" ] && purge_flow "${fid}"
    done
}
trap cleanup EXIT INT TERM

# ---------------------------------------------------------------------------
# Generate a random UUID without relying on uuidgen.
# /proc/sys/kernel/random/uuid returns a fresh UUID on every read.
# ---------------------------------------------------------------------------
new_uuid() {
    cat /proc/sys/kernel/random/uuid
}

# ---------------------------------------------------------------------------
# run_resolution_test <name> <width> <height>
#   Returns 0 on PASS, 1 on FAIL.
# ---------------------------------------------------------------------------
run_resolution_test() {
    local name="$1"
    local width="$2"
    local height="$3"
    local flow_id
    flow_id="$(new_uuid)"
    # Register flow ID so the EXIT trap cleans it up even on early abort.
    CREATED_FLOW_IDS+=("${flow_id}")

    echo ""
    echo "========================================================"
    echo " Testing ${name} — ${width}x${height}  flow: ${flow_id}"
    echo "========================================================"

    # --- Writer ---------------------------------------------------------------
    # videotestsrc produces a colour-bar pattern at the requested resolution.
    # videoconvert converts to v210 (the only format accepted by mxlsink).
    gst-launch-1.0 -q \
        videotestsrc pattern=smpte \
        ! "video/x-raw,width=${width},height=${height},framerate=${FRAMERATE}" \
        ! videoconvert \
        ! "video/x-raw,format=v210" \
        ! queue \
        ! mxlsink flow-id="${flow_id}" domain="${DOMAIN}" \
        &
    WRITER_PID=$!

    # Give the writer time to create and register the flow in the MXL domain.
    sleep "${WRITER_STARTUP_SLEEP}"

    # Check the writer is still alive before starting the reader.
    if ! kill -0 "${WRITER_PID}" 2>/dev/null; then
        echo "FAIL: ${name} — writer process died before reader could start"
        WRITER_PID=""
        FAIL=$((FAIL + 1))
        purge_flow "${flow_id}"
        return 1
    fi

    # --- Reader ---------------------------------------------------------------
    # The capsfilter "video/x-raw,format=v210,width=W,height=H" forces caps
    # negotiation to the exact expected resolution.  If mxlsrc provides a
    # different resolution the pipeline cannot negotiate and exits non-zero.
    local reader_exit=0
    gst-launch-1.0 -q \
        mxlsrc video-flow-id="${flow_id}" domain="${DOMAIN}" \
        ! "video/x-raw,format=v210,width=${width},height=${height}" \
        ! fakesink num-buffers="${NUM_BUFFERS_READ}" sync=false \
        || reader_exit=$?

    # --- Stop writer and purge flow ------------------------------------------
    # Both writer and reader have now released their locks on the flow files,
    # so it is safe to delete the flow directory from the domain.
    kill "${WRITER_PID}" 2>/dev/null || true
    wait "${WRITER_PID}" 2>/dev/null || true
    WRITER_PID=""
    purge_flow "${flow_id}"

    # --- Report ---------------------------------------------------------------
    if [ "${reader_exit}" -eq 0 ]; then
        echo "PASS: ${name} (${width}x${height})"
        PASS=$((PASS + 1))
        return 0
    else
        echo "FAIL: ${name} (${width}x${height}) — reader exited with code ${reader_exit}"
        FAIL=$((FAIL + 1))
        return 1
    fi
}

# ---------------------------------------------------------------------------
# Main: run tests in order; a failure is reported but does not stop the suite
# ---------------------------------------------------------------------------
echo ""
echo "MXL GStreamer Resolution Test Suite"
echo "Domain : ${DOMAIN}"
echo "Format : v210 @ ${FRAMERATE} fps"

run_resolution_test "720p"        1280  720  || true
run_resolution_test "1080p"       1920  1080 || true
run_resolution_test "4K UHD"      3840  2160 || true
run_resolution_test "1024 Square" 1024  1024 || true

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
TOTAL=$((PASS + FAIL))
echo ""
echo "========================================================"
echo " Results: ${PASS}/${TOTAL} passed"
echo "========================================================"

if [ "${FAIL}" -gt 0 ]; then
    exit 1
fi
exit 0

