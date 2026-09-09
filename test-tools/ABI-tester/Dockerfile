# SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
# SPDX-License-Identifier: Apache-2.0
# Multi-stage Dockerfile for the MXL ABI tester.
# Build context: the repository root (two levels above this file).
#
# Stage 1 - build the React + Vite frontend
# Stage 2 - build the C++ backend against the prebuilt MXL SDK
# Stage 3 - runtime: libmxl, the binary, the built frontend. No node, no cmake.

# ------------------------------------------------------------------------------
# Stage 1 - frontend
#
# node:22, not the spec's node:18: Vite 8.2.2 and @vitejs/plugin-react 6.1.0
# both declare engines ^20.19.0 || >=22.12.0
# ------------------------------------------------------------------------------
FROM node:22-bookworm-slim AS frontend-builder

WORKDIR /build

COPY test-tools/ABI-tester/frontend/package.json \
     test-tools/ABI-tester/frontend/package-lock.json \
     ./

RUN npm ci

COPY test-tools/ABI-tester/frontend/ ./

RUN npm run build

# -------------------------------------------------------------------------------
# Stage 2 - backend. Ubuntu 24.04 ships both dependencies, so nothing is vendored.
# -------------------------------------------------------------------------------
FROM ubuntu:24.04 AS backend-builder

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        g++ cmake make pkg-config nlohmann-json3-dev libcpp-httplib-dev \
    && rm -rf /var/lib/apt/lists/*

# Laid out as the CMakeLists expects: $MXL_ROOT/lib/include for the headers,
# $MXL_ROOT/build/$MXL_PRESET/lib for the .so.
COPY dmf-mxl/lib/include /opt/mxl/lib/include
COPY dmf-mxl/build/Linux-Clang-Release/lib/libmxl.so.1.1 \
     /opt/mxl/build/Linux-Clang-Release/lib/libmxl.so.1.1
RUN cd /opt/mxl/build/Linux-Clang-Release/lib \
 && ln -sf libmxl.so.1.1 libmxl.so.1 \
 && ln -sf libmxl.so.1   libmxl.so

COPY test-tools/ABI-tester/backend /build/backend
WORKDIR /build/backend
RUN cmake -B build -DCMAKE_BUILD_TYPE=Release -DMXL_ROOT=/opt/mxl \
 && cmake --build build -j

# --------------------------------------------------------------------------------
# Stage 3 - runtime. No node, no cmake, no headers: nothing here compiles.
# The headers were a build-time contract (what the calls look like); what runs
# needs only the .so that implements them, found by SONAME at load time.
# --------------------------------------------------------------------------------
FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        libcpp-httplib0.14t64 curl \
    && rm -rf /var/lib/apt/lists/*

# Registered in ld.so.conf.d rather than passed as LD_LIBRARY_PATH: the binary's
# baked RPATH points into stage 2 and does not exist here.
COPY dmf-mxl/build/Linux-Clang-Release/lib/libmxl.so.1.1 /opt/mxl/lib/libmxl.so.1.1
RUN cd /opt/mxl/lib \
 && ln -sf libmxl.so.1.1 libmxl.so.1 \
 && ln -sf libmxl.so.1   libmxl.so \
 && echo /opt/mxl/lib > /etc/ld.so.conf.d/mxl.conf \
 && ldconfig

# Cross-checking convenience only; the backend does not depend on it.
COPY dmf-mxl/build/Linux-Clang-Release/tools/mxl-info/mxl-info /opt/mxl/tools/mxl-info/mxl-info
RUN chmod +x /opt/mxl/tools/mxl-info/mxl-info

COPY --from=backend-builder  /build/backend/build/abi-tester /app/abi-tester
COPY --from=frontend-builder /build/dist                     /app/frontend/dist
COPY test-tools/ABI-tester/flows        /app/flows
COPY test-tools/ABI-tester/scenarios    /app/scenarios
COPY test-tools/ABI-tester/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV MXL_DOMAIN_ROOT=/mxl-domain
ENV FRONTEND_DIST=/app/frontend/dist
ENV SCENARIO_DIR=/app/scenarios

EXPOSE 9600
ENTRYPOINT ["/entrypoint.sh"]