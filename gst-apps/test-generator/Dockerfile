# SPDX-FileCopyrightText: 2025 Contributors to the Media eXchange Layer project.
# SPDX-License-Identifier: Apache-2.0
#
# Multi-stage Dockerfile for the MXL Test Generator.
# Build context: repository root.
#
# Stage 1 – Build React + Vite frontend
# Stage 2 – Runtime (Ubuntu 24.04 + GStreamer + MXL libraries)

# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 – Build React + Vite frontend
# ─────────────────────────────────────────────────────────────────────────────
FROM node:18-bullseye-slim AS frontend-builder

WORKDIR /build

COPY gst-apps/test-generator/frontend/package.json \
     gst-apps/test-generator/frontend/package-lock.json* \
     ./

RUN npm install --legacy-peer-deps

COPY gst-apps/test-generator/frontend/ ./

RUN npm run build

# ─────────────────────────────────────────────────────────────────────────────
# Stage 1b – Build gst-plugin-closedcaption (rsclosedcaption) from gst-plugins-rs
# Provides tttocea608 / cctost2038anc used for the CEA-608 ↔ ST 2038 ANC caption
# path. Tag 0.14.5 is the first series shipping the ST 2038 elements and targets
# the GStreamer 1.24 ABI from Ubuntu 24.04 apt.
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
        gstreamer1.0-tools \
        gstreamer1.0-plugins-base \
        gstreamer1.0-plugins-good \
        gstreamer1.0-plugins-bad \
        gstreamer1.0-plugins-ugly \
        gstreamer1.0-libav \
        gstreamer1.0-x \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY gst-apps/test-generator/backend/requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt

# ── Closed-caption / ST 2038 GStreamer plugin (built in stage 1b) ─────────────
COPY --from=rscc-builder /tmp/libgstrsclosedcaption.so /usr/lib/x86_64-linux-gnu/gstreamer-1.0/

# ── MXL libraries ────────────────────────────────────────────────────────────
COPY dmf-mxl/rust/target/release/libgstmxl.so /usr/lib/x86_64-linux-gnu/gstreamer-1.0/

COPY dmf-mxl/build/Linux-Clang-Release/lib/libmxl.so.1.2                    /opt/mxl/lib/libmxl.so.1.2

RUN cd /opt/mxl/lib \
 && ln -sf libmxl.so.1.2        libmxl.so.1 \
 && ln -sf libmxl.so.1          libmxl.so \
 && ldconfig /opt/mxl/lib /usr/lib/x86_64-linux-gnu/gstreamer-1.0 \
 # libgstmxl.so dlopen()s libmxl.so from its compile-time build path; satisfy it
 && mkdir -p /workspace/mxl/build/Linux-Clang-Release/lib \
 && ln -sf /opt/mxl/lib/libmxl.so /workspace/mxl/build/Linux-Clang-Release/lib/libmxl.so

ENV LD_LIBRARY_PATH=/opt/mxl/lib

# ── Application code ─────────────────────────────────────────────────────────
COPY gst-apps/test-generator/backend/ /app/backend/
COPY --from=frontend-builder /build/dist /app/frontend/dist

COPY gst-apps/test-generator/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# ── Environment ──────────────────────────────────────────────────────────────
ENV MXL_DOMAIN=/mxl-domain

EXPOSE 9600

ENTRYPOINT ["/entrypoint.sh"]
