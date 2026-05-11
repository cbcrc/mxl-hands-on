#!/bin/bash
# SPDX-FileCopyrightText: 2025 Contributors to the Media eXchange Layer project.
# SPDX-License-Identifier: Apache-2.0
#
# Entrypoint for the MXL Test Generator container.
# Starts:
#   1. mDNS responder (mdnsd) – needed for NMOS DNS-SD advertisement
#   2. nmos-cpp-node           – NMOS IS-04 / IS-05 node on port 9510
#   3. Static file server      – React frontend on port 9710
#   4. FastAPI backend          – Test Generator API on port 9610 (foreground)

set -e

# 1. Start mDNS responder if present
if command -v mdnsd &>/dev/null; then
    echo "[entrypoint] Starting mdnsd..."
    mdnsd &
    sleep 1
fi

# 2. Start NMOS node
echo "[entrypoint] Starting nmos-cpp-node on port 9510..."
/home/nmos-cpp-node /home/node.json &

# Wait until the NMOS node answers
echo "[entrypoint] Waiting for NMOS node..."
for i in $(seq 1 30); do
    if curl -sf "http://localhost:9510/x-nmos/node/v1.3/" > /dev/null 2>&1; then
        echo "[entrypoint] NMOS node ready."
        break
    fi
    sleep 1
done

# 3. Serve the built React frontend on port 9710
echo "[entrypoint] Starting frontend static server on port 9710..."
python3 -m http.server 9710 --directory /app/frontend/dist &

# 4. Start FastAPI backend (foreground – keeps the container alive)
echo "[entrypoint] Starting FastAPI backend on port 9610..."
cd /app
exec python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 9610
