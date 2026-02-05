# How to build MXL Docker images

This guide explains how to build multi-architecture Docker images for the MXL project using the updated build scripts.

## Prerequisites

- Docker with BuildX support

## Step 0: Checkout dmf-mxl/mxl git repo

mxl lib is integrated as submodule, i.e. an external repo that needs to be initialized:

```bash
git submodule update --init
```

This means hands-on repo is tied to a specific version of the mxl library.
It is possible to [upgrade the mlx lib version](./how_to_build.md#-Upgrade-mxl-lib).

## Step 1: Build the MXL Project

First, build the project, if you build on linux under x86-amd64 use the following

```bash
# Run from the repository root for amd64 build on an amd64 linux based system.
./build_linux.sh
```

If you are building on Mac os on an amr64 use the following

```bash
# Run this on a arm based mac for arm based build
# This need Homebrew, doxygen, ccache and Gstreamer runtime installer and development installer found here:
# https://gstreamer.freedesktop.org/download/#macos
build_darwin.sh
```

These scripts will:

- Build the project in the ./dmf-mxl/ directory
- Place build artifacts in ./dmf-mxl/build/

## Step 2: Creating `portable mxl app`

```sh
   # Creating amd64 portable app
   # Must be run on an x86_64-amd64 machine
   ./create_portables_x86_64.sh

   # Creating arm based portable app
   # Must be run on an arm based mac machine
   ./create_portable_arm.sh
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

Tag nomenclature is:
```<service>:<mxl_recent_ta>-<num_of_commit_since_tag>-<actual_commit_hash>-<compiler>```
Exemple:
```mxl-reader:v1.0.0-rc1-24-g8d280db-linux-clang-release```

## Step 4: Upload to image repository

```sh
   echo <YOUR TOKEN> | docker login ghcr.io -u <YOUR_GITHUB_USERNAME> --password-stdin
   current_date=$(date +%Y-%m-%d)
   docker tag mxl-writer:latest ghcr.io/cbcrc/mxl-writer:latest
   docker tag mxl-writer:latest ghcr.io/cbcrc/mxl-writer:$current_date
   docker tag mxl-reader:latest ghcr.io/cbcrc/mxl-reader:latest
   docker tag mxl-reader:latest ghcr.io/cbcrc/mxl-reader:$current_date
   docker tag mxl-clip-player:latest ghcr.io/cbcrc/mxl-clip-player:latest
   docker tag mxl-clip-player:latest ghcr.io/cbcrc/mxl-clip-player:$current_date
   docker push ghcr.io/cbcrc/mxl-writer:latest
   docker push ghcr.io/cbcrc/mxl-writer:$current_date
   docker push ghcr.io/cbcrc/mxl-reader:latest
   docker push ghcr.io/cbcrc/mxl-reader:$current_date
   docker push ghcr.io/cbcrc/mxl-clip-player:latest
   docker push ghcr.io/cbcrc/mxl-clip-player:$current_date
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
