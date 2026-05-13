#!/bin/bash
# SPDX-FileCopyrightText: 2025 Contributors to the Media eXchange Layer project.
# SPDX-License-Identifier: Apache-2.0
#
# Entrypoint for the MXL-to-WebRTC Gateway container.
# Starts:
#   1. mDNS responder (mdnsd) – needed for NMOS DNS-SD advertisement
#   2. nmos-cpp-node           – NMOS IS-04/IS-05 node on port 9550
#   3. Static file server      – React frontend on port 9750
#   4. FastAPI backend          – MXL2WebRTC API on port 9650 (foreground)

set -e

if command -v mdnsd &>/dev/null; then
    echo "[entrypoint] Starting mdnsd..."
    mdnsd &
    sleep 1
fi

echo "[entrypoint] Starting nmos-cpp-node on port 9550..."
/home/nmos-cpp-node /home/node.json &

echo "[entrypoint] Waiting for NMOS node..."
for i in $(seq 1 30); do
    if curl -sf "http://localhost:9550/x-nmos/node/v1.3/" > /dev/null 2>&1; then
        echo "[entrypoint] NMOS node ready."
        break
    fi
    sleep 1
done

echo "[entrypoint] Starting frontend static server on port 9750..."
python3 -m http.server 9750 --directory /app/frontend/dist &

echo "[entrypoint] Starting FastAPI backend on port 9650..."
cd /app
exec python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 9650
