# GitHub Copilot Agent Instructions: NMOS-Controlled GStreamer File Player

## Project Overview
Your task is to build a Dockerized media application that functions as a video file player. The application uses GStreamer for media playback, FastAPI for the backend API, React + Vite for the web frontend, and is controllable via NMOS. This will be the first application of many. The final goal is illustrated in the mermaid diagram located here `./Exercises/Exercise5.md

## System Architecture
The application consists of three main components running within a Docker environment:
1. **Frontend:** A web UI built with React and Vite.
2. **Backend / Media Engine:** A Python application using FastAPI to serve the frontend and expose an API, and GStreamer (via Python bindings) to handle the actual media playback.
3. **NMOS Control:** The player must be controllable via NMOS. 
   - **Reference 1:** A special NMOS node located at `./docker/exercise-5/nmos-cpp`.
   - **Reference 2:** A Python script example that listens to the NMOS node to trigger CLI changes, located at `./docker/exercise-5/mxl-nmos-bridge`. Use this as a reference for bridging NMOS commands to the FastAPI/GStreamer backend.

## Environment & File Specifications
- **Docker Mount:** Media files will be mounted inside the Docker container at `/home/file`.
- **Media Format:** The player must support **1920x1080p60** video. 
  - *Recommendation:* `.ts` (MPEG-TS) is an excellent choice for broadcast and NMOS environments. However, ensure the GStreamer pipeline can also gracefully handle `.mp4` (H.264/H.265) if needed for easier broad compatibility. There is a sample file at `./docker/exercise-5/data/file-player/Clips`
- **GStreamer Pipeline:** The pipeline needs to be able to dynamically load a file from `/home/file/`, decode it, and output it via the purposed build mxl sink that is already compiled in the project and the instruction on usage can be found at `./dmf-mxl/rust/gst-mxl-rs/readme.md`. The resulting flow_def.json should have a description and a label that identify the clip-player application.

## Required API & UI Functionalities
The FastAPI backend and the React UI must expose and support the following transport controls:
1. **Load File:** Select and load a file from `/home/file`. If a file is already loaded, seamlessly replace it. After a file as been loaded it enter in Cued state
2. **Cue:** Seek to the absolute beginning of the loaded file (ready to play). The sink to mxl should display the first frame of the file
3. **Play:** Start playback of the loaded file.
4. **Pause:** Pause the current playback.
5. **Stop:** Stop playback and release or reset the pipeline stream.

## Step-by-Step Implementation Guide for Copilot

**Step 1: Docker Setup**
- Use the docker-compose.yml located at `./docker/exercise-5/` as a baseline. The Nmos registry, controler and dummy node is already setup as well as the domain volume for MXL usage.
- Ensure the base image installs Python 3, Node.js (for building the React app), GStreamer 1.0 (and all required plugins: `gstreamer1.0-tools`, `gstreamer1.0-plugins-base`, `good`, `bad`, `ugly`, and `libgirepository1.0-dev` for Python bindings).
- Set up the volume mount for `/home/file`.
- Copy the required library for MXL to work with GStreamer, you can have a look at GST_PLUGIN_PATH and LD_LIBRARY_PATH env variable to see where they are located. You are currently working in /home/mxl-hands-on.

**Step 2: FastAPI & GStreamer Backend**
- Create a FastAPI application on port 9600 and need to register to the registry started by `./docker/exercise-5/docker-compose.yml`.
- Implement a GStreamer wrapper class using `gi.repository.Gst`.
- Implement endpoints: `/load`, `/cue`, `/play`, `/pause`, `/stop`.
- Ensure the GStreamer pipeline dynamically handles the `uri` property of a `playbin` or a custom pipeline tailored for `.ts` files.

**Step 3: NMOS Bridge Integration**
- Analyze the code in `./docker/exercise-5/mxl-nmos-bridge` and `./docker/exercise-5/nmos-cpp`.
- Implement a background task or separate process in the Docker container that listens to the NMOS node.
- Map the incoming NMOS transport commands to the internal FastAPI endpoints or directly to the GStreamer wrapper.
- Nmos should be exposed on port 9500 for this application.

**Step 4: React + Vite Frontend**
- Initialize a React + Vite project.
- Build a clean, user-friendly control panel with buttons for Load (with a dropdown or input for filenames), Cue, Play, Pause, and Stop.
- Wire the UI to communicate with the FastAPI backend.
- Serve the built React static files via FastAPI, or run them on a separate port depending on the Docker setup.
- the front end should be exposed on port 9700

Please write the necessary Dockerfiles, Python backend scripts, React components, and integration code following these guidelines at the following location `./docker/exercise-5/data/file-player`.
