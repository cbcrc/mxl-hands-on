#!/bin/bash
# Entrypoint for the MXL Input Selector container.
# FastAPI serves both the REST API and the built React frontend on port 9600.

set -e

cd /app
exec python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 9600
