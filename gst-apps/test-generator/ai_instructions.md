# AI Agent Instructions: GStreamer Test Generator

## Project Overview
Build a Dockerized media application that functions as a video and audio test generator. The application uses GStreamer for media functionalities, FastAPI for the backend API, and React + Vite for the web frontend. The final goal is illustrated in the mermaid diagram located here `./Exercises/Exercise5.md`. The application is located at `./gst-apps/test-generator`. NMOS has been removed entirely — there is no NMOS node, no nmos_bridge, and no NMOS-related dependencies.

## System Architecture
The application consists of two main components running within a single Docker container:
1. **Frontend:** A web UI built with React and Vite, served as static files via `python3 -m http.server` on port 9700.
2. **Backend / Media Engine:** A Python application using FastAPI (port 9600) to expose the API, and GStreamer (via `gi.repository.Gst` Python bindings) to handle the actual media pipeline.

## Environment & File Specifications
- **Media Format:** The test generator must offer the following video raster resolutions: **1280x720**, **1920x1080**, and **3840x2160**, and the following frame rates: **24 Hz**, **25 Hz**, **29.97 Hz**, **30 Hz**, **50 Hz**, **59.94 Hz**, and **60 Hz**. For audio it must offer a choice of **1 to 64 channels at 48 kHz 24-bit** (delivered as F32LE to mxlsink).
- **GStreamer Pipeline:** The pipeline is user-controlled (start/stop). It can dynamically change video and audio test patterns and outputs them via the purpose-built mxl sink (see `./dmf-mxl/rust/gst-mxl-rs/readme.md` for usage). The pipeline produces **one video flow** and **two independent audio flows** (Audio Flow 1 and Audio Flow 2), each sent through its own mxl sink instance.
- **mxlsink format requirements:** `mxlsink` only accepts specific formats. A `capsfilter` with `video/x-raw,format=v210` **must** be placed immediately before every mxl video sink, and a `capsfilter` with `audio/x-raw,format=F32LE,layout=interleaved` **must** be placed immediately before every mxl audio sink. Without these explicit capsfilters, auto-negotiation may land on an incompatible format and cause a hard failure at runtime.
- **MXL Flow Identity:** Every time the pipeline is started, a **new UUID must be generated** for each active flow (do not reuse UUIDs across pipeline restarts). Each mxl sink must be configured with its generated UUID. Once the pipeline is running and the mxl sink has written the flow to disk, the backend must poll until `{selected-domain-path}/{flow_uuid}.mxl-flow/flow_def.json` exists for each active flow, then patch that file replacing the `grouphint`, `description`, and `label` fields with the values provided by the user in the Setup section.

## Required API & UI Functionalities

The UI is divided into two distinct sections: **Setup** and **Operation**.

---

### Section 1 — Setup

This section is used to configure the MXL flows before starting the GStreamer pipeline. The pipeline does **not** start until the user clicks the **Start** button. All controls in this section are disabled while the pipeline is running.

