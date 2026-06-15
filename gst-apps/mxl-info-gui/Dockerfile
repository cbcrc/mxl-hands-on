# SPDX-FileCopyrightText: 2025 Contributors to the Media eXchange Layer project.
# SPDX-License-Identifier: Apache-2.0
#
# Multi-stage Dockerfile for the MXL Info GUI.
#
# Build context: repository root  (../../  relative to this file's directory)
#
# Stage 1  – build the React/Vite frontend
# Stage 2  – runtime image based on ubuntu:24.04

# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 – Build React + Vite frontend
# ─────────────────────────────────────────────────────────────────────────────
FROM node:18-bullseye-slim AS frontend-builder

WORKDIR /build

COPY gst-apps/mxl-info-gui/frontend/package.json \
     gst-apps/mxl-info-gui/frontend/package-lock.json* \
     ./

RUN npm install --legacy-peer-deps

COPY gst-apps/mxl-info-gui/frontend/ ./

# Copy CBC Radio-Canada logo into the public directory so Vite includes it in the build
COPY gst-apps/logo/rgb_cbc-radio-canada-col-coul.png ./public/cbc-logo.png

RUN npm run build

# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 – Runtime on Ubuntu 24.04
# ─────────────────────────────────────────────────────────────────────────────
FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive

# Install Python 3 and curl
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY gst-apps/mxl-info-gui/backend/requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt

# ── MXL libraries ────────────────────────────────────────────────────────────
COPY dmf-mxl/build/Linux-Clang-Release/lib/libmxl.so.1.2          /opt/mxl/lib/libmxl.so.1.2

RUN cd /opt/mxl/lib \
 && ln -sf libmxl.so.1.2        libmxl.so.1 \
 && ln -sf libmxl.so.1          libmxl.so \
 && ldconfig /opt/mxl/lib

# ── mxl-info CLI tool ────────────────────────────────────────────────────────
COPY dmf-mxl/build/Linux-Clang-Release/tools/mxl-info/mxl-info /opt/mxl/tools/mxl-info/mxl-info
RUN chmod +x /opt/mxl/tools/mxl-info/mxl-info

ENV LD_LIBRARY_PATH=/opt/mxl/lib

# ── Application code ─────────────────────────────────────────────────────────
COPY gst-apps/mxl-info-gui/backend/ /app/backend/
COPY --from=frontend-builder /build/dist           /app/frontend/dist

# Entrypoint
COPY gst-apps/mxl-info-gui/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# ── Environment ──────────────────────────────────────────────────────────────
ENV MXL_DOMAIN=/mxl-domain

EXPOSE 9600

ENTRYPOINT ["/entrypoint.sh"]
