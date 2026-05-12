#!/bin/bash
# SPDX-FileCopyrightText: 2025 Contributors to the Media eXchange Layer project.
# SPDX-License-Identifier: Apache-2.0
#
# Entrypoint for the MXL HTML5 Keyer container.
# Starts:
#   1. mDNS responder (mdnsd)  – needed for NMOS DNS-SD advertisement
#   2. Xvfb                    – virtual X11 display required by glvideomixer + CEF
#   3. nmos-cpp-node           – NMOS IS-04/IS-05 node on port 9540
#   4. Static file server      – React frontend on port 9740
#   5. FastAPI backend          – HTML5 Keyer API on port 9640 (foreground)

set -e

# 1. Start mDNS responder if present
if command -v mdnsd &>/dev/null; then
    echo "[entrypoint] Starting mdnsd..."
    mdnsd &
    sleep 1
fi

# 2. Start Xvfb (virtual display for glvideomixer / CEF headless rendering)
echo "[entrypoint] Starting Xvfb on :99..."
Xvfb :99 -screen 0 1920x1080x24 &
export DISPLAY=:99
sleep 1

# 3. Start NMOS node
echo "[entrypoint] Starting nmos-cpp-node on port 9540..."
/home/nmos-cpp-node /home/node.json &

# Wait until the NMOS node answers
echo "[entrypoint] Waiting for NMOS node..."
for i in $(seq 1 30); do
    if curl -sf "http://localhost:9540/x-nmos/node/v1.3/" > /dev/null 2>&1; then
        echo "[entrypoint] NMOS node ready."
        break
    fi
    sleep 1
done

# 4. Serve the built React frontend on port 9740
echo "[entrypoint] Starting frontend static server on port 9740..."
python3 -m http.server 9740 --directory /app/frontend/dist &

# 5. Start FastAPI backend (foreground – keeps the container alive)
echo "[entrypoint] Starting FastAPI backend on port 9640..."
cd /app
exec python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 9640
