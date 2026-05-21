# SPDX-FileCopyrightText: 2025 Contributors to the Media eXchange Layer project.
# SPDX-License-Identifier: Apache-2.0
#
# Multi-stage Dockerfile for the MXL File Player.
#
# Build context: repository root  (../../  relative to this file's directory)
#
# Stage 1  – build the React/Vite frontend
# Stage 2  – extract nmos-cpp-node + mdnsd + libdns_sd from mxl-nmos-cpp:latest
# Stage 3  – runtime image based on ubuntu:24.04 (GLIBC 2.39)
#              + nmos-cpp-node binary (copied from stage 2)
#              + Python 3 + GStreamer 1.0 (with Python GI bindings)
#              + MXL GStreamer plugin (libgstmxl.so)
#              + MXL shared libraries (libmxl.so, libmxl-common.so)

# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 – Build React + Vite frontend
# ─────────────────────────────────────────────────────────────────────────────
FROM node:18-bullseye-slim AS frontend-builder

WORKDIR /build

COPY docker/exercise-5/data/file-player/frontend/package.json \
     docker/exercise-5/data/file-player/frontend/package-lock.json* \
     ./

RUN npm install --legacy-peer-deps

COPY docker/exercise-5/data/file-player/frontend/ ./

RUN npm run build

# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 – Extract NMOS binaries from the existing image
# ─────────────────────────────────────────────────────────────────────────────
FROM ghcr.io/cbcrc/mxl-nmos-cpp:latest AS nmos-binaries

# ─────────────────────────────────────────────────────────────────────────────
# Stage 3 – Runtime on Ubuntu 24.04 (GLIBC 2.39, satisfies libgstmxl.so deps)
# ─────────────────────────────────────────────────────────────────────────────
FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive

# Copy nmos-cpp-node and mDNS support from the NMOS image
COPY --from=nmos-binaries /home/nmos-cpp-node  /home/nmos-cpp-node
COPY --from=nmos-binaries /usr/sbin/mdnsd       /usr/sbin/mdnsd
COPY --from=nmos-binaries /usr/lib/libdns_sd.so.1 /usr/lib/libdns_sd.so.1
RUN ln -sf /usr/lib/libdns_sd.so.1 /usr/lib/libdns_sd.so

# Install Python 3, GStreamer 1.0 with all codec plugins, and GI bindings
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
        curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies (--break-system-packages required on Python 3.12+)
COPY docker/exercise-5/data/file-player/backend/requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt

# ── MXL libraries ────────────────────────────────────────────────────────────
# Copy the MXL GStreamer plugin into the multiarch plugin directory (Ubuntu 24.04)
COPY dmf-mxl/rust/target/release/libgstmxl.so /usr/lib/x86_64-linux-gnu/gstreamer-1.0/

# Copy the MXL shared libraries (.so.1.1 are the real files; create symlinks)
COPY dmf-mxl/build/Linux-Clang-Release/lib/libmxl.so.1.1          /opt/mxl/lib/libmxl.so.1.1
# COPY dmf-mxl/build/Linux-Clang-Release/lib/internal/libmxl-common.so.1.1 \
                                                                    #  /opt/mxl/lib/libmxl-common.so.1.1
# Copy MXL executables
COPY dmf-mxl/install/bin /usr/local/bin

RUN cd /opt/mxl/lib \
 && ln -sf libmxl.so.1.1        libmxl.so.1 \
 && ln -sf libmxl.so.1          libmxl.so \
 && ln -sf libmxl-common.so.1.1 libmxl-common.so.1 \
 && ln -sf libmxl-common.so.1   libmxl-common.so \
 && ldconfig /opt/mxl/lib /usr/lib/x86_64-linux-gnu/gstreamer-1.0 \
 # libgstmxl.so dlopen()s libmxl.so from its compile-time build path; satisfy it
 && mkdir -p /workspace/mxl/build/Linux-Clang-Release/lib \
 && ln -sf /opt/mxl/lib/libmxl.so /workspace/mxl/build/Linux-Clang-Release/lib/libmxl.so

ENV LD_LIBRARY_PATH=/opt/mxl/lib
# Add /usr/local/bin to PATH so that mxl-* executables are found
ENV PATH=/usr/local/bin:$PATH
# ── Application code ─────────────────────────────────────────────────────────
COPY docker/exercise-5/data/file-player/backend/ /app/backend/
COPY --from=frontend-builder /build/dist          /app/frontend/dist

# NMOS node configuration for the file-player
COPY docker/exercise-5/data/file-player/node.json /home/node.json

# Entrypoint
COPY docker/exercise-5/data/file-player/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# ── Environment ──────────────────────────────────────────────────────────────
ENV MEDIA_DIR=/home/file \
    MXL_DOMAIN=/mxl-domain \
    NMOS_NODE_URL=http://localhost:9500

EXPOSE 9500 9600 9700

ENTRYPOINT ["/entrypoint.sh"]
