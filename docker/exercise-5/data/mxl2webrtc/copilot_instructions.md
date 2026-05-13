# GitHub Copilot Agent Instructions: NMOS-Controlled GStreamer MXL to WebRTC application

## Project Overview
Your task is to build a Dockerized media application that functions as a MXL input, **video and audio**, to a WebRTC output gateway. The application uses strictly an **Ubuntu 24.04** base image to support custom downstream plugins. It leverages GStreamer for media functionalities, FastAPI for the backend API, React + Vite for the web frontend, and is controllable via NMOS. 

This is the sixth application of many. The final goal is illustrated in the mermaid diagram located at `./Exercises/Exercise5.md`. You can look at `./docker/exercise-5/data/file-player`, `./docker/exercise-5/data/test-generator`, `./docker/exercise-5/data/hls2mxl`, `./docker/exercise-5/data/input-selector` or `./docker/exercise-5/data/HTML5-keyer` for architecture examples. You must keep code style and patterns consistent between applications.

## System Architecture
The application consists of three main components running within a Docker environment:
1. **Frontend:** A web UI built with React and Vite.
2. **Backend / Media Engine:** A Python application using FastAPI to serve the frontend/API, and GStreamer (via Python bindings) to handle media routing. Media output is handled natively by `webrtcsink` using its embedded signaling server configured for local client consumption.
3. **NMOS Control:** The MXL input must be controllable via NMOS. 
   - **Reference 1:** A special NMOS node located at `./docker/exercise-5/nmos-cpp`.
   - **Reference 2:** A Python script example listening to the NMOS node to trigger CLI changes, located at `./docker/exercise-5/mxl-nmos-bridge`. Use this as a reference for bridging NMOS commands to the FastAPI/GStreamer backend.

## Environment & File Specifications
- **Base OS:** Must strictly use `ubuntu:24.04` for the final runtime stage.
- **Media Format:** The application must support **1920x1080p60** and **48 khz** audio.
- **GStreamer Pipeline:** The pipeline takes the live MXL input source and pushes it to the `webrtcsink` plugin. 
  - *MXL src documentation:* `./dmf-mxl/rust/gst-mxl-rs/readme.md`.
  - *WebRTC Configuration:* The pipeline must enable `webrtcsink`'s internal signaling server (`run-signalling-server=true`).
- **Flow Definition:** The resulting `flow_def.json` should have a description and a label identifying the MXL to WebRTC application. Keep flow UUIDs static regardless of state changes.

## Required API & UI Functionalities
The FastAPI backend and the React UI must expose and support the following controls:
1. **Status of the pipeline:** A status indicating if the GStreamer pipeline is actively running. 
2. **Status of the MXL connection:** A status of the MXL video and audio connection displaying the UUIDs of the connected flows.
3. **WebRTC Stream Access:** The UI must provide a straightforward mechanism or embedded player connected to the local WebRTC signaling endpoint to view the low-latency stream directly.

## Step-by-Step Implementation Guide for Copilot

**Step 1: Multi-Stage Docker Setup**
- Follow the multi-stage build pattern established in the repository (e.g., `hls2mxl` gateway). 
- **Stage 1 (Frontend Builder):** Use `node:18-bullseye-slim` to install dependencies via `npm install --legacy-peer-deps` and compile the React+Vite UI (`npm run build`).
- **Stage 2 (NMOS Binaries):** Extract required NMOS node components from `mxl-nmos-cpp:latest`.
- **Stage 3 (Runtime on Ubuntu 24.04):** - Base image: strictly `ubuntu:24.04`. Do not install Node.js or npm in this runtime stage.
  - Install native runtime packages via `apt-get`: `python3`, `python3-pip`, `python3-gi`, `python3-gi-cairo`, `gir1.2-gstreamer-1.0`, `gir1.2-gst-plugins-base-1.0`, `gstreamer1.0-tools`, `gstreamer1.0-plugins-base`, `gstreamer1.0-plugins-good`, `gstreamer1.0-plugins-bad`, `gstreamer1.0-plugins-ugly`, `gstreamer1.0-nice` (crucial for WebRTC ICE traversal), and `curl`.
  - Install Python backend dependencies globally using the break-system-packages flag to bypass PEP 668:
    ```dockerfile
    RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt
    ```
  - Ensure the `webrtcsink` plugin (`libgstrswebrtc.so`) is correctly located or accessible within `GST_PLUGIN_PATH` alongside custom upstream plugins (`libgstmxl.so` / `libgstcef.so`).
  - Set `LD_LIBRARY_PATH` to `/opt/mxl/lib` and configure the shared MXL dynamic library symlinks exactly matching reference patterns:
    ```dockerfile
    RUN cd /opt/mxl/lib \
     && ldconfig /opt/mxl/lib /usr/lib/x86_64-linux-gnu/gstreamer-1.0 \
     && mkdir -p /workspace/mxl/build/Linux-Clang-Release/lib \
     && ln -sf /opt/mxl/lib/libmxl.so /workspace/mxl/build/Linux-Clang-Release/lib/libmxl.so
    ```
  - Copy backend execution logic and the compiled UI static files (`/dist`) from Stage 1 into the target runtime directories.
  - **Network Mapping:** Ensure `docker-compose.yml` configuration exposes application ports alongside the embedded WebRTC signaling server (**TCP 8443**) and a pinned local UDP range for media transport (**UDP 50000-50020**). Expose ports `9550`, `9650`, `9750`, and `8443` in the Dockerfile.

**Step 2: FastAPI & GStreamer Backend**
- Create a FastAPI application running on **port 9650**. Register it to the node registry started by `./docker/exercise-5/docker-compose.yml`.
- Implement a GStreamer wrapper class using `gi.repository.Gst`.
- Initialize `webrtcsink` with the property `run-signalling-server=true`. Configure the signaller to accept connections from the host interface.
- **Bridge Network Traversal:** To allow the host PC browser to consume UDP media packets out of the container's isolated bridge network, attach a listener to `webrtcsink`'s `consumer-added` signal. Intercept the underlying `webrtcbin` element and restrict its ICE agent to the mapped port range:
    ```python
    def on_consumer_added(sink, consumer_id, webrtcbin):
        agent = webrtcbin.get_property("ice-agent")
        if agent:
            agent.set_property("min-rtp-port", 50000)
            agent.set_property("max-rtp-port", 50020)
    ```
- Implement the REST endpoint `/status` returning pipeline status and MXL input connections. Serve the copied `/dist` static frontend files cleanly via FastAPI routes.

**Step 3: NMOS Bridge Integration**
- Analyze the code in `./docker/exercise-5/mxl-nmos-bridge` and `./docker/exercise-5/nmos-cpp`.
- Implement a background task/process listening to the local NMOS node. Map incoming NMOS commands to dynamically route or update the active MXL input flow subscriptions, or toggle the pipeline playback state accordingly.
- NMOS must be exposed on **port 9550** for this application.
- The application's sender must be active by default upon startup.

**Step 4: React + Vite Frontend**
- Initialize a React + Vite project inside the frontend directory.
- Build a clean dashboard visualizing backend telemetry fetched from port 9650.
- Integrate a WebRTC video consumer element utilizing standard browser WebRTC APIs or a dedicated client library to negotiate streams directly against `ws://localhost:8443`.
- Configure Vite to target **port 9750** during local development previews. Ensure production builds route cleanly relative to the FastAPI static hosting paths.

Please write the necessary Dockerfiles, Python backend scripts, React components, and integration code following these guidelines at `./docker/exercise-5/data/mxl2webrtc`.