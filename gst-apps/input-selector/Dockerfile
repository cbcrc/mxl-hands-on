# SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
# SPDX-License-Identifier: Apache-2.0
# Multi-stage Dockerfile for the MXL Input Selector (native Rust backend).
# Build context: repository root (two levels above this file's directory).
#
# Stage 1 – Build React + Vite frontend
# Stage 2 – Build the Rust backend (axum grain-router) against the prebuilt MXL SDK
# Stage 3 – Runtime on Ubuntu 24.04 (just libmxl + mxl-info + the binary; no
#           GStreamer, no Python)

# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 – Build React + Vite frontend
# ─────────────────────────────────────────────────────────────────────────────
FROM node:18-bullseye-slim AS frontend-builder

WORKDIR /build

COPY gst-apps/input-selector/frontend/package.json \
     gst-apps/input-selector/frontend/package-lock.json* \
     ./

RUN npm install --legacy-peer-deps

COPY gst-apps/input-selector/frontend/ ./

# Copy CBC Radio-Canada logo into the public directory so Vite includes it in the build
COPY gst-apps/logo/rgb_cbc-radio-canada-col-coul.png ./public/cbc-logo.png

RUN npm run build

# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 – Build the Rust backend
#
# Uses the `mxl-not-built` feature so mxl-sys binds against the prebuilt MXL
# headers (no cmake/ninja).  dmf-mxl is copied to /build/dmf-mxl so the MXL
# library path baked into the binary is
# /build/dmf-mxl/build/Linux-Clang-Release/lib/libmxl.so — the runtime stage
# symlinks exactly that path to /opt/mxl/lib/libmxl.so.
# ─────────────────────────────────────────────────────────────────────────────
FROM rust:1-bookworm AS backend-builder

# libclang is required by bindgen (mxl-sys).
RUN apt-get update && apt-get install -y --no-install-recommends \
        clang \
        libclang-dev \
    && rm -rf /var/lib/apt/lists/*

# MXL SDK sources/headers + the whole rust workspace (needed because the `mxl`
# crate inherits package fields from the dmf-mxl/rust workspace manifest).
COPY dmf-mxl/lib  /build/dmf-mxl/lib
COPY dmf-mxl/rust /build/dmf-mxl/rust
COPY dmf-mxl/LICENSE.txt /build/dmf-mxl/LICENSE.txt

# The backend crate (path-deps on ../../../dmf-mxl/rust/mxl — layout preserved).
COPY gst-apps/input-selector/backend-rs /build/gst-apps/input-selector/backend-rs

WORKDIR /build/gst-apps/input-selector/backend-rs
RUN cargo build --release

# ─────────────────────────────────────────────────────────────────────────────
# Stage 3 – Runtime on Ubuntu 24.04
# ─────────────────────────────────────────────────────────────────────────────
FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive

# ── MXL runtime library ───────────────────────────────────────────────────────
COPY dmf-mxl/build/Linux-Clang-Release/lib/libmxl.so.1.2 /opt/mxl/lib/libmxl.so.1.2

RUN cd /opt/mxl/lib \
 && ln -sf libmxl.so.1.2 libmxl.so.1 \
 && ln -sf libmxl.so.1   libmxl.so \
 && ldconfig /opt/mxl/lib \
 # The binary dlopen()s libmxl.so from the absolute path baked at compile time
 # (/build/dmf-mxl/build/Linux-Clang-Release/lib/libmxl.so); satisfy it.
 && mkdir -p /build/dmf-mxl/build/Linux-Clang-Release/lib \
 && ln -sf /opt/mxl/lib/libmxl.so /build/dmf-mxl/build/Linux-Clang-Release/lib/libmxl.so

# ── mxl-info CLI tool (used for input flow discovery) ─────────────────────────
COPY dmf-mxl/build/Linux-Clang-Release/tools/mxl-info/mxl-info /opt/mxl/tools/mxl-info/mxl-info
RUN chmod +x /opt/mxl/tools/mxl-info/mxl-info

# ── Application: Rust binary + built frontend ─────────────────────────────────
COPY --from=backend-builder \
     /build/gst-apps/input-selector/backend-rs/target/release/input-selector-router \
     /app/input-selector-router
COPY --from=frontend-builder /build/dist /app/frontend/dist

COPY gst-apps/input-selector/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# ── Environment ───────────────────────────────────────────────────────────────
ENV LD_LIBRARY_PATH=/opt/mxl/lib
ENV MXL_DOMAIN=/mxl-domain
ENV FRONTEND_DIST=/app/frontend/dist
ENV MXL_INFO_BIN=/opt/mxl/tools/mxl-info/mxl-info

EXPOSE 9600

ENTRYPOINT ["/entrypoint.sh"]
