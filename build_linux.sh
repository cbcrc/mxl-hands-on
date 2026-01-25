#!/bin/bash

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

for COMP in "${COMPILERS[@]}"; do
    echo "=================================================================="
    echo "Building with compiler: ${COMP}"
    echo "=================================================================="
    
    # Convert compiler name to lowercase for Docker tag
    COMP_LOWER=$(echo ${COMP} | tr '[:upper:]' '[:lower:]')
    
    # 1. Build Docker image
    # Note: The backslash below fixes the line continuation error from the original script.
    docker build \
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
    docker run --mount src=$(pwd)/${MXL_PROJECT_PATH},target=/workspace/mxl,type=bind \
      -e VCPKG_BINARY_SOURCES="clear;files,/workspace/mxl/vcpkg_cache,readwrite" \
      -i mxl_build_container_${COMP_LOWER} \
      bash -c "
        cmake -S /workspace/mxl -B /workspace/mxl/build/${COMP} \
          --preset ${COMP} \
          -DCMAKE_INSTALL_PREFIX=/workspace/mxl/install
      "
    
    # 3. Build Project
    echo "--- Building Project ---"
    docker run --mount src=$(pwd)/${MXL_PROJECT_PATH},target=/workspace/mxl,type=bind \
      -i mxl_build_container_${COMP_LOWER} \
      bash -c "
        cmake --build /workspace/mxl/build/${COMP} -t all doc install package
      "
    
    # 4. Run Tests
    echo "--- Running Tests ---"
    docker run --mount src=$(pwd)/${MXL_PROJECT_PATH},target=/workspace/mxl,type=bind \
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