1. **MXL Domain Selector:** Scan `/mxl-domain` recursively for `domain_def.json` files. Read the `id` field for the domain UUID and use the containing directory path as the domain path (passed to `mxlsink`'s `domain` property). Provide a dropdown to select the target MXL domain.
2. **Resolution Selector:** A dropdown to select the output video raster: `1280x720`, `1920x1080`, or `3840x2160`. Default: `1920x1080`.
3. **Frame Rate Selector:** A dropdown to select the output frame rate: `24`, `25`, `29.97`, `30`, `50`, `59.94`, or `60` fps. Default: `30`.
4. **Group Hint:** A text input shared across all three flows. Default value: `Test-Generator`.
5. **Flow Configuration Table:** Three rows — one for the Video flow, one for Audio Flow 1, and one for Audio Flow 2 — each with:
   - **Active checkbox** — tick to include the flow in the pipeline. All three are active by default.
   - **Channels** — numeric input (1–64) for audio flows only (Video row shows "—"). Default: `2`. Channel count is fixed for the lifetime of the pipeline; changing it requires a Stop + Start.
   - **Description** — text input unique to each flow. Defaults: `video-out-1`, `audio-out-1`, `audio-out-2`.
   - **Label** — text input unique to each flow. Defaults: `video-test-pattern`, `audio-test-pattern`, `audio-test-pattern`.
   - Description and Label are mandatory fields; the Start button is disabled until all active flows have non-empty values.
6. **Start / Stop button** — starts the GStreamer pipeline with the configured flow metadata when clicked. Changes to a **Stop** button once the pipeline is running. Only active flows are included in the pipeline.

---

### Section 2 — Operation

This section is enabled only once the pipeline is running (greyed-out and non-interactive otherwise). It is divided into a **Video** panel and two independent **Audio Flow** panels.

**Video panel:**
1. **Select Video Test Pattern:** A dropdown to select any available GStreamer video test pattern. Default: `100% bars`.
2. **Timecode overlay:** A checkbox to burn timecode onto the video. Default: **on**.
3. **Ident:** A text box for an identification string overlaid on the video test pattern, applied via an **Apply** button (or Enter key).

**Audio Flow 1 panel & Audio Flow 2 panel** (independent controls for each):
1. **Select Audio Test Pattern:** A dropdown to select any available GStreamer audio test pattern. Default: `1 kHz tone`.
2. **Audio level:** A fader with 0.5 dB increments and a numerical readout. Default: `−20 dBFS`.

> Channel count is configured in the **Setup** section before starting the pipeline and cannot be changed while it is running.

## Step-by-Step Implementation Guide

**Step 1: Docker Setup**
- Use the docker-compose.yml located at `./gst-apps/` as a baseline. The `mxl-domain` named volume is defined there and shared by all services.
- **MXL domain volume:** The volume uses a host-directory bind mount (not tmpfs) so that `domain_def.json` and any flow files persist independently of container lifecycle:
  ```yaml
  volumes:
    mxl-domain:
      driver: local
      driver_opts:
        type: none
        device: ${MXL_DOMAIN_DEVICE}
        o: "bind,nosuid,strictatime"
  ```
  `MXL_DOMAIN_DEVICE` must be set to an absolute path on the host that contains `domain_def.json` before running `docker compose up` (e.g. via a `.env` file next to `docker-compose.yml` or an exported shell variable). No init container is needed — the file is already present on the host.
- Add port mappings `9600:9600` and `9700:9700` for the test-generator service.
- The Dockerfile uses **two stages**: a `node:18-bullseye-slim` stage to build the React frontend, and an `ubuntu:24.04` runtime stage. There is no NMOS stage.
- The build context is the repository root (`..` relative to `./gst-apps/`). All `COPY` paths in the Dockerfile are therefore relative to the repository root (e.g. `COPY gst-apps/test-generator/backend/ /app/backend/`).
- The runtime stage installs: `python3`, `python3-pip`, `python3-gi`, `python3-gi-cairo`, `gir1.2-gstreamer-1.0`, `gir1.2-gst-plugins-base-1.0`, `gstreamer1.0-tools`, `gstreamer1.0-plugins-base`, `gstreamer1.0-plugins-good`, `gstreamer1.0-plugins-bad`, `gstreamer1.0-plugins-ugly`, `gstreamer1.0-libav`.
- Copy `libgstmxl.so` to `/usr/lib/x86_64-linux-gnu/gstreamer-1.0/` (the default GStreamer plugin path on Ubuntu 24.04 — no `GST_PLUGIN_PATH` env var is needed). Copy `libmxl.so.1.1` and `libmxl-common.so.1.1` to `/opt/mxl/lib/` and create the expected symlinks. Set `ENV LD_LIBRARY_PATH=/opt/mxl/lib`.
- Create the symlink that `libgstmxl.so` needs (it `dlopen()`s `libmxl.so` from its compile-time build path):
  ```
  && mkdir -p /workspace/mxl/build/Linux-Clang-Release/lib \
  && ln -sf /opt/mxl/lib/libmxl.so /workspace/mxl/build/Linux-Clang-Release/lib/libmxl.so
  ```

**Step 2: FastAPI & GStreamer Backend**
- Create a FastAPI application on port 9600. No NMOS bridge or startup event is needed.
- Implement a `GstGenerator` class using `gi.repository.Gst`. The pipeline is **not** started at init — only when `start(config)` is called explicitly.
- **Setup endpoints:**
  - `GET /domains` — scan `/mxl-domain` recursively for `domain_def.json` files; return `path` (containing directory), `id` (UUID from JSON `id` field), and `label` per domain.
  - `GET /patterns` — return lists of available video and audio test pattern names.
  - `GET /options` — return lists of available resolutions and frame rates.
  - `POST /pipeline/start` — accepts `domain` (path), `grouphint`, `resolution`, `framerate`, and per-flow config. `video` has `active`, `description`, `label`. `audio1` and `audio2` additionally have `channels` (int, 1–64, default 2). On each call: (1) set audio channel counts from config, (2) generate a fresh `uuid4` for every active flow, (3) build and start the GStreamer pipeline, (4) return once the pipeline reaches PLAYING state, (5) in a background thread, poll until `{domain-path}/{flow_uuid}.mxl-flow/flow_def.json` exists for each active flow, then patch `grouphint`, `description`, and `label`.
  - `POST /pipeline/stop` — stops and tears down the GStreamer pipeline.
  - `GET /pipeline/status` — returns the full runtime state: `running` (bool), `flow_uuids` (dict), current video settings (`pattern`, `timecode`, `ident`, `resolution`, `framerate`), and per-flow audio settings (`pattern`, `channels`, `level_db`).
- **Operation endpoints (only functional while pipeline is running):**
  - `POST /video/test-pattern` — set the video test pattern `{"pattern": "..."}`.
  - `POST /video/timecode` — enable/disable timecode overlay `{"enabled": true}`.
  - `POST /video/ident` — set the ident overlay text `{"text": "..."}`.
  - `POST /audio/flow1/test-pattern` — set audio test pattern for Flow 1.
  - `POST /audio/flow1/level` — set audio level (dBFS) for Flow 1.
  - `GET  /audio/flow1/level` — read current audio level for Flow 1.
  - `POST /audio/flow2/test-pattern` — set audio test pattern for Flow 2.
  - `POST /audio/flow2/level` — set audio level (dBFS) for Flow 2.
  - `GET  /audio/flow2/level` — read current audio level for Flow 2.
  - *(Channel count is not a runtime endpoint — it is passed in `POST /pipeline/start` and fixed for the pipeline lifetime.)*
- **GStreamer pipeline structure:**
  - Video: `videotestsrc → capsfilter(res+fps) → timeoverlay → textoverlay → videoconvert → capsfilter(v210) → queue → mxlsink`
  - Audio (per flow): `audiotestsrc → audioconvert → capsfilter(F32LE, N ch, 48 kHz, interleaved) → queue → mxlsink`
  - Video pattern, timecode, and ident are live-adjustable without rebuilding the pipeline. Audio pattern and level are also live. Channel count changes require a rebuild.

**Step 3: React + Vite Frontend**
- Initialize a React + Vite project.
- **Header branding:** At the very top of the page, display the CBC Radio-Canada logo (`gst-apps/logo/rgb_cbc-radio-canada-col-coul.png`) inline beside the "MXL Test Generator" h1 title. Copy the logo into `frontend/public/cbc-logo.png` so Vite serves it as a static asset; reference it in JSX as `<img src="/cbc-logo.png" />` with `height: 2.2rem`. The logo and title must share a flex row (`display: flex; align-items: center; gap: 1rem`).
- Structure the UI into two clearly labelled sections:
  - **Setup section** (always visible, disabled while running): MXL domain dropdown, resolution dropdown, frame rate dropdown, shared grouphint input, a three-row flow configuration table (Video / Audio Flow 1 / Audio Flow 2) each with an active checkbox, description, and label inputs, and a Start/Stop button. The Start button is disabled until a domain is selected and all active flows have non-empty description and label values.
  - **Operation section** (greyed-out and non-interactive until the pipeline is running): a Video panel (test pattern dropdown, timecode checkbox, ident text input with Apply button) and two independent Audio panels (test pattern dropdown, channel count numeric input, audio level fader with dBFS readout).
- Wire all controls to the corresponding FastAPI endpoints on port 9600. In `App.jsx`, the API base URL is hardcoded as `` `http://${window.location.hostname}:9600` ``.
- The built React static files are served by `python3 -m http.server 9700 --directory /app/frontend/dist` in the entrypoint script — no FastAPI `StaticFiles` mount is needed.
- Update `vite.config.js` to proxy all API paths (`/domains`, `/patterns`, `/options`, `/pipeline`, `/video`, `/audio`) to `http://localhost:9600` for local development.

**Step 4: Pipeline Documentation**
Once the application is fully working, update **Section 2 — Test Generator** in `./gst-apps/gstreamer-pipeline.md` to reflect the actual pipeline built. That section must contain:
- A `gst-launch-1.0` CLI equivalent showing the full pipeline (video branch + Audio Flow 1 branch + Audio Flow 2 branch), including all capsfilters, overlays, and mxl sinks with representative property values.
- A Mermaid `flowchart` diagram with one subgraph per branch (Video, Audio Flow 1, Audio Flow 2) clearly showing each element and its connections.
- A prose explanation of what the pipeline does, with justification for key plugin choices (e.g. why `videoconvert` is needed before the `v210` capsfilter, why `audioconvert` is used, why explicit capsfilters are mandatory for `mxlsink`, why channel count changes require a pipeline rebuild, etc.).

Please write the necessary Dockerfiles, Python backend scripts, React components, and integration code following these guidelines at `./gst-apps/test-generator`, modifying content already present.
