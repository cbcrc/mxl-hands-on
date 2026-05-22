# SPDX-FileCopyrightText: 2025 Contributors to the Media eXchange Layer project.
# SPDX-License-Identifier: Apache-2.0
#
# Multi-stage Dockerfile for the MXL HTML5 Keyer.
#
# Build context: repository root  (../../  relative to this file's directory)
#
# Stage 1  – build the gstcefsrc GStreamer plugin (ubuntu:24.04)
#             cmake's DownloadCEF.cmake fetches the matching CEF from Spotify CDN.
#             cmake --install puts libgstcef.so, gstcefsubprocess, libcef.so,
#             CEF resources/locales all under /opt/gst-cef/.
# Stage 2  – build the React/Vite frontend
# Stage 3  – extract nmos-cpp-node + mdnsd + libdns_sd from mxl-nmos-cpp:latest
# Stage 4  – runtime image based on ubuntu:24.04

# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 – Build gstcefsrc (ubuntu:24.04)
# ─────────────────────────────────────────────────────────────────────────────
FROM ubuntu:24.04 AS cef-builder

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        pkg-config \
        python3 \
        curl \
        ca-certificates \
        libgstreamer1.0-dev \
        libgstreamer-plugins-base1.0-dev \
        libglib2.0-dev \
        libasound2-dev \
        libdbus-1-dev \
        libcups2-dev \
        libnss3 \
        libatk1.0-dev \
        libatk-bridge2.0-dev \
        libdrm-dev \
        libxkbcommon-dev \
        libxcomposite-dev \
        libxdamage-dev \
        libxrandr-dev \
        libgbm-dev \
        libpango1.0-dev \
        libcairo2-dev \
    && rm -rf /var/lib/apt/lists/*

# Download gstcefsrc source (curl -L follows GitHub's redirect to codeload).
RUN mkdir -p /build/gstcefsrc && \
    curl -sL "https://github.com/centricular/gstcefsrc/archive/refs/heads/master.tar.gz" \
         -o /tmp/gstcefsrc.tar.gz && \
    tar -xzf /tmp/gstcefsrc.tar.gz --strip-components=1 -C /build/gstcefsrc && \
    rm /tmp/gstcefsrc.tar.gz

WORKDIR /build/gstcefsrc

# CMake's DownloadCEF.cmake automatically fetches the correct CEF version
# (defined in CMakeLists.txt) from the Spotify CDN via file(DOWNLOAD ...).
# cmake --install puts libgstcef.so + gstcefsubprocess + libcef.so +
# CEF resources/locales all into /opt/gst-cef/ (DESTINATION . in CMakeLists).
RUN cmake -B _build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/opt/gst-cef \
    && cmake --build _build -j$(nproc) \
    && cmake --install _build

# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 – Build React + Vite frontend
# ─────────────────────────────────────────────────────────────────────────────
FROM node:18-bullseye-slim AS frontend-builder

WORKDIR /build

COPY docker/exercise-5/data/HTML5-keyer/frontend/package.json \
     docker/exercise-5/data/HTML5-keyer/frontend/package-lock.json* \
     ./

RUN npm install --legacy-peer-deps

COPY docker/exercise-5/data/HTML5-keyer/frontend/ ./

RUN npm run build

# ─────────────────────────────────────────────────────────────────────────────
# Stage 3 – Extract NMOS binaries from the existing image
# ─────────────────────────────────────────────────────────────────────────────
FROM mxl-nmos-cpp:latest AS nmos-binaries

# ─────────────────────────────────────────────────────────────────────────────
# Stage 4 – Runtime on Ubuntu 24.04 (GLIBC 2.39)
# ─────────────────────────────────────────────────────────────────────────────
FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive

# Copy nmos-cpp-node and mDNS support from the NMOS image
COPY --from=nmos-binaries /home/nmos-cpp-node    /home/nmos-cpp-node
COPY --from=nmos-binaries /usr/sbin/mdnsd         /usr/sbin/mdnsd
COPY --from=nmos-binaries /usr/lib/libdns_sd.so.1 /usr/lib/libdns_sd.so.1
RUN ln -sf /usr/lib/libdns_sd.so.1 /usr/lib/libdns_sd.so

# Install Python 3, GStreamer 1.0 with all codec plugins, OpenGL/EGL support,
# GI bindings, and Xvfb for headless GL rendering (required by glvideomixer + CEF)
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
        gstreamer1.0-x \
        gstreamer1.0-gl \
        gstreamer1.0-libav \
        libgstreamer-gl1.0-0 \
        libgl1 \
        libgl1-mesa-dri \
        libegl1 \
        libegl-mesa0 \
        libgles2 \
        xvfb \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY docker/exercise-5/data/HTML5-keyer/backend/requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt

# ── gstcefsrc plugin and CEF runtime ─────────────────────────────────────────
# cmake --install put everything (libgstcef.so, gstcefsubprocess, libcef.so,
# resources, locales) into /opt/gst-cef/ with DESTINATION . in CMakeLists.
# Keep them together so gstcefsubprocess is co-located with libgstcef.so
# (required for CEF's multi-process model to find the subprocess binary).
COPY --from=cef-builder /opt/gst-cef/ /opt/gst-cef/

RUN ldconfig /opt/gst-cef

# ── MXL libraries ────────────────────────────────────────────────────────────
COPY dmf-mxl/rust/target/release/libgstmxl.so /usr/lib/x86_64-linux-gnu/gstreamer-1.0/

COPY dmf-mxl/build/Linux-Clang-Release/lib/libmxl.so.1.1                    /opt/mxl/lib/libmxl.so.1.1
COPY dmf-mxl/build/Linux-Clang-Release/lib/internal/libmxl-common.so.1.1   /opt/mxl/lib/libmxl-common.so.1.1

RUN cd /opt/mxl/lib \
 && ln -sf libmxl.so.1.1        libmxl.so.1 \
 && ln -sf libmxl.so.1          libmxl.so \
 && ln -sf libmxl-common.so.1.1 libmxl-common.so.1 \
 && ln -sf libmxl-common.so.1   libmxl-common.so \
 && ldconfig /opt/mxl/lib /usr/lib/x86_64-linux-gnu/gstreamer-1.0 \
 && mkdir -p /workspace/mxl/build/Linux-Clang-Release/lib \
 && ln -sf /opt/mxl/lib/libmxl.so /workspace/mxl/build/Linux-Clang-Release/lib/libmxl.so

# ── Environment ──────────────────────────────────────────────────────────────
# GST_PLUGIN_PATH: /opt/gst-cef contains libgstcef.so alongside the CEF runtime
#                  so gstcefsubprocess is always co-located with the plugin.
ENV LD_LIBRARY_PATH=/opt/mxl/lib:/opt/gst-cef \
    GST_PLUGIN_PATH=/opt/gst-cef:/usr/lib/x86_64-linux-gnu/gstreamer-1.0

# ── Application code ─────────────────────────────────────────────────────────
COPY docker/exercise-5/data/HTML5-keyer/backend/ /app/backend/
COPY --from=frontend-builder /build/dist          /app/frontend/dist

COPY docker/exercise-5/data/HTML5-keyer/node.json /home/node.json

COPY docker/exercise-5/data/HTML5-keyer/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV MXL_DOMAIN=/mxl-domain \
    NMOS_NODE_URL=http://localhost:9540

EXPOSE 9540 9640 9740

ENTRYPOINT ["/entrypoint.sh"]
