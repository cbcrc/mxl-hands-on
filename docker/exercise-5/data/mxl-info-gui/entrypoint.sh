#!/bin/bash
# SPDX-FileCopyrightText: 2025 Contributors to the Media eXchange Layer project.
# SPDX-License-Identifier: Apache-2.0
#
# Entrypoint for the MXL Info GUI container.
# Starts:
#   1. Static file server  – React frontend on port 9760
#   2. FastAPI backend      – MXL Info API on port 9660 (foreground)

set -e

# 1. Serve the built React frontend on port 9760
echo "[entrypoint] Starting frontend static server on port 9760..."
python3 -m http.server 9760 --directory /app/frontend/dist &

# 2. Start FastAPI backend (foreground – keeps the container alive)
echo "[entrypoint] Starting FastAPI backend on port 9660..."
cd /app
exec python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 9660
