#!/bin/bash
# Entrypoint for the MXL HTML5 Keyer container.
#
# CEF/Chromium needs an X display on Linux even when it runs offscreen, so we
# start an Xvfb (X virtual framebuffer) before launching uvicorn.  The X
# display is local-only (Unix socket); TCP listening is disabled.

set -e

XVFB_DISPLAY=":99"

# Clear any stale Xvfb lock/socket from a prior crashed run in case Docker
# kept the container's /tmp around (e.g. on restart-without-recreate).
rm -f /tmp/.X99-lock
mkdir -p /tmp/.X11-unix
rm -f /tmp/.X11-unix/X99

# Start Xvfb in the background.  +extension GLX enables OpenGL (used by
# Chromium's software GL fallback).  Resolution chosen large enough that any
# CEF page can render at up to UHD without truncation.
Xvfb "${XVFB_DISPLAY}" \
    -screen 0 3840x2160x24 \
    +extension GLX +extension RANDR +extension RENDER \
    -nolisten tcp &
export DISPLAY="${XVFB_DISPLAY}"

# Wait for the Xvfb socket to appear before launching anything that uses it.
for i in 1 2 3 4 5 6 7 8 9 10; do
    if [ -e /tmp/.X11-unix/X99 ]; then
        break
    fi
    sleep 0.2
done

cd /app
exec python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 9600
