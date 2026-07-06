#!/bin/bash
# SPDX-FileCopyrightText: 2025 Contributors to the Media eXchange Layer project.
# SPDX-License-Identifier: Apache-2.0
#
# Entrypoint for the MXL File Player container.
# FastAPI serves both the REST API and the built React frontend on port 9600.

set -e

# The FFmpeg-based decoders (gstreamer1.0-libav) are not shipped in the
# published image: Ubuntu's FFmpeg build enables GPL components and
# hard-depends on the patent-encumbered libx264 encoder library. Install
# from the Ubuntu archive at first start instead (needs network access; the
# GPL combined work stays on the deployer's machine).
if ! gst-inspect-1.0 avdec_h264 >/dev/null 2>&1; then
    echo "avdec_h264 not found — installing gstreamer1.0-libav..."
    apt-get update
    apt-get install -y --no-install-recommends gstreamer1.0-libav
    rm -rf /var/lib/apt/lists/*
fi

cd /app
exec python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 9600
