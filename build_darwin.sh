#!/bin/bash

# ==============================================================================
# SCRIPT: build_darwin.sh
# INTENDED SYSTEM: macOS (requires native CMake, Clang, and Xcode tools)
# PURPOSE: Builds the Darwin-Clang-Release preset directly on the host macOS
#          machine, as cross-compiling macOS targets in Linux Docker is complex.
# ==============================================================================

# Define project path
MXL_PROJECT_PATH="./dmf-mxl"
COMP="Darwin-Clang-Release"

echo "=================================================================="
echo "Starting native macOS build with compiler: ${COMP}"
echo "=================================================================="

# Check if dmf-mxl directory exists
if [ ! -d "$MXL_PROJECT_PATH" ]; then
  echo "Error: dmf-mxl directory not found. Make sure you're running this script from the correct location."
  exit 1
fi

# Check for necessary tools
if ! command -v cmake &> /dev/null; then
    echo "ERROR: CMake is not installed or not in PATH. Please install it (e.g., via Homebrew)."
    exit 1
fi

# Ensure the build directory is ready
BUILD_DIR="${MXL_PROJECT_PATH}/build/${COMP}"
INSTALL_DIR="${MXL_PROJECT_PATH}/install"

mkdir -p "${BUILD_DIR}"
mkdir -p "${INSTALL_DIR}"

# 1. Configure CMake
echo "--- Configuring CMake ---"
cmake -S "${MXL_PROJECT_PATH}" -B "${BUILD_DIR}" \
  --preset "${COMP}" \
  -DCMAKE_INSTALL_PREFIX="${INSTALL_DIR}"

if [ $? -ne 0 ]; then
    echo "ERROR: CMake configuration failed for ${COMP}. Check your CMake preset and environment."
    exit 1
fi

# 2. Build Project
echo "--- Building Project ---"
cmake --build "${BUILD_DIR}" -t all doc install package

if [ $? -ne 0 ]; then
    echo "ERROR: Project build failed for ${COMP}."
    exit 1
fi

# 3. Run Tests
echo "--- Running Tests ---"
cd "${BUILD_DIR}" && \
ctest --output-junit test-results.xml

if [ $? -ne 0 ]; then
    echo "WARNING: Tests failed for ${COMP}. Check test results in ${BUILD_DIR}/test-results.xml"
fi

echo "=================================================================="
echo "All macOS builds completed for ${COMP}!"
echo "Build artifacts can be found in ${MXL_PROJECT_PATH}/build/ and ${MXL_PROJECT_PATH}/install_*"
echo "=================================================================="