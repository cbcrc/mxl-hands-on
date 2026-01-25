#!/bin/bash
# ==============================================================================
# SCRIPT: build-demo-images.sh (Local Build - Clean Tags)
# PURPOSE: Builds amd64 Docker images locally using artifacts from build_linux.sh
#          Images are tagged simply (e.g., mxl-writer:latest) and loaded locally.
# ==============================================================================

set -e

# --- SETUP ---

# Get current date for tagging
CURRENT_DATE=$(date +%Y-%m-%d)

# Script location detection
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "Script directory: ${SCRIPT_DIR}"

# Configuration
ARCH="x86_64"               # Fixed to x86_64 for Linux build
PLATFORM="linux/amd64"      # Docker platform target
COMPILERS=("Linux-GCC-Release" "Linux-Clang-Release")
DEFAULT_COMPILER="Linux-Clang-Release"

# Determine the correct path to the project root
ROOT_DIR="../dmf-mxl"
if [ ! -d "${ROOT_DIR}" ]; then
  if [ -d "./dmf-mxl" ]; then
    ROOT_DIR="./dmf-mxl"
  else
    echo "ERROR: Could not locate dmf-mxl directory."
    exit 1
  fi
fi

# Verify build directory exists
if [ ! -d "${ROOT_DIR}/build" ]; then
  echo "ERROR: No build directory found in ${ROOT_DIR}."
  echo "       Please run build_linux.sh first to generate build artifacts."
  exit 1
fi

echo "Building Docker images for Architecture: ${ARCH}"
echo "Using compilers: ${COMPILERS[*]}"
echo ""

# Function to build image
build_image() {
  local service=$1      # e.g., reader, writer, clip-player
  local compiler=$2     # e.g., Linux-GCC-Release
  
  # Convert compiler name to lowercase for tagging
  local compiler_lower=$(echo ${compiler} | tr '[:upper:]' '[:lower:]')

  # 1. Determine Build Path
  # build_linux.sh outputs to build/${COMPILER}
  FULL_BUILD_PATH="/build/${compiler}"

  # 2. Determine Executable Path (for validation)
  if [ "$service" == "writer" ]; then
    EXECUTABLE="${ROOT_DIR}/${FULL_BUILD_PATH}/tools/mxl-gst/mxl-gst-testsrc"
  elif [ "$service" == "clip-player" ]; then
    EXECUTABLE="${ROOT_DIR}/${FULL_BUILD_PATH}/tools/mxl-gst/mxl-gst-looping-filesrc"
  else  # reader
    EXECUTABLE="${ROOT_DIR}/${FULL_BUILD_PATH}/tools/mxl-info/mxl-info"
  fi

  if [ ! -f "$EXECUTABLE" ]; then
    echo "WARNING: Executable for $service not found at ${EXECUTABLE}."
    echo "         Skipping build for $service ($compiler)."
    return 1
  fi
  
  # 3. Prepare Dockerfile
  local temp_dockerfile="${SCRIPT_DIR}/Dockerfile.${service}.temp"
  if [ "$service" == "clip-player" ]; then
    # Maps user's 'mxl_loop_player' request to the existing 'clip-player' file
    cp "${SCRIPT_DIR}/Dockerfile.clip-player.txt" "$temp_dockerfile"
  else
    cp "${SCRIPT_DIR}/Dockerfile.${service}.txt" "$temp_dockerfile"
  fi

  # 4. Build Tags
  # Tag 1: Specific compiler version (e.g., mxl-writer:linux-gcc-release)
  TAG_COMPILER="mxl-$service:$compiler_lower"
  
  # Note: --load is used to keep images local
  BUILD_ARGS=(
    "--platform" "$PLATFORM"
    "--build-arg" "FULL_BUILD_PATH=$FULL_BUILD_PATH"
    "--tag" "$TAG_COMPILER"
    "--file" "$temp_dockerfile"
    "--load" 
  )

  # If this is the default compiler, add :latest and :date tags
  if [ "$compiler" == "$DEFAULT_COMPILER" ]; then
    TAG_LATEST="mxl-$service:latest"
    TAG_DATE="mxl-$service:$CURRENT_DATE"
    BUILD_ARGS+=("--tag" "$TAG_LATEST")
    BUILD_ARGS+=("--tag" "$TAG_DATE")
    echo "   -> Marking as 'latest' and '$CURRENT_DATE'"
  fi

  echo "-----------------------------------------------------"
  echo "Building (Local): mxl-$service"
  echo "Compiler: $compiler | Path: $FULL_BUILD_PATH"
  echo "Tags: ${TAG_COMPILER} ..."
  echo "-----------------------------------------------------"

  # 5. Run Docker BuildX
  # Ensure builder exists
  docker buildx inspect mxl-builder > /dev/null 2>&1 || docker buildx create --name mxl-builder --driver docker-container --bootstrap --use

  docker buildx build "${BUILD_ARGS[@]}" "${ROOT_DIR}"
  
  # Cleanup
  rm -f "$temp_dockerfile"
  echo "Done."
}

# --- Main Loop ---

for COMPILER in "${COMPILERS[@]}"; do
  # Build mapped services:
  # mxl_writer      -> writer
  # mxl_reader      -> reader
  # mxl_loop_player -> clip-player
  
  build_image "writer" "$COMPILER"
  build_image "reader" "$COMPILER"
  build_image "clip-player" "$COMPILER"
done

echo ""
echo "=================================================================="
echo "All images built locally."
echo "Verify them with: docker images | grep mxl-"
echo ""
echo "To run the demo, ensure your docker-compose.yaml uses these clean image names:"
echo "  image: mxl-writer:latest"
echo "  image: mxl-reader:latest"
echo "  image: mxl-clip-player:latest"
echo "=================================================================="