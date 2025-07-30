# How to build MXL Docker images

This guide explains how to build multi-architecture Docker images for the MXL project using the updated build scripts.

## Prerequisites

- Docker with BuildX support
- Git with the dmf-mxl repository checked out as a submodule

## Step 1: Build the MXL Project

First, build the project for all required architectures:

```bash
# Run from the repository root
./build_all.sh
```

This script will:

- Determine the correct architecture (x86_64 or arm64)
- Create Docker build containers for each architecture and compiler
- Build the project in the dmf-mxl directory
- Place build artifacts in dmf-mxl/build/

## Step 2: Build Docker Images

After the project is built, create the Docker images:

```bash
# Navigate to the build-images directory first
cd build-images
./build-demo-images.sh

# Or from the repository root (alternative)
bash build-images/build-demo-images.sh
```

This will:

- Create multi-architecture Docker images using the build artifacts
- Generate both reader and writer images for each compiler
- Tag the images appropriately

## Step 3: Upload to image repository

```sh
   current_date=$(date +%Y-%m-%d)
   docker tag mxl-writer:latest ghcr.io/cbcrc/mxl-writer:latest
   docker tag mxl-writer:latest ghcr.io/cbcrc/mxl-writer:$current_date
   docker tag mxl-reader:latest ghcr.io/cbcrc/mxl-reader:latest
   docker tag mxl-reader:latest ghcr.io/cbcrc/mxl-reader:$current_date
   docker push ghcr.io/cbcrc/mxl-writer:latest
   docker push ghcr.io/cbcrc/mxl-writer:$current_date
   docker push ghcr.io/cbcrc/mxl-reader:latest
   docker push ghcr.io/cbcrc/mxl-reader:$current_date
```

## Step 4: Create `portable-mxl-reader` for Excercise3

```sh
   cd ~/mxl-hands-on
   mkdir ../portable-mxl-reader
   cp ./dmf-mxl/build/Linux-Clang-Release_x86_64/lib/*.so* ../portable-mxl-reader/
   cp ./dmf-mxl/build/Linux-Clang-Release_x86_64/tools/mxl-info/mxl-info ../portable-mxl-reader/
   cp ./dmf-mxl/build/Linux-Clang-Release_x86_64/tools/mxl-gst/mxl-gst-videosink ../portable-mxl-reader/
   cp ./dmf-mxl/build/Linux-Clang-Release_x86_64/lib/tests/data/*.json ../portable-mxl-reader/
   tar czf ../portable-mxl-reader.tar.gz --directory=../portable-mxl-reader/ .
   cp ../portable-mxl-reader.tar.gz ./docker/exercise-3/data/
```

## Step 5 Test with Exercises

After building the Docker images, follow the exercises in the repository to test and explore MXL functionality:

```bash
# Navigate to the Exercises directory to find detailed instructions
cd Exercises
```

The repository contains three exercises:

- Exercise1.md - Introduction to MXL basics
- Exercise2.md - Working with MXL flows
- Exercise3.md - Advanced MXL features

Each exercise contains step-by-step instructions to test different aspects of the MXL project using the Docker images you've built.

```bash
# Follow each exercise in order for the best learning experience
cat Exercise1.md
```

## Verifying Multi-Architecture Support

To check that your images support multiple architectures:

```bash
# View image manifest tree structure
docker image ls --tree
```

## Troubleshooting

If you encounter build issues:

1. Ensure dmf-mxl directory contains all necessary build artifacts
2. Check logs for any compilation errors
3. Verify Docker BuildX is configured correctly with `docker buildx ls`

## Notes

- Build artifacts are stored in `dmf-mxl/build/` directory
- Final images will be tagged as `mxl-writer:latest` and `mxl-reader:latest`

## Optional: Cleaning Up Build Artifacts

After you've built the Docker images, you can clean up the build artifacts to save disk space:

```bash
# Remove build Artifacts
rm -rf dmf-mxl/build
rm -rf dmf-mxl/install_*
rm -rf dmf-mxl/vcpkg_cache
rm -rf ~/portable-mxl-reader
rm ~/portable-mxl-reader.tar.gz
```

These commands will free up a significant amount of disk space, but you'll need to rebuild from scratch if you want to make changes later. You can also use the Git exclusion file to keep these directories ignored:
