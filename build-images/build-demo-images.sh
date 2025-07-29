#!/bin/bash
# Ensure this script runs with a compatible version of bash
# macOS ships with Bash 3.2 by default

# This script builds multi-architecture Docker images for the MXL project
# It can be used to create smaller demonstration images
# Creates proper multi-arch manifests that show up as a single image with multiple platforms

set -e

# Script location detection
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "Script directory: ${SCRIPT_DIR}"

# Default values
ARCHITECTURES=("x86_64" "arm64")  # Changed amd64 to x86_64 to match build_all.sh
COMPILERS=("Linux-GCC-Release" "Linux-Clang-Release")
DEFAULT_COMPILER="Linux-Clang-Release"
DEFAULT_ARCH="x86_64"  # Changed amd64 to x86_64 to match build_all.sh

# Parse command line arguments
while [[ $# -gt 0 ]]; do
  key="$1"
  case $key in
    --arch)
      ARCHITECTURES=("$2")
      shift 2
      ;;
    --compiler)
      COMPILERS=("$2")
      shift 2
      ;;
    --help)
      echo "Usage: $0 [OPTIONS]"
      echo "Build multi-architecture Docker images for MXL demonstrations"
      echo ""
      echo "Options:"
      echo "  --arch ARCH         Architecture to build (x86_64 or arm64, default: both)"
      echo "  --compiler COMPILER Compiler to use (Linux-GCC-Release or Linux-Clang-Release, default: both)"
      echo "  --help              Display this help message"
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
done

echo "Building Docker images for architectures: ${ARCHITECTURES[*]}"
echo "Using compilers: ${COMPILERS[*]}"
echo ""

# Create platform mapping using a simpler approach compatible with older Bash versions
get_platform() {
  local arch=$1
  if [ "$arch" = "x86_64" ] || [ "$arch" = "amd64" ]; then
    echo "linux/amd64"
  elif [ "$arch" = "arm64" ]; then
    echo "linux/arm64"
  else
    echo "Unknown architecture: $arch" >&2
    exit 1
  fi
}

# Ensure we have Docker BuildX with proper driver for multi-arch builds
docker buildx inspect mxl-builder > /dev/null 2>&1 || docker buildx create --name mxl-builder --driver docker-container --bootstrap --use

# Determine the correct path to the project root
ROOT_DIR="../dmf-mxl"
if [ ! -d "${ROOT_DIR}" ]; then
  # Check if we're in the project root directory
  if [ -d "./dmf-mxl" ]; then
    ROOT_DIR="./dmf-mxl"
  else
    echo "ERROR: Could not locate dmf-mxl directory with build artifacts."
    echo "       Please run this script from the build-images directory or the parent directory of dmf-mxl."
    exit 1
  fi
fi

# Verify build directory exists
if [ ! -d "${ROOT_DIR}/build" ]; then
  echo "ERROR: No build directory found in ${ROOT_DIR}."
  echo "       Please run build_all.sh first to generate build artifacts."
  exit 1
fi

