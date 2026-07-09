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

All images are published with `docker buildx build --push` instead of `docker tag` + `docker push`: this generates an **SPDX SBOM and provenance attestation** for each image and attaches them to the pushed image (see `THIRD-PARTY-NOTICES.md` for why we publish SBOMs). The builds reuse the cache from Steps 2 and 3, so this is mostly a re-export + push. Attestations only attach when buildx pushes directly to the registry — images loaded locally and then `docker push`ed lose them, which is why the local build scripts don't bother with `--sbom`.

> ⚠️ Use `docker buildx build` here, not `docker compose build --sbom=true` — on current Docker versions the compose flag silently produces no SBOM. Attestations also require the **containerd image store** (Docker Desktop: Settings → General → "Use containerd for pulling and storing images").

```sh
   docker login ghcr.io -u <YOUR_GITHUB_USERNAME>
   # enter your personnal Github token (permission scope: Workflows, Write+Delete Package)
```

First the tools/demo images (writer, reader, clip-player). This reuses the `mxl-builder` cache from Step 3, and pushes both `:$TAG_TOOLS` and `:latest` with the SBOM attached:

```sh
   # Run from the repository root.
   for svc in writer reader clip-player; do
     docker buildx build --builder mxl-builder --platform linux/amd64 \
       --sbom=true --provenance=mode=max \
       --build-arg FULL_BUILD_PATH=/build/Linux-Clang-Release \
       -f "build-images/Dockerfile.${svc}.txt" \
       -t "ghcr.io/cbcrc/mxl-${svc}:${TAG_TOOLS}" -t "ghcr.io/cbcrc/mxl-${svc}:latest" \
       --push dmf-mxl
   done
```

Then the gst-apps images, same pattern:

```sh
   # Run from the repository root. Pushes both :$TAG_APP and :latest with SBOM attached.
   for pair in mxl-info-gui:mxl-info-gui test-generator:test-generator \
               file-player:file-player mxl2webrtc:mxl2webrtc \
               input-selector:input-selector html5-keyer:HTML5-keyer \
               hls2mxl:hls2mxl webrtc2mxl:webrtc2mxl; do
     image=${pair%%:*}; dir=${pair##*:}
     docker buildx build --platform linux/amd64 --sbom=true --provenance=mode=max \
       -f "gst-apps/${dir}/Dockerfile" \
       -t "ghcr.io/cbcrc/${image}:${TAG_APP}" -t "ghcr.io/cbcrc/${image}:latest" \
       --push .
   done
```

To verify the SBOM landed on GHCR (prints the SPDX document):

```sh
   docker buildx imagetools inspect ghcr.io/cbcrc/file-player:latest --format '{{ json .SBOM }}' | head -c 500
```

These versions are the **latest stable** version of mxl that we deploy by default: both buildx loops above already pushed the moving `latest` tag alongside the versioned tags, so no separate `docker tag` + `docker push` step is needed.

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
