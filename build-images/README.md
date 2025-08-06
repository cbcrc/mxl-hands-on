# Multi-Architecture MXL Demo Builds

This directory contains updated scripts and configuration files for building true multi-architecture Docker images for MXL demonstrations. These files provide a streamlined way to create smaller, optimized Docker images that support multiple CPU architectures in a single image manifest.

## Files in this Directory

- **build-demo-images.sh**: Script to build multi-architecture Docker images with unified manifests
- **docker-compose.yaml**: Defines the services for the MXL demonstration
- **Dockerfile.writer.txt**: Container definition for the MXL writer service
- **Dockerfile.reader.txt**: Container definition for the MXL reader service

## Usage

1. First, build the MXL project for your target architectures using the main build_all.sh script:

   ```bash
   # Build for both architectures
   ../build_all.sh --arch x86_64 --arch arm64
   ```

2. Run this script to create multi-arch demonstration images:

   ```bash
   cd multi-arch-builds
   chmod +x build-demo-images.sh
   ./build-demo-images.sh
   ```

3. Verify multi-arch support:

   ```bash
   docker buildx imagetools inspect mxl-writer:latest
   # Or 
   docker image ls --tree # Shows all architectures in a single image
   ```

4. Run the demonstration:

   ```bash
   docker-compose up
   ```

## Features

- **True multi-architecture images**: Single image manifest supports multiple platforms (x86_64/arm64)
- **Multiple compiler support**: Build with both GCC and Clang
- **Optimized images**: Smaller Docker images for demonstrations
- **Dependency verification**: Checks that required build artifacts exist
- **Docker BuildX integration**: Uses the latest Docker multi-architecture features

## Uploading to Docker Hub

To share your multi-architecture images with others via Docker Hub, follow these steps:

1. Log in to Docker Hub:

   ```bash
   docker login
   ```

2. Tag your images with your Docker Hub username:

   ```bash
   # Replace 'yourusername' with your Docker Hub username
   docker tag mxl-writer:latest yourusername/mxl-writer:latest
   docker tag mxl-reader:latest yourusername/mxl-reader:latest
   
   # You can also tag specific compiler versions
   docker tag mxl-writer:linux-gcc-release yourusername/mxl-writer:linux-gcc-release
   docker tag mxl-reader:linux-gcc-release yourusername/mxl-reader:linux-gcc-release
   ```

3. Push the images to Docker Hub:

   ```bash
   docker push yourusername/mxl-writer:latest
   docker push yourusername/mxl-reader:latest
   ```

4. For a more efficient workflow, you can modify the build-demo-images.sh script to directly push to Docker Hub by adding these lines at the end of the build_multiarch_image function:

   ```bash
   # Add this to the build-demo-images.sh script if you want to push to Docker Hub
   if [ -n "$DOCKER_HUB_USERNAME" ]; then
     docker tag "mxl-$service:$tag" "$DOCKER_HUB_USERNAME/mxl-$service:$tag"
     echo "Pushing $DOCKER_HUB_USERNAME/mxl-$service:$tag to Docker Hub..."
     docker push "$DOCKER_HUB_USERNAME/mxl-$service:$tag"
   fi
   ```

   Then run the script with:

   ```bash
   DOCKER_HUB_USERNAME=yourusername ./build-demo-images.sh
   ```

5. Verify your multi-architecture image on Docker Hub:
   
   After pushing, anyone can pull and use your images on any supported architecture (x86_64/arm64) with:

   ```bash
   docker pull yourusername/mxl-writer:latest
   ```

   Docker will automatically select the appropriate architecture version.

## Building the looping testsrc

Commands at a glance

```sh
# Build the container
docker image build -f Dockerfile.looping.txt -t mxl-looping:latest .. 
# run interacive shell bypassing the CMD instruction 
docker run --entrypoint bash -it -v ~/domain:/domain mxl-looping
# run the app
/app/mxl-gst-looping-filesrc -d /domain -i /app/city.ts
# Debug
gst-inspect-1.0
```

## Comparison with Original Examples

These files are enhanced versions of those in the `/examples` directory with the following improvements:

1. **Architecture compatibility**: Fixed architecture naming consistency
2. **Error handling**: Added verification of build artifacts
3. **Optimized Dockerfiles**: Reduced image size through better layering
4. **Proper lowercase tags**: Fixed Docker tag naming to comply with requirements
