# GitHub Copilot Agent Instructions: NMOS-Controlled GStreamer HTML5 keyer

## Project Overview
Your task is to build a Dockerized media application that functions as an HTML5 keyer keying graphics over an MXL input and outputting the composited result to an MXL output. The application uses strictly an **Ubuntu 24.04** base image to support custom downstream plugins. It leverages GStreamer for media functionalities, FastAPI for the backend API, React + Vite for the web frontend, and is controllable via NMOS. 

This is the fifth application of many. The final goal is illustrated in the mermaid diagram located at `./Exercises/Exercise5.md`. You can look at `./docker/exercise-5/data/file-player`, `./docker/exercise-5/data/test-generator`, `./docker/exercise-5/data/hls2mxl` or `./docker/exercise-5/data/input-selector` for architecture examples. You must keep code style and patterns consistent between applications.

## System Architecture
The application consists of three main components running within a Docker environment:
1. **Frontend:** A web UI built with React and Vite.
2. **Backend / Media Engine:** A Python application using FastAPI to serve the frontend/API, and GStreamer (via Python bindings) to handle media compositing.
3. **NMOS Control:** The player must be controllable via NMOS. 
   - **Reference 1:** A special NMOS node located at `./docker/exercise-5/nmos-cpp`.
   - **Reference 2:** A Python script example listening to the NMOS node to trigger CLI changes, located at `./docker/exercise-5/mxl-nmos-bridge`. Use this as a reference for bridging NMOS commands to the FastAPI/GStreamer backend.

## Environment & File Specifications
- **Base OS:** Must strictly use `ubuntu:24.04`.
- **Media Format:** The application must support **1920x1080p60** video compositing.
- **HTML5 Keying Source (`gst-cef`):** Because `wpesrc` is unavailable natively on Ubuntu 24.04, the application must use a multi-stage Docker build to compile the Chromium Embedded Framework (CEF) GStreamer plugin (`gst-cef`). The source element in the pipeline will be `cefsrc url="http://localhost:5660/renderer/"`.
- **GStreamer Pipeline:** The pipeline takes the live MXL input source and the `cefsrc` graphics stream, routes both into a hardware-accelerated mixer (e.g., `glvideomixer`), and pushes the final composited frame to the custom MXL sink. 
  - *MXL Sink documentation:* `./dmf-mxl/rust/gst-mxl-rs/readme.md`.
- **Flow Definition:** The resulting `flow_def.json` should have a description and a label identifying the HTML5-keyer application. Keep flow UUIDs static regardless of state changes.

## Required API & UI Functionalities
The FastAPI backend and the React UI must expose and support the following transport controls:
1. **Key ON/OFF:** A single button/toggle to turn the keyer overlay ON or OFF. 
   - It should tally green if the current state is ON.
   - It must start OFF by default.
   - **Implementation Rule:** Do *not* stop or rebuild the GStreamer pipeline to toggle the key. Instead, dynamically adjust the `alpha` property of the `cefsrc` input pad on the `glvideomixer` element (`0.0` for OFF, `1.0` for ON) to ensure seamless 1080p60 playback without dropped frames.

## Step-by-Step Implementation Guide for Copilot

**Step 1: Docker Setup (Multi-Stage Ubuntu 24.04 Build)**
- Use the `docker-compose.yml` located at `./docker/exercise-5/` as a baseline. Ensure shared volumes for MXL usage are retained.
- **Stage 1 (Builder):** - Base image: `ubuntu:24.04`.
  - Install build dependencies: `git`, `build-essential`, `cmake`, `meson`, `ninja-build`, `pkg-config`, `libgstreamer1.0-dev`, `libgstreamer-plugins-base1.0-dev`, `libglib2.0-dev`, `wget`.
  - Download the appropriate Linux 64-bit minimal CEF binary distribution from Spotify's automated CEF builds. Extract it to `/opt/cef`.
  - Clone the `gst-cef` repository (e.g., `https://github.com/centricular/gst-cef.git`). Set `export CEF_ROOT=/opt/cef` and compile the plugin using Meson/Ninja.
- **Stage 2 (Runtime):**
  - Base image: `ubuntu:24.04`.
  - Install runtime dependencies: Python 3, Node.js, `gstreamer1.0-tools`, `gstreamer1.0-plugins-base`, `gstreamer1.0-plugins-good`, `gstreamer1.0-plugins-bad`, `gstreamer1.0-plugins-ugly`, `python3-gi`, `python3-gst-1.0`, `libgirepository-1.0-1`.
  - Copy the built `libgstcef.so` plugin and required CEF dynamic libraries (`libcef.so`, etc.) from the Builder stage to the runtime container.
  - Set `GST_PLUGIN_PATH` to include the directory containing `libgstcef.so`.
  - Set `LD_LIBRARY_PATH` to include the path to `libcef.so` and the MXL libraries.
  - Create the required MXL symlink to satisfy `dlopen()` paths (consistent with the file-player app):
    ```dockerfile
    RUN mkdir -p /workspace/mxl/build/Linux-Clang-Release/lib \
     && ln -sf /opt/mxl/lib/libmxl.so /workspace/mxl/build/Linux-Clang-Release/lib/libmxl.so
    ```

**Step 2: FastAPI & GStreamer Backend**
- Create a FastAPI application running on **port 9640**. Register it to the node registry started by `./docker/exercise-5/docker-compose.yml`.
- Implement a GStreamer wrapper class using `gi.repository.Gst`. 
- Ensure the pipeline sets `cefsrc` output caps to RGBA to preserve the graphics alpha channel before feeding it into `glvideomixer`.
- Implement the REST endpoint: `/keyer-control` accepting JSON payloads to toggle the key state.

**Step 3: NMOS Bridge Integration**
- Analyze the code in `./docker/exercise-5/mxl-nmos-bridge` and `./docker/exercise-5/nmos-cpp`.
- Implement a background task/process in the container listening to the NMOS node. Map incoming NMOS commands directly to the GStreamer wrapper's pad alpha toggle.
- NMOS must be exposed on **port 9540** for this application.
- The application's sender must be active by default upon startup.

**Step 4: React + Vite Frontend**
- Initialize a React + Vite project.
- Build a clean, user-friendly control panel featuring the **Key ON/OFF** toggle button with accurate green tally indication based on state feedback from the backend.
- Wire the UI to communicate cleanly with the FastAPI backend on port 9640.
- Configure Vite to expose the frontend UI on **port 9740**. Serve static files via FastAPI or run the Vite preview server directly based on baseline standard practices in the repository.

Please write the necessary Dockerfiles, Python backend scripts, React components, and integration code following these guidelines at `./docker/exercise-5/data/HTML5-keyer`.