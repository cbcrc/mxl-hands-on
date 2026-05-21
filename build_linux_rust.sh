#!/bin/bash

# ==============================================================================
# SCRIPT: build_linux_rust.sh
# INTENDED SYSTEM: Linux (requires Docker installed and running)
# PURPOSE: Builds, lints, and tests the MXL Rust bindings inside a Docker
#          container. Mirrors the steps performed by the GitHub Actions
#          workflow at dmf-mxl/.github/workflows/build.yml (build_rust job).
#
# NOTE:    The Rust bindings link against the native MXL C/C++ libraries, so
#          you typically want to run build_linux.sh first (or at least have a
#          configured/built tree) so vcpkg and CMake artifacts are in place.
# ==============================================================================

# Define project path
MXL_PROJECT_PATH="./dmf-mxl"

# Define compilers (matches workflow matrix; the rust job builds against each)
COMPILERS=("Linux-GCC-Release" "Linux-Clang-Release")

# Ubuntu base image version (matches workflow matrix.version)
BASE_IMAGE_VERSION="24.04"

# Check if dmf-mxl directory exists
if [ ! -d "$MXL_PROJECT_PATH" ]; then
  echo "Error: dmf-mxl directory not found. Make sure you're running this script from the correct location."
  exit 1
fi

if [ ! -d "${MXL_PROJECT_PATH}/rust" ]; then
  echo "Error: ${MXL_PROJECT_PATH}/rust directory not found."
  exit 1
fi

# Use the current user's UID/GID for permission consistency with mounted volumes.
# On macOS the default GID is 20 (staff) and UID is 501, both of which collide
# with reserved accounts/groups inside the Ubuntu base image (e.g. GID 20 =
# dialout). When that happens, fall back to 1000:1000 inside the container.
# Bind-mount permissions are still handled via the `chmod 777` calls below.
HOST_UID=$(id -u)
HOST_GID=$(id -g)
if [ "${HOST_UID}" -lt 1000 ]; then
    USER_UID=1000
else
    USER_UID=${HOST_UID}
fi
if [ "${HOST_GID}" -lt 1000 ]; then
    USER_GID=1000
else
    USER_GID=${HOST_GID}
fi
echo "Host UID/GID: ${HOST_UID}/${HOST_GID} -> container UID/GID: ${USER_UID}/${USER_GID}"

# Ensure build / rust target directories exist with permissions usable in container
mkdir -p "${MXL_PROJECT_PATH}/build"
chmod 777 "${MXL_PROJECT_PATH}/build"
chmod g+s "${MXL_PROJECT_PATH}/build"
mkdir -p "${MXL_PROJECT_PATH}/rust/target"
chmod 777 "${MXL_PROJECT_PATH}/rust/target"
chmod g+s "${MXL_PROJECT_PATH}/rust/target"

# Enable BuildKit (matches workflow env)
export DOCKER_BUILDKIT=1

# Optional: skip the heavy lint step (cargo audit/outdated/deny/machete) by
# setting SKIP_RUST_LINT=1 in the environment. These tools may not all be
# preinstalled in the devcontainer image.
SKIP_RUST_LINT="${SKIP_RUST_LINT:-0}"

for COMP in "${COMPILERS[@]}"; do
    echo "=================================================================="
    echo "Building Rust bindings with compiler image: ${COMP}"
    echo "=================================================================="

    # Convert compiler name to lowercase for Docker tag
    COMP_LOWER=$(echo ${COMP} | tr '[:upper:]' '[:lower:]')
    IMAGE_TAG="mxl_build_container_${COMP_LOWER}"

    # 1. Build Docker image (same devcontainer image used by the C/C++ script)
    docker build \
      --build-arg BASE_IMAGE_VERSION=${BASE_IMAGE_VERSION} \
      --build-arg USER_UID=${USER_UID} \
      --build-arg USER_GID=${USER_GID} \
      -t ${IMAGE_TAG} \
      -f ${MXL_PROJECT_PATH}/.devcontainer/Dockerfile \
      ${MXL_PROJECT_PATH}/.devcontainer

    if [ $? -ne 0 ]; then
        echo "ERROR: Docker image build failed for ${COMP}."
        continue
    fi

    # 2. Check the Rust bindings (formatting, clippy, audits)
    if [ "${SKIP_RUST_LINT}" != "1" ]; then
        echo "--- Checking Rust bindings (fmt / clippy / audit / outdated / machete / deny) ---"
        docker run --mount src=$(pwd)/${MXL_PROJECT_PATH},target=/workspace/mxl,type=bind \
          -e RUSTFLAGS="-Dwarnings" \
          -i ${IMAGE_TAG} \
          bash -c "
            cd /workspace/mxl/rust && \
            cargo fmt -- --check && \
            cargo clippy --all-targets --all-features --locked -- -D warnings && \
            cargo audit && \
            cargo outdated && \
            cargo machete && \
            cargo deny check all
          "

        if [ $? -ne 0 ]; then
            echo "ERROR: Rust lint/check stage failed for ${COMP}."
            continue
        fi
    else
        echo "--- Skipping Rust lint stage (SKIP_RUST_LINT=1) ---"
    fi

    # 3. Build Rust bindings
    echo "--- Building Rust bindings ---"
    docker run --mount src=$(pwd)/${MXL_PROJECT_PATH},target=/workspace/mxl,type=bind \
      -i ${IMAGE_TAG} \
      bash -c "
        cd /workspace/mxl/rust && \
        cargo build --release --all-targets --locked
      "

    if [ $? -ne 0 ]; then
        echo "ERROR: Rust build failed for ${COMP}."
        continue
    fi

    # 4. Test Rust bindings
    echo "--- Testing Rust bindings ---"
    docker run --mount src=$(pwd)/${MXL_PROJECT_PATH},target=/workspace/mxl,type=bind \
      --shm-size=1g \
      -i ${IMAGE_TAG} \
      bash -c "
        cd /workspace/mxl/rust && \
        cargo nextest run --release --locked
      "

    echo "Finished Rust build for ${COMP}"
done

echo "=================================================================="
echo "All Rust binding builds completed!"
echo "Rust artifacts can be found in ${MXL_PROJECT_PATH}/rust/target/"
echo "=================================================================="
