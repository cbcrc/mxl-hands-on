# SPDX-FileCopyrightText: 2025 CBC/Radio-Canada
# SPDX-License-Identifier: Apache-2.0
#
# Multi-stage Dockerfile for the MXL-to-WebRTC Gateway.
# Build context: repository root (two levels above this file's directory).
#
# Stage 1 – Build React + Vite frontend
# Stage 2 – Runtime on Ubuntu 24.04 (GStreamer + MXL libraries)
#
# Uses webrtcbin (gstreamer1.0-plugins-bad) + Python WHIP handshake instead of
# whipsink, eliminating the heavy Cargo/gst-plugins-rs build stage.

# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 – Build React + Vite frontend
# ─────────────────────────────────────────────────────────────────────────────
FROM node:18-bullseye-slim AS frontend-builder

WORKDIR /build

COPY gst-apps/mxl2webrtc/frontend/package.json \
     gst-apps/mxl2webrtc/frontend/package-lock.json* \
     ./

RUN npm install --legacy-peer-deps

COPY gst-apps/mxl2webrtc/frontend/ ./

# Copy CBC Radio-Canada logo into the public directory so Vite includes it in the build
COPY gst-apps/logo/rgb_cbc-radio-canada-col-coul.png ./public/cbc-logo.png

RUN npm run build

# ─────────────────────────────────────────────────────────────────────────────
# Stage 1b – Build gst-plugin-closedcaption (rsclosedcaption) from gst-plugins-rs
# Provides st2038anctocc / cea608tott used to decode CEA-608 captions out of the
# ST 2038 ANC data flow. Tag 0.14.5 is the first series shipping the ST 2038
# elements and targets the GStreamer 1.24 ABI from Ubuntu 24.04 apt.
# ─────────────────────────────────────────────────────────────────────────────
FROM ubuntu:24.04 AS rscc-builder

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates curl build-essential pkg-config \
        libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev \
        libpango1.0-dev libcairo2-dev \
    && rm -rf /var/lib/apt/lists/*

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
ENV PATH="/root/.cargo/bin:${PATH}"

RUN git clone --depth 1 --branch 0.14.5 \
      https://gitlab.freedesktop.org/gstreamer/gst-plugins-rs.git /tmp/gst-plugins-rs \
 && cd /tmp/gst-plugins-rs \
 && cargo build --release -p gst-plugin-closedcaption \
 && cp target/release/libgstrsclosedcaption.so /tmp/libgstrsclosedcaption.so

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
        gir1.2-gst-plugins-bad-1.0 \
        gstreamer1.0-tools \
        gstreamer1.0-plugins-base \
        gstreamer1.0-plugins-good \
        gstreamer1.0-plugins-bad \
        gstreamer1.0-nice \
        gstreamer1.0-x \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY gst-apps/mxl2webrtc/backend/requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt

# ── Closed-caption / ST 2038 GStreamer plugin (built in stage 1b) ─────────────
COPY --from=rscc-builder /tmp/libgstrsclosedcaption.so /usr/lib/x86_64-linux-gnu/gstreamer-1.0/

# ── MXL libraries ─────────────────────────────────────────────────────────────
COPY dmf-mxl/rust/target/release/libgstmxl.so /usr/lib/x86_64-linux-gnu/gstreamer-1.0/

COPY dmf-mxl/build/Linux-Clang-Release/lib/libmxl.so.1.2 /opt/mxl/lib/libmxl.so.1.2

RUN cd /opt/mxl/lib \
 && ln -sf libmxl.so.1.2        libmxl.so.1 \
 && ln -sf libmxl.so.1          libmxl.so \
 && ldconfig /opt/mxl/lib /usr/lib/x86_64-linux-gnu/gstreamer-1.0 \
 # libgstmxl.so dlopen()s libmxl.so from its compile-time build path; satisfy it
 && mkdir -p /workspace/mxl/build/Linux-Clang-Release/lib \
 && ln -sf /opt/mxl/lib/libmxl.so /workspace/mxl/build/Linux-Clang-Release/lib/libmxl.so

# ── mxl-info CLI tool ─────────────────────────────────────────────────────────
COPY dmf-mxl/build/Linux-Clang-Release/tools/mxl-info/mxl-info /opt/mxl/tools/mxl-info/mxl-info
RUN chmod +x /opt/mxl/tools/mxl-info/mxl-info

# ── Application code ──────────────────────────────────────────────────────────
COPY gst-apps/mxl2webrtc/backend/ /app/backend/
COPY --from=frontend-builder /build/dist /app/frontend/dist

COPY gst-apps/mxl2webrtc/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# ── Environment ───────────────────────────────────────────────────────────────
ENV LD_LIBRARY_PATH=/opt/mxl/lib
ENV MXL_DOMAIN=/mxl-domain
ENV MEDIAMTX_WHIP_URL=http://host.docker.internal:8889/mxl2webrtc/whip
ENV MEDIAMTX_WEBRTC_URL=http://localhost:8889
# Direct-mode WebRTC ICE: pin webrtcbin's ICE UDP ports to a published range (one per
# viewer); the host candidate address is rewritten per request to the host the browser
# connected to, so the same bridged config serves local and remote viewers.
ENV WEBRTC_ICE_PORT_MIN=""
ENV WEBRTC_ICE_PORT_MAX=""

EXPOSE 9600
# Direct-mode ICE UDP range (must match WEBRTC_ICE_PORT_MIN/MAX when bridged;
# 8189 is reserved by the host-networked mediamtx service)
EXPOSE 8200-8210/udp

ENTRYPOINT ["/entrypoint.sh"]
