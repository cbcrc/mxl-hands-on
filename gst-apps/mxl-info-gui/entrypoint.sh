#!/bin/bash
# SPDX-FileCopyrightText: 2025 Contributors to the Media eXchange Layer project.
# SPDX-License-Identifier: Apache-2.0
#
# Entrypoint for the MXL Info GUI container.
# FastAPI serves both the REST API and the built React frontend on port 9600.

set -e

cd /app
exec python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 9600
