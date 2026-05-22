# GitHub Copilot Agent Instructions: NMOS-Controlled GStreamer HLS to MXL application

## Project Overview
Your task is to build a Dockerized media application that functions as a HSL to MXL gateway. The application uses GStreamer for media functionalities, FastAPI for the backend API, React + Vite for the web frontend, and is controllable via NMOS. This is the third application of many. The final goal is illustrated in the mermaid diagram located here `./Exercises/Exercise5.md`. You can look at the `./docker/exercise-5/data/file-player` or `./docker/exercise-5/data/test-generator` for an example of an application. You need to keep code consistent between application

## System Architecture
The application consists of three main components running within a Docker environment:
1. **Frontend:** A web UI built with React and Vite.
2. **Backend / Media Engine:** A Python application using FastAPI to serve the frontend and expose an API, and GStreamer (via Python bindings) to handle the actual media functionalities.
3. **NMOS Control:** The player must be controllable via NMOS. 
   - **Reference 1:** A special NMOS node located at `./docker/exercise-5/nmos-cpp`.
   - **Reference 2:** A Python script example that listens to the NMOS node to trigger CLI changes, located at `./docker/exercise-5/mxl-nmos-bridge`. Use this as a reference for bridging NMOS commands to the FastAPI/GStreamer backend.

## Environment & File Specifications
- **Media Format:** The application must support **1920x1080p60** video and **2 and 6 channels 48khz 24 bits** audio.
- **GStreamer Pipeline:** The pipeline needs to be able to dynamically change video and audio when a new HLS link is provided, and output it via the purposed build mxl sink that is already compiled in the project and the instruction on usage can be found at `./dmf-mxl/rust/gst-mxl-rs/readme.md`. The resulting flow_def.json should have a description and a label that identify the HLS2MXL application.

## Required API & UI Functionalities
The FastAPI backend and the React UI must expose and support the following transport controls:
1. **HLS link:** A text box to enter the HLS link to connect to.
2. **APPLY:** An apply button to subscribe to the provided HLS link.

## Step-by-Step Implementation Guide for Copilot

**Step 1: Docker Setup**
- Use the docker-compose.yml located at `./docker/exercise-5/` as a baseline. The Nmos registry, controler and dummy node is already setup as well as the domain volume for MXL usage.
- Ensure the base image installs Python 3, Node.js (for building the React app), GStreamer 1.0 (and all required plugins: `gstreamer1.0-tools`, `gstreamer1.0-plugins-base`, `good`, `bad`, `ugly`, and `libgirepository1.0-dev` for Python bindings).
- Copy the required library for MXL to work with GStreamer, you can have a look at GST_PLUGIN_PATH and LD_LIBRARY_PATH env variable to see where they are located. You are currently working in /home/mxl-hands-on.
- Create the symlink libgstmxl.so dlopen()s libmxl.so from its compile-time build path; that is what we did to satisfy it in the file-player application:
 && mkdir -p /workspace/mxl/build/Linux-Clang-Release/lib \
 && ln -sf /opt/mxl/lib/libmxl.so /workspace/mxl/build/Linux-Clang-Release/lib/libmxl.so

**Step 2: FastAPI & GStreamer Backend**
- Create a FastAPI application on port 9620 and need to register to the registry started by `./docker/exercise-5/docker-compose.yml`.
- Implement a GStreamer wrapper class using `gi.repository.Gst`.
- Implement endpoints: `/hls-link`, `/apply`.

**Step 3: NMOS Bridge Integration**
- Analyze the code in `./docker/exercise-5/mxl-nmos-bridge` and `./docker/exercise-5/nmos-cpp`.
- Implement a background task or separate process in the Docker container that listens to the NMOS node.
- Map the incoming NMOS transport commands to the internal FastAPI endpoints or directly to the GStreamer wrapper.
- Nmos should be exposed on port 9520 for this application.
- The application should have its sender active by default when it start.
- The application need to keep his flows uuid the same no matter what pattern are actually playing.

**Step 4: React + Vite Frontend**
- Initialize a React + Vite project.
- Build a clean, user-friendly control panel with a dropdown menu to select the video pattern, a dropdown menu to select the audio patter, a checkbox for time code on/off and a text box for the user to enter the ident.
- Wire the UI to communicate with the FastAPI backend.
- Serve the built React static files via FastAPI, or run them on a separate port depending on the Docker setup.
- the front end should be exposed on port 9720

Please write the necessary Dockerfiles, Python backend scripts, React components, and integration code following these guidelines at the following location `./docker/exercise-5/data/hls2mxl`.