#!/bin/bash
# SPDX-FileCopyrightText: 2025 CBC/Radio-Canada
# SPDX-License-Identifier: Apache-2.0
#
# Entrypoint for the MXL-to-WebRTC Gateway container.
# FastAPI serves both the REST API and the built React frontend on port 9600.

set -e

# The x264enc H.264 encoder (gstreamer1.0-plugins-ugly) is GPL-licensed and
# patent-encumbered, so it is not shipped in the published image. Install it
# from the Ubuntu archive at first start instead (needs network access; the
# resulting GPL combined work stays on the deployer's machine).
if ! gst-inspect-1.0 x264enc >/dev/null 2>&1; then
    echo "x264enc not found — installing gstreamer1.0-plugins-ugly..."
    apt-get update
    apt-get install -y --no-install-recommends gstreamer1.0-plugins-ugly
    rm -rf /var/lib/apt/lists/*
fi

cd /app
exec python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 9600
