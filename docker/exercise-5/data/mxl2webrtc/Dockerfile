# SPDX-FileCopyrightText: 2025 Contributors to the Media eXchange Layer project.
# SPDX-License-Identifier: Apache-2.0
#
# Multi-stage Dockerfile for the MXL-to-WebRTC Gateway.
# Build context: repository root (../../ relative to this file's directory)
#
# Stage 1 – Build libgstrswebrtc.so from gst-plugins-rs
# Stage 2 – Build React + Vite frontend
# Stage 3 – Extract NMOS binaries
# Stage 4 – Runtime on Ubuntu 24.04

# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 – Build gst-plugin-webrtc (webrtcsink) from gst-plugins-rs
# ─────────────────────────────────────────────────────────────────────────────
FROM ubuntu:24.04 AS webrtc-builder

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        pkg-config \
        libssl-dev \
        git \
        curl \
        ca-certificates \
        libgstreamer1.0-dev \
        libgstreamer-plugins-base1.0-dev \
        libgstreamer-plugins-bad1.0-dev \
        libglib2.0-dev \
    && rm -rf /var/lib/apt/lists/*

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable --no-modify-path
ENV PATH="/root/.cargo/bin:${PATH}"

RUN git clone https://gitlab.freedesktop.org/gstreamer/gst-plugins-rs.git --depth 1 /build/gst-plugins-rs

RUN cd /build/gst-plugins-rs && cargo build --package gst-plugin-webrtc --release

RUN cp /build/gst-plugins-rs/target/release/libgstrswebrtc.so /tmp/libgstrswebrtc.so

# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 – Build React + Vite frontend
# ─────────────────────────────────────────────────────────────────────────────
FROM node:18-bullseye-slim AS frontend-builder

WORKDIR /build

COPY docker/exercise-5/data/mxl2webrtc/frontend/package.json \
     docker/exercise-5/data/mxl2webrtc/frontend/package-lock.json* \
     ./

RUN npm install --legacy-peer-deps

COPY docker/exercise-5/data/mxl2webrtc/frontend/ ./

RUN npm run build

# ─────────────────────────────────────────────────────────────────────────────
# Stage 3 – Extract NMOS binaries
# ─────────────────────────────────────────────────────────────────────────────
FROM mxl-nmos-cpp:latest AS nmos-binaries

# ─────────────────────────────────────────────────────────────────────────────
# Stage 4 – Runtime on Ubuntu 24.04
# ─────────────────────────────────────────────────────────────────────────────
FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive

COPY --from=nmos-binaries /home/nmos-cpp-node    /home/nmos-cpp-node
COPY --from=nmos-binaries /usr/sbin/mdnsd         /usr/sbin/mdnsd
COPY --from=nmos-binaries /usr/lib/libdns_sd.so.1 /usr/lib/libdns_sd.so.1
RUN ln -sf /usr/lib/libdns_sd.so.1 /usr/lib/libdns_sd.so

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
        gstreamer1.0-plugins-ugly \
        gstreamer1.0-nice \
        gstreamer1.0-x \
        gstreamer1.0-libav \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY docker/exercise-5/data/mxl2webrtc/backend/requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt

# ── WebRTC plugin ─────────────────────────────────────────────────────────────
COPY --from=webrtc-builder /tmp/libgstrswebrtc.so /usr/lib/x86_64-linux-gnu/gstreamer-1.0/

# ── MXL libraries ─────────────────────────────────────────────────────────────
COPY dmf-mxl/rust/target/release/libgstmxl.so /usr/lib/x86_64-linux-gnu/gstreamer-1.0/

COPY dmf-mxl/build/Linux-Clang-Release/lib/libmxl.so.1.1                  /opt/mxl/lib/libmxl.so.1.1
COPY dmf-mxl/build/Linux-Clang-Release/lib/internal/libmxl-common.so.1.1  /opt/mxl/lib/libmxl-common.so.1.1

RUN cd /opt/mxl/lib \
 && ln -sf libmxl.so.1.1        libmxl.so.1 \
 && ln -sf libmxl.so.1          libmxl.so \
 && ln -sf libmxl-common.so.1.1 libmxl-common.so.1 \
 && ln -sf libmxl-common.so.1   libmxl-common.so \
 && ldconfig /opt/mxl/lib /usr/lib/x86_64-linux-gnu/gstreamer-1.0 \
 && mkdir -p /workspace/mxl/build/Linux-Clang-Release/lib \
 && ln -sf /opt/mxl/lib/libmxl.so /workspace/mxl/build/Linux-Clang-Release/lib/libmxl.so

ENV LD_LIBRARY_PATH=/opt/mxl/lib

# ── Application code ──────────────────────────────────────────────────────────
COPY docker/exercise-5/data/mxl2webrtc/backend/ /app/backend/
COPY --from=frontend-builder /build/dist         /app/frontend/dist

COPY docker/exercise-5/data/mxl2webrtc/node.json    /home/node.json
COPY docker/exercise-5/data/mxl2webrtc/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV MXL_DOMAIN=/mxl-domain \
    NMOS_NODE_URL=http://localhost:9550

EXPOSE 9550 9650 9750 8443

ENTRYPOINT ["/entrypoint.sh"]
