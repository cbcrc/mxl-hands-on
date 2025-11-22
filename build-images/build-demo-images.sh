#!/bin/bash
# Ensure this script runs with a compatible version of bash
# macOS ships with Bash 3.2 by default

# This script builds multi-architecture Docker images for the MXL project
# by explicitly building each architecture and then combining them into a manifest.

set -e

# --- USER INPUT AND SETUP ---

# Function to prompt for GHCR username
prompt_for_username() {
  read -p "Enter your GHCR.io username (e.g., myuser): " USERNAME
  if [ -z "$USERNAME" ]; then
    echo "Username cannot be empty. Exiting."
    exit 1
  fi
  # Set the full registry path
  GHCR_REGISTRY="ghcr.io/$USERNAME"
  echo "Using GHCR Registry: ${GHCR_REGISTRY}"
}

# Get current date for tagging
CURRENT_DATE=$(date +%Y-%m-%d)
echo "Using date tag: ${CURRENT_DATE}"
echo ""

# Ask the user for their GHCR username
prompt_for_username

# --- END USER INPUT AND SETUP ---

# Script location detection
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "Script directory: ${SCRIPT_DIR}"

# Default values
ARCHITECTURES=("x86_64" "arm64")  
COMPILERS=("Linux-GCC-Release" "Linux-Clang-Release")
DEFAULT_COMPILER="Linux-Clang-Release"
DEFAULT_ARCH="x86_64"  

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

# Create platform mapping
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

# Ensure we have Docker BuildX with proper driver
# We still use buildx to ensure cross-compilation is possible and to push.
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

# Create a function to build a single-arch image
build_single_arch_image() {
  local service=$1
  local compiler=$2
  local arch=$3
  local platform=$4
  local tag=$5 # This is the unique tag: compiler-arch
  
  # Calculate the full, final path string (relative to build context, which is ROOT_DIR)
  # Example: build/GCC-Release_x86_64
  FULL_BUILD_PATH="build/${compiler}_${arch}"

  # Determine the executable path to verify existence
  if [ "$service" == "writer" ]; then
    EXECUTABLE="${ROOT_DIR}/${FULL_BUILD_PATH}/tools/mxl-gst/mxl-gst-videotestsrc"
  elif [ "$service" == "clip-player" ]; then
    EXECUTABLE="${ROOT_DIR}/${FULL_BUILD_PATH}/tools/mxl-gst/mxl-gst-looping-filesrc"
  else  # reader
    EXECUTABLE="${ROOT_DIR}/${FULL_BUILD_PATH}/tools/mxl-info/mxl-info"
  fi

  if [ ! -f "$EXECUTABLE" ]; then
    echo "WARNING: Executable for $service not found at ${EXECUTABLE}. Skipping platform $platform."
    return 1
  fi
  
  # Create a temporary Dockerfile for the build
  local script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  local temp_dockerfile="${script_dir}/Dockerfile.${service}.temp"
  if [ "$service" == "clip-player" ]; then
    cp "${SCRIPT_DIR}/Dockerfile.clip-player.txt" "$temp_dockerfile"
  else
    cp "${SCRIPT_DIR}/Dockerfile.${service}.txt" "$temp_dockerfile"
  fi
  
  # Define the full tag for the push, including the GHCR registry
  FULL_TAG="$GHCR_REGISTRY/mxl-$service:$tag"
  
  echo "====================================================="
  echo "Building single-architecture image for: $service"
  echo "Compiler: $compiler, Architecture: $arch, Platform: $platform"
  echo "Pushing tag: $FULL_TAG"
  echo "Build path argument: $FULL_BUILD_PATH"
  echo "====================================================="
  
  # Build and push the single-arch image using the platform and path as build-arg
  docker buildx build \
    --platform "$platform" \
    --build-arg FULL_BUILD_PATH="$FULL_BUILD_PATH" \
    --tag "$FULL_TAG" \
    --file "$temp_dockerfile" \
    --push=true \
    "${ROOT_DIR}"
  
  # Clean up temporary Dockerfile
  rm -f "$temp_dockerfile"
  
  echo "Completed pushing single-arch image $FULL_TAG"
  return 0
}

# Map to store successfully pushed tags for manifest creation
declare -A IMAGE_TAGS

