#!/bin/bash

# --- Configuration Variables ---
# Define the source base directories for both architectures
SOURCE_DIR_X86_64="./dmf-mxl/build/Linux-Clang-Release"


# Define component paths relative to the specific SOURCE_DIR (simplified for reuse)
LIB_PATH="lib/*.so*"
COMMON_LIB_PATH="lib/internal/libmxl-common.*"
INFO_TOOL_PATH="tools/mxl-info/mxl-info"
DATA_FILES_PATH="lib/tests/data/*.json"
GST_TOOL_PATH="tools/mxl-gst/"

# --- Setup ---
echo "Starting portable MXL creation script..."

# Change to the root directory once
cd ~/mxl-hands-on || { echo "Error: mxl-hands-on directory not found. Exiting."; exit 1; }

# --- Function for Building and Archiving ---
# $1: tool name (e.g., 'reader')
# $2: specific tool binary to copy (e.g., 'mxl-gst-videosink')
# $3: architecture (e.g., 'x86_64' or 'arm64')
# $4: architecture-specific source directory (e.g., $SOURCE_DIR_X86_64)
function build_portable() {
    local TOOL_NAME="$1"
    local TOOL_BINARY="$2"
    local ARCHITECTURE="$3"
    local SOURCE_DIR="$4"

    # Define paths with the architecture included
    local TARGET_DIR="../portable-mxl-${TOOL_NAME}-${ARCHITECTURE}"
    local TAR_FILE="../portable-mxl-${TOOL_NAME}-${ARCHITECTURE}.tar.gz"
    # Ensure final destination is structured by application and architecture
    local FINAL_DEST="./Portable-mxl-app/mxl-${TOOL_NAME}/${ARCHITECTURE}"

    echo "--- Building ${TOOL_NAME} for ${ARCHITECTURE} from ${SOURCE_DIR} ---"

    # 1. Create target directory and final destination folder
    mkdir -p "${TARGET_DIR}" || { echo "Error creating temporary directory ${TARGET_DIR}. Aborting."; return 1; }
    mkdir -p "${FINAL_DEST}" || { echo "Error creating final destination ${FINAL_DEST}. Aborting."; return 1; }

    # 2. Copy common files using the architecture-specific SOURCE_DIR
    cp ${SOURCE_DIR}/${LIB_PATH} "${TARGET_DIR}/"
    cp ${SOURCE_DIR}/${COMMON_LIB_PATH} "${TARGET_DIR}/"
    cp ${SOURCE_DIR}/${INFO_TOOL_PATH} "${TARGET_DIR}/"
    cp ${SOURCE_DIR}/${DATA_FILES_PATH} "${TARGET_DIR}/"

    # 3. Copy specific tool binary
    cp "${SOURCE_DIR}/${GST_TOOL_PATH}/${TOOL_BINARY}" "${TARGET_DIR}/"

    # 4. Create compressed archive
    # The -C flag changes the directory before archiving, ensuring the contents are at the root of the tar file.
    tar -czf "${TAR_FILE}" -C "${TARGET_DIR}" .

    # 5. Copy archive to final destination
    cp "${TAR_FILE}" "${FINAL_DEST}/"

    # Special case for the reader's second destination (assuming this is only needed for the primary x86_64 build)
    if [ "${TOOL_NAME}" == "reader" ] && [ "${ARCHITECTURE}" == "x86_64" ]; then
        mkdir -p "./docker/exercise-3/data/"
        cp "${TAR_FILE}" "./docker/exercise-3/data/portable-mxl-${TOOL_NAME}-${ARCHITECTURE}.tar.gz"
    fi

    echo "Successfully created and placed ${TAR_FILE} in ${FINAL_DEST}"
}

# --- Execution ---

# Define the list of applications and their binaries to package
APP_CONFIGS=(
    "reader mxl-gst-videosink"
    "writer mxl-gst-videotestsrc"
    "loop-player mxl-gst-looping-filesrc"
)

# Define the architectures to build
ARCHITECTURES=(
    "x86_64"
)

# Loop through all architectures and applications to build everything
for arch in "${ARCHITECTURES[@]}"; do
    SOURCE_DIR_VAR=""
    if [ "$arch" == "x86_64" ]; then
        SOURCE_DIR_VAR="$SOURCE_DIR_X86_64"
    elif [ "$arch" == "arm64" ]; then
        SOURCE_DIR_VAR="$SOURCE_DIR_ARM64"
    fi

    # Check if the source directory exists before trying to build
    if [ -d "$SOURCE_DIR_VAR" ]; then
        for app_config in "${APP_CONFIGS[@]}"; do
            # Read the tool name and binary name from the configuration string
            read TOOL_NAME TOOL_BINARY <<< "$app_config"
            build_portable "$TOOL_NAME" "$TOOL_BINARY" "$arch" "$SOURCE_DIR_VAR"
        done
    else
        echo "Warning: Source directory for ${arch} (${SOURCE_DIR_VAR}) not found. Skipping builds for this architecture."
    fi
done


# --- Cleanup ---
echo "Cleaning up temporary tar files from parent directory..."
# This cleans up the temporary tar files created in the parent directory (../)
rm -f ../portable-mxl-*.tar.gz

echo "Script finished successfully! Check Portable-mxl-app/ for your archives."