# How to build MXL Docker images

This guide explains how to build multi-architecture Docker images for the MXL project using the updated build scripts.

## Prerequisites

- Docker with BuildX support
- GitHub CLI
```sh
   sudo apt install gh
```

## Step 0: Checkout dmf-mxl/mxl git repo

mxl lib is integrated as submodule, i.e. an external repo that needs to be initialized:

```bash
git submodule update --init
```
Update the submodule to latest commit of main branch.
```sh
   cd ~/mxl-hands-on/dmf-mxl
   git checkout main
   git pull origin main
   cd ..
```

Setting up the TAG variable according to the version you are building:
```sh
   MAIN_HASH=$(git rev-parse --short HEAD) && \
   SUB_HASH=$(git -C dmf-mxl rev-parse --short HEAD) && \
   TAG_TOOLS="mxl-${SUB_HASH}" && \
   TAG_APP="hands-on-${MAIN_HASH}-mxl-${SUB_HASH}"
```

This means hands-on repo is tied to a specific version of the mxl library.
It is possible to [upgrade the mlx lib version](./how_to_build.md#-Upgrade-mxl-lib).

## Step 1: Build the MXL Project


First, build the project, if you build on linux under x86-amd64 use the following

```bash
# Run from the repository root for amd64 build on an amd64 linux based system.
./build_linux.sh
```

This scripts will:

- Build the project in the ./dmf-mxl/ directory
- Place build artifacts in ./dmf-mxl/build/
- Build the rust part of the project in the ./dmf-mxl/rust directory
- Place the rust build artifacts in ./dmf-mxl/rust/target

## Step 2: Build the GStreamer based application images.

```sh
   # Navigate to the gst-apps directory first
   cd gst-apps
   docker compose build
```

## Step 3: Build Docker Images. ONLY WORK UNDER LINUX

After the project is built, create the Docker images:

```bash
# Navigate to the build-images directory first
cd build-images
./build-demo-images.sh
```

This will:

- Create multi-architecture Docker images using the build artifacts
- Generate both reader and writer images for each compiler
- Tag the images appropriately

the nomenclature of the generated tag is:
```<service>:<actual_commit_hash>-<compiler>```
Exemple:
```mxl-reader:mxl-8d280db-linux-Clang-release```

## Step 4: Upload to image repository

Let's push the freshly built images with the $TAG_TOOLS tag `v1.0.0-rc2..` and push to Github Container Registry.

```sh
   docker login ghcr.io -u <YOUR_GITHUB_USERNAME>
   # enter your personnal Github token (permission scope: Workflows, Write+Delete Package   
   docker tag mxl-writer:"${TAG_TOOLS}-linux-clang-release" ghcr.io/cbcrc/mxl-writer:$TAG_TOOLS
   docker tag mxl-reader:"${TAG_TOOLS}-linux-clang-release" ghcr.io/cbcrc/mxl-reader:$TAG_TOOLS
   docker tag mxl-clip-player:"${TAG_TOOLS}-linux-clang-release" ghcr.io/cbcrc/mxl-clip-player:$TAG_TOOLS
   docker tag mxl-info-gui:latest ghcr.io/cbcrc/mxl-info-gui:$TAG_APP
   docker tag test-generator:latest ghcr.io/cbcrc/test-generator:$TAG_APP
   docker tag file-player:latest ghcr.io/cbcrc/file-player:$TAG_APP
   docker tag mxl2webrtc:latest ghcr.io/cbcrc/mxl2webrtc:$TAG_APP
   docker tag input-selector:latest ghcr.io/cbcrc/input-selector:$TAG_APP
   docker tag html5-keyer:latest ghcr.io/cbcrc/html5-keyer:$TAG_APP
   docker tag hls2mxl:latest ghcr.io/cbcrc/hls2mxl:$TAG_APP
   docker push ghcr.io/cbcrc/mxl-writer:$TAG_TOOLS
   docker push ghcr.io/cbcrc/mxl-reader:$TAG_TOOLS
   docker push ghcr.io/cbcrc/mxl-clip-player:$TAG_TOOLS
   docker push ghcr.io/cbcrc/mxl-info-gui:$TAG_APP
   docker push ghcr.io/cbcrc/test-generator:$TAG_APP
   docker push ghcr.io/cbcrc/file-player:$TAG_APP
   docker push ghcr.io/cbcrc/mxl2webrtc:$TAG_APP
   docker push ghcr.io/cbcrc/input-selector:$TAG_APP
   docker push ghcr.io/cbcrc/html5-keyer:$TAG_APP
   docker push ghcr.io/cbcrc/hls2mxl:$TAG_APP
```

Let's consider this versions as the **latest stable** version of mxl that we want to deploy by default.
Let's attach the moving tag `latest` and push.

```sh
   docker tag mxl-writer:"${TAG_TOOLS}-linux-clang-release" ghcr.io/cbcrc/mxl-writer:latest
   docker tag mxl-reader:"${TAG_TOOLS}-linux-clang-release" ghcr.io/cbcrc/mxl-reader:latest
   docker tag mxl-clip-player:"${TAG_TOOLS}-linux-clang-release" ghcr.io/cbcrc/mxl-clip-player:latest
   docker tag mxl-info-gui:latest ghcr.io/cbcrc/mxl-info-gui:latest
   docker tag test-generator:latest ghcr.io/cbcrc/test-generator:latest
   docker tag file-player:latest ghcr.io/cbcrc/file-player:latest
   docker tag mxl2webrtc:latest ghcr.io/cbcrc/mxl2webrtc:latest
   docker tag input-selector:latest ghcr.io/cbcrc/input-selector:latest
   docker tag html5-keyer:latest ghcr.io/cbcrc/html5-keyer:latest
   docker tag hls2mxl:latest ghcr.io/cbcrc/hls2mxl:latest
   docker push ghcr.io/cbcrc/mxl-writer:latest
   docker push ghcr.io/cbcrc/mxl-reader:latest
   docker push ghcr.io/cbcrc/mxl-clip-player:latest
   docker push ghcr.io/cbcrc/mxl-info-gui:latest
   docker push ghcr.io/cbcrc/test-generator:latest
   docker push ghcr.io/cbcrc/file-player:latest
   docker push ghcr.io/cbcrc/mxl2webrtc:latest
   docker push ghcr.io/cbcrc/input-selector:latest
   docker push ghcr.io/cbcrc/html5-keyer:latest
   docker push ghcr.io/cbcrc/hls2mxl:latest
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
- Exercise4.md - Portable MXL application
- Exercise5.md - Gstreamer based MXL application with complex workflow

Each exercise contains step-by-step instructions to test different aspects of the MXL project using the Docker images you've built.

```bash
# Follow each exercise in order for the best learning experience
cat Exercise1.md
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
rm -rf dmf-mxl/rust/.cargo-docker
rm -rf dmf-mxl/rust/target
```

These commands will free up a significant amount of disk space, but you'll need to rebuild from scratch if you want to make changes later. You can also use the Git exclusion file to keep these directories ignored:

## Upgrade mxl lib

It is possible to upgrade or checkout any version of mxl.

```bash
git submodule 
 28994489abb332af15a4a13466f89086540adb7a dmf-mxl <ref--commit-hash>
cd ./dmf-mxl
git pull # or checkout the targetted version of mxl
git describe  --tags
v1.0.0-rc1-3-g2899448 # recent_tag - num_of_commit_since_tag - actual_commit_hash>
cd ..
git add mxl-dmf # commit this upgrade in the hands-on repo
git commit -m "Upgrade mxl to v1.0.0-rc1-3-g2899448"
```
