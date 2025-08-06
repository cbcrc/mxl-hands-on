#!/bin/bash

# Define project path
MXL_PROJECT_PATH="./dmf-mxl"

# Define architectures and compilers to build
ARCHITECTURES=("x86_64" "arm64")
COMPILERS=("Linux-GCC-Release" "Linux-Clang-Release")

# Check if dmf-mxl directory exists
if [ ! -d "$MXL_PROJECT_PATH" ]; then
  echo "Error: dmf-mxl directory not found. Make sure you're running this script from the correct location."
  exit 1
fi

for ARCH in "${ARCHITECTURES[@]}"; do
  for COMP in "${COMPILERS[@]}"; do
    echo "Building for architecture: ${ARCH} with compiler: ${COMP}"
    
    # Build Docker image
    # For macOS: Use fixed UID/GID values to avoid permission issues
    if [[ "$(uname)" == "Darwin" ]]; then
      echo "Running on macOS - using fixed UID/GID values"
      USER_UID=1000
      USER_GID=1000
    else
      # For Linux: Use the current user's UID/GID
      USER_UID=$(id -u)
      USER_GID=$(id -g)
    fi
    
    # Convert compiler name to lowercase for Docker tag
    COMP_LOWER=$(echo ${COMP} | tr '[:upper:]' '[:lower:]')
    
    docker build \
      --build-arg BASE_IMAGE_VERSION=24.04 \
      --build-arg USER_UID=${USER_UID} \
      --build-arg USER_GID=${USER_GID} \
      --build-arg ARCHITECTURE=${ARCH} \
      -t mxl_build_container_${ARCH}_${COMP_LOWER} \
      -f ${MXL_PROJECT_PATH}/.devcontainer/Dockerfile \
      ${MXL_PROJECT_PATH}/.devcontainer
    
    # Configure CMake
    docker run --mount src=$(pwd)/${MXL_PROJECT_PATH},target=/workspace/mxl,type=bind \
      -e VCPKG_BINARY_SOURCES="clear;files,/workspace/mxl/vcpkg_cache,readwrite" \
      -i mxl_build_container_${ARCH}_${COMP_LOWER} \
      bash -c "
        cmake -S /workspace/mxl -B /workspace/mxl/build/${COMP}_${ARCH} \
          --preset ${COMP} \
          -DCMAKE_INSTALL_PREFIX=/workspace/mxl/install_${ARCH}
      "
    
    # Build Project
    docker run --mount src=$(pwd)/${MXL_PROJECT_PATH},target=/workspace/mxl,type=bind \
      -i mxl_build_container_${ARCH}_${COMP_LOWER} \
      bash -c "
        cmake --build /workspace/mxl/build/${COMP}_${ARCH} -t all doc install package
      "
    
    # Run Tests
    docker run --mount src=$(pwd)/${MXL_PROJECT_PATH},target=/workspace/mxl,type=bind \
      -i mxl_build_container_${ARCH}_${COMP_LOWER} \
      bash -c "
        cd /workspace/mxl/build/${COMP}_${ARCH} && \
        ctest --output-junit test-results.xml
      "
      
    # Note: The code format check (Step 4) is skipped in this script as it's optional
  done
done

echo "All builds completed!"
echo "Build artifacts can be found in ${MXL_PROJECT_PATH}/build/ and ${MXL_PROJECT_PATH}/install_*"