# Main loop to build all individual arch images
for COMPILER in "${COMPILERS[@]}"; do
  # Convert compiler name to lowercase for unique tagging
  COMPILER_LOWER=$(echo ${COMPILER} | tr '[:upper:]' '[:lower:]')
  
  for SERVICE in "writer" "reader" "clip-player"; do
    
    # Initialize the array for this specific service and compiler base tag
    MANIFEST_TAGS_ARRAY=()
    
    for ARCH in "${ARCHITECTURES[@]}"; do
      PLATFORM=$(get_platform "$ARCH")
      
      # Tag is unique to compiler and architecture
      UNIQUE_TAG="${COMPILER_LOWER}-${ARCH}"
      
      # Build the image and check if successful (return 0)
      if build_single_arch_image "$SERVICE" "$COMPILER" "$ARCH" "$PLATFORM" "$UNIQUE_TAG"; then
        # If successful, add the unique tag to the manifest list
        MANIFEST_TAGS_ARRAY+=("$GHCR_REGISTRY/mxl-$SERVICE:$UNIQUE_TAG")
      fi
    done
    
    # Check if we have any successful builds for this service/compiler combination
    if [ ${#MANIFEST_TAGS_ARRAY[@]} -gt 0 ]; then
      
      # The base tag for the multi-arch manifest is just the compiler name
      BASE_TAG="$GHCR_REGISTRY/mxl-$SERVICE:$COMPILER_LOWER"
      
      # Join the array elements with spaces for the imagetools create command
      MANIFEST_SOURCES=$(IFS=$' '; echo "${MANIFEST_TAGS_ARRAY[*]}")
      
      echo "====================================================="
      echo "Creating multi-arch manifest $BASE_TAG"
      echo "Sources: ${MANIFEST_SOURCES}"
      echo "====================================================="
      
      # Use the first successful image as the base and add the others
      docker buildx imagetools create "${MANIFEST_TAGS_ARRAY[0]}" "${MANIFEST_TAGS_ARRAY[@]:1}" -t "$BASE_TAG"
      
      echo "Successfully pushed multi-arch manifest $BASE_TAG"
      echo ""

      # Store the successful base manifest tag for 'latest' creation
      if [ "$COMPILER" = "$DEFAULT_COMPILER" ]; then
        # Store the base tag for later creation of 'latest' and 'date' tags
        IMAGE_TAGS["$SERVICE"]="$BASE_TAG"
      fi
      
    else
      echo "WARNING: Could not build any architecture for $SERVICE with compiler $COMPILER. Skipping manifest creation."
      echo ""
    fi
  done
done

# Create 'latest' and 'date' manifests for the default compiler
if [ "$DEFAULT_COMPILER" = "$COMPILER" ]; then
    echo "====================================================="
    echo "Creating 'latest' and 'date' manifests for default compiler: $DEFAULT_COMPILER"
    echo "====================================================="

    for SERVICE in "writer" "reader" "clip-player"; do
        BASE_TAG="${IMAGE_TAGS[$SERVICE]}"
        
        if [ -z "$BASE_TAG" ]; then
          echo "Skipping 'latest' and 'date' manifests for $SERVICE as no valid manifest was created with the default compiler."
          continue
        fi

        # Create 'latest' manifest and push it
        LATEST_TAG="$GHCR_REGISTRY/mxl-$SERVICE:latest"
        echo "Creating manifest $LATEST_TAG pointing to $BASE_TAG"
        docker buildx imagetools create "$BASE_TAG" -t "$LATEST_TAG"
        
        # Create 'current_date' manifest and push it
        DATE_TAG="$GHCR_REGISTRY/mxl-$SERVICE:$CURRENT_DATE"
        echo "Creating manifest $DATE_TAG pointing to $BASE_TAG"
        echo "Source: $BASE_TAG"
        docker buildx imagetools create "$BASE_TAG" -t "$DATE_TAG"
    done
fi

# Reset to default for convenience
export COMPILER=$DEFAULT_COMPILER
export COMPILER_LOWER=$(echo ${COMPILER} | tr '[:upper:]' '[:lower:]')

echo "All builds and pushes completed!"
echo "---"
echo "To run the demo locally, use the images you pushed to GHCR.io. Example:"
echo "  docker pull $GHCR_REGISTRY/mxl-writer:latest"
echo "The docker-compose.yaml you provided already uses the tag variable convention, assuming your shell is set up:"
echo "  image: $GHCR_REGISTRY/mxl-writer:\${COMPILER_LOWER:-linux-clang-release}"
echo "And run the demo:"
echo "  docker-compose -f ${SCRIPT_DIR}/docker-compose.yaml up"