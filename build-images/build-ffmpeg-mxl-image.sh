#!/usr/bin/env bash
#
# Build script for FFmpeg with MXL Docker image
# Prerequisites: MXL must be built first using ../build_all.sh

set -e

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd -P)"

echo "Building FFmpeg with MXL Docker image..."
echo "Project root: ${PROJECT_ROOT}"
echo ""

# Check if MXL is already built
MXL_INSTALL_DIR="${PROJECT_ROOT}/dmf-mxl/install_x86_64"

if [ ! -d "${MXL_INSTALL_DIR}/lib" ]; then
    echo "ERROR: MXL install directory not found!"
    echo ""
    echo "Please build MXL first using the build_all.sh script:"
    echo "  cd ${PROJECT_ROOT}"
    echo "  ./build_all.sh --arch x86_64"
    echo ""
    echo "This will create the required MXL libraries at:"
    echo "  ${MXL_INSTALL_DIR}"
    echo ""
    exit 1
fi

echo "✓ MXL build found at: ${MXL_INSTALL_DIR}"

# Verify required files exist
REQUIRED_FILES=(
    "${MXL_INSTALL_DIR}/lib/libmxl.so"
    "${MXL_INSTALL_DIR}/include/mxl/mxl.h"
)

for file in "${REQUIRED_FILES[@]}"; do
    if [ ! -f "$file" ]; then
        echo "ERROR: Required file not found: $file"
        echo "Please rebuild MXL using: ./build_all.sh --arch x86_64"
        exit 1
    fi
done

echo "✓ All required MXL files present"
echo ""

# Build the Docker image from the project root so relative paths work
echo "Building FFmpeg with MXL Docker image..."
echo "This may take 10-30 minutes depending on your system..."
echo ""

cd "${PROJECT_ROOT}"
docker build \
    -f build-images/Dockerfile.ffmpeg-mxl.txt \
    -t mxl-ffmpeg:latest \
    .

echo ""
echo "========================================="
echo "✓ Build complete!"
echo "========================================="
echo ""
echo "Image: mxl-ffmpeg:latest"
echo ""
echo "Test the image with:"
echo "  docker run --rm mxl-ffmpeg:latest ffmpeg -version"
echo ""
echo "Or tag and push to a registry:"
echo "  docker tag mxl-ffmpeg:latest ghcr.io/yourusername/mxl-ffmpeg:latest"
echo "  docker push ghcr.io/yourusername/mxl-ffmpeg:latest"
