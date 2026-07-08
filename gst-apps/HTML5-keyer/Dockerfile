# SPDX-FileCopyrightText: 2025 CBC/Radio-Canada
# SPDX-License-Identifier: Apache-2.0
#
# Multi-stage Dockerfile for the MXL HTML5 Keyer.
# Build context: repository root (two levels above this file's directory).
#
# Stage 1 — Build React + Vite frontend
# Stage 2 — Build gst-plugin-cef (gstcefsrc) on Ubuntu 24.04 (matches runtime ABI)
# Stage 3 — Runtime on Ubuntu 24.04 (GStreamer + MXL + CEF + Xvfb)

# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — Build React + Vite frontend
# ─────────────────────────────────────────────────────────────────────────────
FROM node:18-bullseye-slim AS frontend-builder

WORKDIR /build

COPY gst-apps/HTML5-keyer/frontend/package.json \
     gst-apps/HTML5-keyer/frontend/package-lock.json* \
     ./

RUN npm install --legacy-peer-deps

COPY gst-apps/HTML5-keyer/frontend/ ./

COPY gst-apps/logo/rgb_cbc-radio-canada-col-coul.png ./public/cbc-logo.png

RUN npm run build

# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — Build gst-plugin-cef (gstcefsrc) on Ubuntu 24.04
# ─────────────────────────────────────────────────────────────────────────────
# Built on the same Ubuntu 24.04 base as the runtime stage so the resulting
# .so links against compatible GStreamer / glibc / libstdc++ versions.
#
# CMake's DownloadCEF.cmake step pulls the CEF binary distribution (~600 MB)
# from cef-builds.spotifycdn.com — expect this stage to take several minutes
# the first time and roughly 1 GB of build cache.
FROM ubuntu:24.04 AS cef-builder

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake pkg-config git ca-certificates curl \
        python3 \
        libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev \
        libglib2.0-dev \
        libx11-dev libxext-dev libxi-dev libxtst-dev libxrandr-dev \
        libnss3-dev libdbus-1-dev libgtk-3-dev libdrm-dev \
        libxcomposite-dev libxdamage-dev libxfixes-dev \
        libasound2-dev libpulse-dev libatk1.0-dev libatk-bridge2.0-dev \
        libcups2-dev libxkbcommon-dev libpango1.0-dev libcairo2-dev \
        libnspr4-dev libgbm-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN git clone --depth 1 https://github.com/centricular/gstcefsrc.git

WORKDIR /build/gstcefsrc
RUN mkdir build \
 && cd build \
 && cmake -G "Unix Makefiles" -DCMAKE_BUILD_TYPE=Release .. \
 && make -j"$(nproc)"

# Collect the runtime tree.  The build drops the GStreamer plugins, the CEF
# subprocess helper, and the CEF binary distribution into build/Release/.
# We additionally copy the CEF Resources/ tree (locale .pak files and ICU
# data) from third_party/cef.
RUN set -eux \
 && mkdir -p /opt/cef \
 && cp -a /build/gstcefsrc/build/Release/. /opt/cef/ \
 # CEF extracts to <source>/third_party/cef (see gstcefsrc cmake/DownloadCEF.cmake);
 # find keeps this robust if upstream master moves it again
 && CEF_DIR="$(find /build/gstcefsrc -maxdepth 4 -type d -name 'cef_binary_*' -print -quit)" \
 && test -n "$CEF_DIR" \
 && if [ -d "$CEF_DIR/Resources" ]; then cp -a "$CEF_DIR/Resources/." /opt/cef/ ; fi \
 && if [ -d "$CEF_DIR/Release" ];  then cp -a "$CEF_DIR/Release/."  /opt/cef/ ; fi \
 # keep CEF's license/notice files in the shipped image (BSD-3-Clause attribution)
 && cp "$CEF_DIR/LICENSE.txt" /opt/cef/ \
 && if [ -f "$CEF_DIR/README.txt" ]; then cp "$CEF_DIR/README.txt" /opt/cef/ ; fi \
 && ls -la /opt/cef | head -40

# ─────────────────────────────────────────────────────────────────────────────
# Stage 2b — Download Vosk speech models (offline STT for prompt-mode voice tracking)
# ─────────────────────────────────────────────────────────────────────────────
FROM ubuntu:24.04 AS vosk-models

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates unzip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /dl
RUN curl -L --fail -o en.zip https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip \
 && unzip -q en.zip && mkdir -p /opt/vosk && mv vosk-model-small-en-us-0.15 /opt/vosk/en && rm en.zip
