# SPDX-FileCopyrightText: 2025 Contributors to the Media eXchange Layer project.
# SPDX-License-Identifier: Apache-2.0
#
# Multi-stage Dockerfile for the MXL HLS Gateway.
# Build context: repository root.
#
# Stage 1 – Build React + Vite frontend
# Stage 2 – Runtime (Ubuntu 24.04 + GStreamer + MXL libraries)

# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 – Build React + Vite frontend
# ─────────────────────────────────────────────────────────────────────────────
FROM node:18-bullseye-slim AS frontend-builder

WORKDIR /build

COPY gst-apps/hls2mxl/frontend/package.json \
     gst-apps/hls2mxl/frontend/package-lock.json* \
     ./

RUN npm install --legacy-peer-deps

COPY gst-apps/hls2mxl/frontend/ ./

RUN npm run build

# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 – Runtime on Ubuntu 24.04 (GLIBC 2.39)
# ─────────────────────────────────────────────────────────────────────────────
FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-gi \
        python3-gi-cairo \
        gir1.2-gstreamer-1.0 \
        gir1.2-gst-plugins-base-1.0 \
        gstreamer1.0-tools \
        gstreamer1.0-plugins-base \
        gstreamer1.0-plugins-good \
        gstreamer1.0-plugins-bad \
        gstreamer1.0-x \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY gst-apps/hls2mxl/backend/requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt

# ── MXL libraries ────────────────────────────────────────────────────────────
COPY dmf-mxl/rust/target/release/libgstmxl.so /usr/lib/x86_64-linux-gnu/gstreamer-1.0/

COPY dmf-mxl/build/Linux-Clang-Release/lib/libmxl.so.1.2 /opt/mxl/lib/libmxl.so.1.2

RUN cd /opt/mxl/lib \
 && ln -sf libmxl.so.1.2        libmxl.so.1 \
 && ln -sf libmxl.so.1          libmxl.so \
 && ldconfig /opt/mxl/lib /usr/lib/x86_64-linux-gnu/gstreamer-1.0 \
 # libgstmxl.so dlopen()s libmxl.so from its compile-time build path; satisfy it
 && mkdir -p /workspace/mxl/build/Linux-Clang-Release/lib \
 && ln -sf /opt/mxl/lib/libmxl.so /workspace/mxl/build/Linux-Clang-Release/lib/libmxl.so

ENV LD_LIBRARY_PATH=/opt/mxl/lib

# ── Application code ─────────────────────────────────────────────────────────
COPY gst-apps/hls2mxl/backend/ /app/backend/
COPY --from=frontend-builder /build/dist /app/frontend/dist

COPY gst-apps/hls2mxl/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# ── Environment ──────────────────────────────────────────────────────────────
ENV MXL_DOMAIN=/mxl-domain

EXPOSE 9600

ENTRYPOINT ["/entrypoint.sh"]