# Create a function to build multi-arch images
build_multiarch_image() {
  local service=$1
  local compiler=$2
  local platforms=$3
  local tag=$4

  # Build arguments to pass to buildx
  local build_args=""
  
  # Create a temporary Dockerfile for multi-arch build
  # Get the directory of the current script
  local script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  local temp_dockerfile="${script_dir}/Dockerfile.${service}.multiarch.tmp"
  
  echo "====================================================="
  echo "Building multi-architecture image for: $service"
  echo "Using compiler: $compiler"
  echo "For platforms: $platforms"
  echo "Tag: $tag"
  echo "Root directory: ${ROOT_DIR}"
  echo "====================================================="
  
  # Create platform-specific build context directories
  for ARCH in "${ARCHITECTURES[@]}"; do
    PLATFORM=$(get_platform "$ARCH")
    
    # Only include platforms that are requested and have build artifacts
    if [[ $platforms == *"$PLATFORM"* ]]; then
      # Check if build artifacts exist using absolute path
      BUILD_DIR="${ROOT_DIR}/build/${compiler}_${ARCH}"
      
      if [ "$service" == "writer" ]; then
        EXECUTABLE="${BUILD_DIR}/tools/mxl-gst/mxl-gst-videotestsrc"
      else  # reader
        EXECUTABLE="${BUILD_DIR}/tools/mxl-info/mxl-info"
      fi
      
      if [ ! -f "$EXECUTABLE" ]; then
        echo "ERROR: Executable for $service not found at ${EXECUTABLE}"
        echo "       Please build the project first using build_all.sh"
        echo "       Make sure dmf-mxl directory contains the build artifacts"
        echo "       Skipping platform $PLATFORM for $service"
        
        # Remove this platform from the platforms list
        platforms=${platforms//$PLATFORM/}
        # Clean up any remaining commas
        platforms=${platforms//,,/,}
        platforms=${platforms/%,/}
        platforms=${platforms/#,/}
        
        continue
      fi
      
      # We don't need to pass BUILD_DIR as we're modifying the Dockerfile directly with sed
      # This is just a check to verify the path exists
      if [ -d "${BUILD_DIR}" ]; then
        echo "Found build directory at: ${BUILD_DIR}"
      fi
    fi
  done
  
  # If we have no platforms left, skip this build
  if [ -z "$platforms" ]; then
    echo "No valid platforms found for $service with compiler $compiler, skipping build"
    return
  fi
  
  # Copy the template Dockerfile and modify it to use proper path references
  cp "${script_dir}/Dockerfile.${service}.txt" "$temp_dockerfile"
  
  # Update the BUILD_DIR path in the Dockerfile to use the correct relative path
  # macOS and Linux handle sed -i differently, use a compatible approach
  if [[ "$(uname)" == "Darwin" ]]; then
    sed -i '' "s|ARG BUILD_DIR=.*|ARG BUILD_DIR=build/${compiler}_${ARCH}|g" "$temp_dockerfile"
  else
    sed -i "s|ARG BUILD_DIR=.*|ARG BUILD_DIR=build/${compiler}_${ARCH}|g" "$temp_dockerfile"
  fi
  
  # Build the multi-arch image directly with Docker BuildX
  docker buildx build \
    --platform "$platforms" \
    $build_args \
    --tag "mxl-$service:$tag" \
    --file "$temp_dockerfile" \
    --push=false \
    --load \
    --build-arg ROOT_DIR="." \
    ${ROOT_DIR}
  
  # Clean up temporary Dockerfile
  rm -f "$temp_dockerfile"
  
  echo "Completed building multi-arch image mxl-$service:$tag"
  echo ""
}

# Build multi-arch images for each compiler
for COMPILER in "${COMPILERS[@]}"; do
  # Convert compiler name to lowercase for Docker tag
  COMPILER_LOWER=$(echo ${COMPILER} | tr '[:upper:]' '[:lower:]')
  
  # Create platform list from all architectures
  PLATFORMS=""
  for ARCH in "${ARCHITECTURES[@]}"; do
    if [ -n "$PLATFORMS" ]; then
      PLATFORMS="$PLATFORMS,"
    fi
    PLATFORMS="${PLATFORMS}$(get_platform "$ARCH")"
  done
  
  # Build multi-architecture images for writer and reader
  build_multiarch_image "writer" "$COMPILER" "$PLATFORMS" "$COMPILER_LOWER"
  build_multiarch_image "reader" "$COMPILER" "$PLATFORMS" "$COMPILER_LOWER"
  
  # Also tag as latest if it's the default compiler
  if [ "$COMPILER" = "$DEFAULT_COMPILER" ]; then
    docker tag "mxl-writer:$COMPILER_LOWER" "mxl-writer:latest"
    docker tag "mxl-reader:$COMPILER_LOWER" "mxl-reader:latest"
  fi
done

# Reset to default for convenience
export COMPILER=$DEFAULT_COMPILER
export COMPILER_LOWER=$(echo ${COMPILER} | tr '[:upper:]' '[:lower:]')

echo "All builds completed!"
echo "You can now run the demo with: docker-compose -f ${SCRIPT_DIR}/docker-compose.yaml up"
echo ""
echo "Or specify a specific compiler with:"
echo "COMPILER=linux-gcc-release docker-compose -f ${SCRIPT_DIR}/docker-compose.yaml up"
echo ""
echo "Using build artifacts from: ${ROOT_DIR}/build"
echo ""
echo "To verify multi-architecture support, run:"
echo "docker inspect --format='{{.Architecture}}:{{.Os}}' mxl-writer:latest"
echo "OR"
echo "docker buildx imagetools inspect mxl-writer:latest"
echo ""
echo "To view image manifest tree structure:"
echo "docker image ls --tree | grep mxl-"
echo ""
echo "See the README.md for instructions on manually uploading images to Docker Hub if needed."
echo ""
echo "NOTE: Make sure you've built the project for all target architectures first using build_all.sh"
