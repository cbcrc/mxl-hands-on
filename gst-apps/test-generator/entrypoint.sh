#!/bin/bash
# SPDX-FileCopyrightText: 2025 Contributors to the Media eXchange Layer project.
# SPDX-License-Identifier: Apache-2.0
set -e

echo "[entrypoint] Starting frontend static server on port 9700..."
python3 -m http.server 9700 --directory /app/frontend/dist &

echo "[entrypoint] Starting FastAPI backend on port 9600..."
cd /app
exec python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 9600