RUN curl -L --fail -o fr.zip https://alphacephei.com/vosk/models/vosk-model-small-fr-0.22.zip \
 && unzip -q fr.zip && mv vosk-model-small-fr-0.22 /opt/vosk/fr && rm fr.zip

# ─────────────────────────────────────────────────────────────────────────────
# Stage 3 — Runtime on Ubuntu 24.04
# ─────────────────────────────────────────────────────────────────────────────
FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive

# Core GStreamer + Python + Xvfb + Chromium native deps.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip \
        python3-gi python3-gi-cairo \
        gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 \
        gstreamer1.0-tools \
        gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
        gstreamer1.0-plugins-bad \
        xvfb \
        libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
        libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 libgbm1 \
        libpango-1.0-0 libcairo2 libasound2t64 libxshmfence1 \
        libgtk-3-0t64 libxtst6 libxss1 libnspr4 libexpat1 \
        fonts-liberation xdg-utils \
    && rm -rf /var/lib/apt/lists/*

COPY gst-apps/HTML5-keyer/backend/requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt

# ── MXL libraries ─────────────────────────────────────────────────────────────
COPY dmf-mxl/rust/target/release/libgstmxl.so /usr/lib/x86_64-linux-gnu/gstreamer-1.0/

COPY dmf-mxl/build/Linux-Clang-Release/lib/libmxl.so.1.2 /opt/mxl/lib/libmxl.so.1.2

RUN cd /opt/mxl/lib \
 && ln -sf libmxl.so.1.2 libmxl.so.1 \
 && ln -sf libmxl.so.1   libmxl.so \
 && ldconfig /opt/mxl/lib /usr/lib/x86_64-linux-gnu/gstreamer-1.0 \
 # libgstmxl.so dlopen()s libmxl.so from its compile-time build path; satisfy it
 && mkdir -p /workspace/mxl/build/Linux-Clang-Release/lib \
 && ln -sf /opt/mxl/lib/libmxl.so /workspace/mxl/build/Linux-Clang-Release/lib/libmxl.so

# ── mxl-info CLI tool ─────────────────────────────────────────────────────────
COPY dmf-mxl/build/Linux-Clang-Release/tools/mxl-info/mxl-info /opt/mxl/tools/mxl-info/mxl-info
RUN chmod +x /opt/mxl/tools/mxl-info/mxl-info

# ── CEF runtime + gstcefsrc plugin ────────────────────────────────────────────
# Keep the plugin co-located with its CEF siblings (gstcefsubprocess, libcef.so,
# *.pak, locales/, swiftshader/, etc.).  Do NOT symlink libgstcef.so into the
# system GStreamer plugin dir: CEF derives the gstcefsubprocess path from the
# directory of the loaded plugin, so loading via the symlink makes CEF look for
# the subprocess in /usr/lib/x86_64-linux-gnu/gstreamer-1.0/ where it does not
# exist.  Instead, point GStreamer at /opt/cef via GST_PLUGIN_PATH so the
# plugin loads with its siblings adjacent.
COPY --from=cef-builder /opt/cef /opt/cef
RUN ldconfig /opt/cef

# ── Environment ───────────────────────────────────────────────────────────────
# LD_LIBRARY_PATH lets the CEF subprocess find libcef.so and friends, and lets
# libgstmxl.so find libmxl.so.
ENV LD_LIBRARY_PATH=/opt/mxl/lib:/opt/cef
ENV MXL_DOMAIN=/mxl-domain
# Load gst-plugin-cef from /opt/cef (where its CEF siblings live) in addition
# to the default Ubuntu plugin path.
ENV GST_PLUGIN_PATH=/opt/cef
# Disable CEF's chrome-sandbox (requires setuid root, unavailable in Docker)
# and disable GPU so Chromium uses its software rasterizer.
ENV GST_CEF_CHROME_EXTRA_FLAGS="no-sandbox,disable-component-update,disable-gpu,disable-gpu-compositing"

# ── Vosk speech models (prompt-mode voice tracking) ───────────────────────────
COPY --from=vosk-models /opt/vosk /opt/vosk

# ── Application code ──────────────────────────────────────────────────────────
COPY gst-apps/HTML5-keyer/backend/ /app/backend/
COPY --from=frontend-builder /build/dist /app/frontend/dist
# OGraf teleprompter host page + graphic, served by FastAPI and loaded by CEF.
COPY gst-apps/HTML5-keyer/prompter/ /app/prompter/

COPY gst-apps/HTML5-keyer/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 9600

ENTRYPOINT ["/entrypoint.sh"]
