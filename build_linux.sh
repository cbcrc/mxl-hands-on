#!/bin/bash
# SPDX-FileCopyrightText: 2025 CBC/Radio-Canada
# SPDX-License-Identifier: Apache-2.0

# ==============================================================================
# SCRIPT: build_linux.sh
# INTENDED SYSTEM: Linux (requires Docker installed and running)
# PURPOSE: Builds Linux-targeted presets (GCC/Clang) inside a Docker container
#          to ensure a consistent environment.
# ==============================================================================

# Define project path
MXL_PROJECT_PATH="./dmf-mxl"

# Define compilers to build for Linux targets
COMPILERS=("Linux-GCC-Release" "Linux-Clang-Release")

# Check if dmf-mxl directory exists
if [ ! -d "$MXL_PROJECT_PATH" ]; then
  echo "Error: dmf-mxl directory not found. Make sure you're running this script from the correct location."
  exit 1
fi

# Use the current user's UID/GID for permission consistency with mounted volumes
USER_UID=$(id -u)
USER_GID=$(id -g)

# On macOS the default primary group is "staff" (GID 20), which collides with
# Ubuntu's built-in "dialout" group (also GID 20) and breaks groupadd in the
# container. Fall back to GID == UID, which is unused and avoids the collision.
if [ "$(uname)" = "Darwin" ]; then
    USER_GID=${USER_UID}
fi

for COMP in "${COMPILERS[@]}"; do
    echo "=================================================================="
    echo "Building with compiler: ${COMP}"
    echo "=================================================================="
    
    # Convert compiler name to lowercase for Docker tag
    COMP_LOWER=$(echo ${COMP} | tr '[:upper:]' '[:lower:]')
    
    # 1. Build Docker image
    # Note: The backslash below fixes the line continuation error from the original script.
    # --platform linux/amd64 pins the SDK build to x86_64, matching the hardcoded
    # x86_64-linux-gnu paths in the gst-apps Dockerfiles and their linux/amd64 containers
    # (see gst-apps/docker-compose.yml), regardless of the host's native architecture.
    docker build \
      --platform linux/amd64 \
      --build-arg BASE_IMAGE_VERSION=24.04 \
      --build-arg USER_UID=${USER_UID} \
      --build-arg USER_GID=${USER_GID} \
      \
      -t mxl_build_container_${COMP_LOWER} \
      -f ${MXL_PROJECT_PATH}/.devcontainer/Dockerfile \
      ${MXL_PROJECT_PATH}/.devcontainer

    if [ $? -ne 0 ]; then
        echo "ERROR: Docker image build failed for ${COMP}."
        continue
    fi

    # 2. Configure CMake
    echo "--- Configuring CMake ---"
    docker run --platform linux/amd64 --mount src=$(pwd)/${MXL_PROJECT_PATH},target=/workspace/mxl,type=bind \
      -e VCPKG_BINARY_SOURCES="clear;files,/workspace/mxl/vcpkg_cache,readwrite" \
      -i mxl_build_container_${COMP_LOWER} \
      bash -c "
        cmake -S /workspace/mxl -B /workspace/mxl/build/${COMP} \
          --preset ${COMP} \
          -DCMAKE_INSTALL_PREFIX=/workspace/mxl/install
      "

    # 3. Build Project
    echo "--- Building Project ---"
    docker run --platform linux/amd64 --mount src=$(pwd)/${MXL_PROJECT_PATH},target=/workspace/mxl,type=bind \
      -i mxl_build_container_${COMP_LOWER} \
      bash -c "
        cmake --build /workspace/mxl/build/${COMP} -t all doc install package
      "

    # 4. Run Tests
    echo "--- Running Tests ---"
    docker run --platform linux/amd64 --mount src=$(pwd)/${MXL_PROJECT_PATH},target=/workspace/mxl,type=bind \
      -i mxl_build_container_${COMP_LOWER} \
      bash -c "
        cd /workspace/mxl/build/${COMP} && \
        ctest --output-junit test-results.xml
      "
      
    echo "Finished build for ${COMP}"
done

echo "=================================================================="
echo "All Linux builds completed!"
echo "Build artifacts can be found in ${MXL_PROJECT_PATH}/build/ and ${MXL_PROJECT_PATH}/install_*"
echo "=================================================================="

# ==============================================================================
# Rust bindings + GStreamer plugin (depends on Linux-Clang-Release C build)
# ==============================================================================
RUST_PRESET="Linux-Clang-Release"
RUST_BUILD_DIR="${MXL_PROJECT_PATH}/build/${RUST_PRESET}"

if [ ! -d "${RUST_BUILD_DIR}" ]; then
    echo "WARNING: ${RUST_BUILD_DIR} not found — skipping Rust build."
    echo "Make sure the C library built successfully for ${RUST_PRESET}."
    exit 0
fi

echo "=================================================================="
echo "Building Rust bindings and GStreamer plugin"
echo "=================================================================="

# 1. Build Rust Docker image (context is repo root so Dockerfile can copy devcontainer scripts)
docker build \
  --platform linux/amd64 \
  --build-arg BASE_IMAGE_VERSION=24.04 \
  --build-arg USER_UID=${USER_UID} \
  --build-arg USER_GID=${USER_GID} \
  -t mxl_rust_build_container \
  -f Dockerfile.rust-build \
  .

if [ $? -ne 0 ]; then
    echo "ERROR: Rust Docker image build failed."
    exit 1
fi

# 2. Build Rust crates (mxl-sys, mxl, gst-mxl-rs)
echo "--- Building Rust crates ---"
docker run \
  --platform linux/amd64 \
  --mount src=$(pwd)/${MXL_PROJECT_PATH},target=/workspace/mxl,type=bind \
  -u ${USER_UID}:${USER_GID} \
  -e CARGO_HOME=/workspace/mxl/rust/.cargo-docker \
  -e LD_LIBRARY_PATH=/workspace/mxl/build/${RUST_PRESET}/lib:/workspace/mxl/build/${RUST_PRESET}/lib/internal \
  -w /workspace/mxl/rust \
  -i mxl_rust_build_container \
  bash -c "cargo build --release --features mxl-sys/mxl-not-built,mxl/mxl-not-built"

if [ $? -ne 0 ]; then
    echo "ERROR: Rust build failed."
    exit 1
fi

echo "=================================================================="
echo "Rust build completed!"
echo "Build artifacts can be found in ${MXL_PROJECT_PATH}/rust/target/release/"
echo "=================================================================="
