# AI Agent Instructions: GStreamer HLS to MXL Gateway

## Project Overview
Build a Dockerized media application that functions as an HLS-to-MXL gateway. The application uses GStreamer for media functionalities, FastAPI for the backend API, and React + Vite for the web frontend. The final goal is illustrated in the mermaid diagram located here `./Exercises/Exercise5.md`. The application is located at `./gst-apps/hls2mxl`. NMOS has been removed entirely — there is no NMOS node, no nmos_bridge, and no NMOS-related dependencies.

## System Architecture
The application runs as a **single service on one port** inside Docker:
1. **FastAPI (port 9600 internal / 9603 external):** Serves both the REST API and the built React static files (mounted via `StaticFiles`). The `StaticFiles` mount is added **last**, after all API routes, so API paths take precedence.
2. **Frontend:** Built with React + Vite into a `dist/` directory. No separate static file server — FastAPI serves `dist/` directly via `fastapi.staticfiles.StaticFiles`.
3. **Media Engine:** GStreamer via `gi.repository.Gst` Python bindings handles the actual media pipeline. The pipeline ingests an HLS stream via `uridecodebin` and outputs video and audio flows to `mxlsink`.

This single-port design means the browser always uses the **same origin** for both the UI and the API, so no port number is hardcoded in the frontend JavaScript. The docker-compose host-port mapping (`9603:9600`) can be changed freely without rebuilding the image.

## Environment & File Specifications
- **Media Format:** The gateway accepts any video resolution, frame rate, and audio channel count that the HLS stream provides. No manual selection of resolution, frame rate, or channel count is required or exposed — the pipeline adapts to whatever the source delivers.
- **GStreamer Pipeline:** The pipeline is user-controlled (start/stop). It ingests an HLS URI via `uridecodebin`, decodes video and audio streams dynamically via `pad-added` signal handling, and outputs them via the purpose-built mxl sink (see `./dmf-mxl/rust/gst-mxl-rs/readme.md` for usage). The pipeline produces **one video flow** and **one audio flow**, each sent through its own `mxlsink` instance.
- **10-Second Stabilisation Buffer:** A `valve` element (initially `drop=True`) is placed immediately before each `mxlsink`. After the pipeline reaches PLAYING state, a background timer fires after **10 seconds** and sets each valve's `drop` property to `False`. Only then does data begin flowing into the MXL domain. This allows the HLS stream to fully buffer and stabilise before being written. During these 10 seconds the backend reports `"stabilising": true` in the status endpoint.
- **Apply / Re-link:** Applying a new HLS URL while the pipeline is running triggers a full pipeline teardown and rebuild, followed by the same 10-second stabilisation window. Because flow UUIDs are deterministic (see below), downstream consumers observe seamless flow-ID continuity.
- **mxlsink format requirements:** `mxlsink` only accepts specific formats. A `capsfilter` with `video/x-raw,format=v210` **must** be placed immediately before every mxl video sink, and a `capsfilter` with `audio/x-raw,format=F32LE,layout=interleaved` **must** be placed immediately before every mxl audio sink. Without these explicit capsfilters, auto-negotiation may land on an incompatible format and cause a hard failure at runtime.
- **MXL Flow Identity:** Flow UUIDs are **deterministic** — derived via UUID v5 from a fixed application namespace and the name `"<grouphint>:<role>"` (e.g. `"HLS2MXL:video"`). This means restarting the pipeline or applying a new HLS URL with the same group hint reuses the same UUIDs and overwrites the existing flow files in the domain, while changing the group hint produces a completely different set of UUIDs. The application namespace UUID is a constant defined in `gst_hls2mxl.py` (`_MXL_HLS_NS`) and must never change after deployment, as doing so would orphan previously written flow directories. Once the valves open and the mxl sink has written the flow to disk, the backend must poll until `{selected-domain-path}/{flow_uuid}.mxl-flow/flow_def.json` exists for each active flow, then patch that file: set `grouphint` to `"<user-grouphint>:Video"` for the video flow and `"<user-grouphint>:Audio"` for the audio flow; also update `tags["urn:x-nmos:tag:grouphint/v1.0"]` to `["<user-grouphint>:Video"]` or `["<user-grouphint>:Audio"]` respectively (if the `tags` object is present); and replace `description` and `label` with the values provided by the user in the Setup section.

## Required API & UI Functionalities

The UI is divided into two distinct sections: **Setup** and **Operation**.

---

### Section 1 — Setup

This section is used to configure the MXL flows and the initial HLS source before starting the GStreamer pipeline. The pipeline does **not** start until the user clicks the **Start** button. All controls in this section are disabled while the pipeline is running.

1. **MXL Domain Selector:** Scan `/mxl-domain` recursively for `domain_def.json` files. Read the `id` field for the domain UUID and use the containing directory path as the domain path (passed to `mxlsink`'s `domain` property). Provide a dropdown to select the target MXL domain.
2. **Group Hint:** A text input shared across all flows. Default value: `HLS2MXL`.
3. **HLS URL:** A text input for the initial HLS stream URL to connect to on start.
4. **Flow Configuration Table:** Two rows — one for the Video flow and one for the Audio flow — each always active (no checkbox). Channel count is not configurable; it is detected automatically from the incoming HLS stream. Each row has:
   - **Description** — text input unique to each flow. Defaults: `hls-video-out`, `hls-audio-out`.
   - **Label** — text input unique to each flow. Defaults: `hls-video`, `hls-audio`.
   - Description and Label are mandatory fields; the Start button is disabled until a domain is selected, the HLS URL is non-empty, and both flows have non-empty values.
5. **Start / Stop button** — starts the GStreamer pipeline with the configured flow metadata and HLS URL when clicked. Changes to a **Stop** button once the pipeline is running. Both flows (video and audio) are always included. A status badge beside the button shows `Stabilising…` for the first 10 seconds, then `Running`.

---

### Section 2 — Operation

This section is enabled only once the pipeline is running (greyed-out and non-interactive otherwise). During the 10-second stabilisation window the section remains enabled but displays a `Stabilising…` banner.

1. **HLS URL:** A text input pre-filled with the currently active HLS URL.
2. **Apply button** — applies the new HLS URL: tears down and rebuilds the GStreamer pipeline with the new URI, then re-enters the 10-second stabilisation window. All flow metadata (grouphint, description, label, channels) carries over from the Setup section. Flow UUIDs remain identical because the group hint has not changed.

---

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
- Add port mapping `9603:9600` only — FastAPI serves both the API and the React frontend on the same port. No separate frontend port is needed.
- The Dockerfile uses **two stages**: a `node:18-bullseye-slim` stage to build the React frontend, and an `ubuntu:24.04` runtime stage. There is no NMOS stage.
- The build context is the repository root (`..` relative to `./gst-apps/`). All `COPY` paths in the Dockerfile are therefore relative to the repository root (e.g. `COPY gst-apps/hls2mxl/backend/ /app/backend/`).
- The runtime stage installs: `python3`, `python3-pip`, `python3-gi`, `python3-gi-cairo`, `gir1.2-gstreamer-1.0`, `gir1.2-gst-plugins-base-1.0`, `gstreamer1.0-tools`, `gstreamer1.0-plugins-base`, `gstreamer1.0-plugins-good`, `gstreamer1.0-plugins-bad`, `gstreamer1.0-plugins-ugly`, `gstreamer1.0-libav`. `gstreamer1.0-plugins-good` provides `souphttpsrc` (HTTP transport for HLS) and `gstreamer1.0-plugins-bad` provides `hlsdemux`; both are required for HLS ingestion via `uridecodebin`.
- Copy `libgstmxl.so` to `/usr/lib/x86_64-linux-gnu/gstreamer-1.0/` (the default GStreamer plugin path on Ubuntu 24.04 — no `GST_PLUGIN_PATH` env var is needed). Copy `libmxl.so.1.1` and `libmxl-common.so.1.1` to `/opt/mxl/lib/` and create the expected symlinks. Set `ENV LD_LIBRARY_PATH=/opt/mxl/lib`.
- Create the symlink that `libgstmxl.so` needs (it `dlopen()`s `libmxl.so` from its compile-time build path):
  ```
  && mkdir -p /workspace/mxl/build/Linux-Clang-Release/lib \
  && ln -sf /opt/mxl/lib/libmxl.so /workspace/mxl/build/Linux-Clang-Release/lib/libmxl.so
  ```

**Step 2: FastAPI & GStreamer Backend**
- Create a FastAPI application on port 9600. No NMOS bridge or startup event is needed.
- Implement a `GstHLS2MXL` class using `gi.repository.Gst`. The pipeline is **not** started at init — only when `start(config)` is called explicitly.
- **Dynamic pad handling:** `uridecodebin` exposes pads dynamically once it has probed the stream. Connect to its `pad-added` signal. Inspect each new pad's caps to determine whether it is video (`video/x-raw` or `video/x-*`) or audio (`audio/x-raw` or `audio/x-*`), then link it to the appropriate downstream branch. Ignore pads that do not match an active flow.
- **Stabilisation valve:** Place a `valve` element (with `drop=True` initially) immediately before each `mxlsink`. Once the pipeline reaches PLAYING state, launch a background thread that sleeps 10 seconds, sets `valve.set_property("drop", False)` on all active valves, then begins polling for `flow_def.json` (see below). Maintain a `_stabilising` flag on the class that is set to `True` on start and `False` when the valves open.
- **GStreamer pipeline structure:**
  - Video: `uridecodebin → [pad-added link] → videoconvert → capsfilter(video/x-raw,format=v210) → valve(drop=True) → queue → mxlsink`
  - Audio: `uridecodebin → [pad-added link] → audioconvert → capsfilter(audio/x-raw,format=F32LE,rate=48000,layout=interleaved) → valve(drop=True) → queue → mxlsink` — the channel count is not fixed in the capsfilter; `audioconvert` passes through whatever channel count the HLS stream provides.
- **Setup endpoints:**
  - `GET /domains` — scan `/mxl-domain` recursively for `domain_def.json` files; return `path` (containing directory), `id` (UUID from JSON `id` field), and `label` per domain.
  - `POST /pipeline/start` — accepts `domain` (path), `grouphint`, `hls_url`, and per-flow config. Both `video` and `audio` objects have only `description` and `label` (no `active` flag, no `channels` — both flows are always started and channel count is auto-detected from the stream). On each call: (1) derive deterministic flow UUIDs via UUID v5 from `_MXL_HLS_NS` and `"<grouphint>:video"` / `"<grouphint>:audio"`, (2) build and start the GStreamer pipeline, (3) return once the pipeline reaches PLAYING state (valves still dropping, `stabilising=True`), (4) in a background thread: sleep 10 s, open valves, set `stabilising=False`, then poll until `{domain-path}/{flow_uuid}.mxl-flow/flow_def.json` exists for each flow, then patch `grouphint`, `tags["urn:x-nmos:tag:grouphint/v1.0"]`, `description`, and `label`.
  - `POST /pipeline/stop` — stops and tears down the GStreamer pipeline; resets `stabilising` to `False`.
  - `GET /pipeline/status` — returns the full runtime state: `running` (bool), `stabilising` (bool), `flow_uuids` (dict), and `hls_url` (str).
- **Operation endpoints (only functional while pipeline is running):**
  - `POST /hls/apply` — accepts `{"url": "<new_hls_url>"}`. Tears down the current pipeline, rebuilds it with the new URI and the same flow metadata (grouphint, description, label, channels) already held in the class state, and re-enters the 10-second stabilisation window. Flow UUIDs remain identical because the group hint has not changed.
  - `GET /hls/url` — returns the currently active HLS URL.

**Step 3: React + Vite Frontend**
- Initialize a React + Vite project inside `gst-apps/hls2mxl/frontend/`.
- **Header branding:** At the very top of the page, display the CBC Radio-Canada logo (`gst-apps/logo/rgb_cbc-radio-canada-col-coul.png`) inline beside the "MXL HLS Gateway" h1 title. Copy the logo into `frontend/public/cbc-logo.png` so Vite serves it as a static asset; reference it in JSX as `<img src="/cbc-logo.png" />` with `height: 2.2rem`. The logo and title must share a flex row (`display: flex; align-items: center; gap: 1rem`).
- Structure the UI into two clearly labelled sections:
  - **Setup section** (always visible, disabled while running): MXL domain dropdown, shared grouphint input, HLS URL input, a two-row flow configuration table (Video / Audio) each with description and label inputs only (no active checkbox, no channels — both flows are always active and channel count is auto-detected from the stream), and a Start/Stop button with a status badge (`Stabilising…` / `Running`). The Start button is disabled until a domain is selected, the HLS URL is non-empty, and both flows have non-empty description and label values.
  - **Operation section** (greyed-out and non-interactive until the pipeline is running; shows a `Stabilising…` banner during the first 10 seconds): HLS URL text input (pre-filled with the current URL) and an Apply button. The Apply button is disabled while `stabilising` is `true`.
- In `App.jsx`, set `const API = ""` so all fetch calls use relative paths (e.g. `${API}/pipeline/status`). This means the browser always calls the same origin as the page — no port number hardcoded, works regardless of the docker-compose host-port mapping.
- Poll `GET /pipeline/status` every 2 seconds while the pipeline is running to update the `stabilising` flag and status badge in real time.
- FastAPI serves the React static files directly: `app.mount("/", StaticFiles(directory="/app/frontend/dist", html=True), name="static")` must be the **last** statement in `backend/main.py` so API routes take precedence. Add `aiofiles>=23.0.0` to `requirements.txt` (required by `StaticFiles`).
- Update `vite.config.js` to proxy all API paths (`/domains`, `/pipeline`, `/hls`) to `http://localhost:9600` for local development (Vite dev server only — not used in the Docker image).

**Entrypoint** — single process, single port:
```bash
cd /app
exec python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 9600
```

**Step 4: Pipeline Documentation**
Once the application is fully working, add a new **Section — HLS to MXL Gateway** in `./gst-apps/gstreamer-pipeline.md`. That section must contain:
- A `gst-launch-1.0` CLI equivalent showing the full pipeline (video branch + audio branch), including all capsfilters, valve elements, and mxl sinks with representative property values. Note: `gst-launch-1.0` cannot replicate dynamic `pad-added` linking; document the closest static equivalent and annotate the dynamic parts.
- A Mermaid `flowchart` diagram with one subgraph per branch (Video, Audio) clearly showing each element and its connections, including the valve and the 10-second stabilisation step.
- A prose explanation of what the pipeline does, with justification for key plugin choices (e.g. why `uridecodebin` is used instead of a static `souphttpsrc ! hlsdemux` chain, why dynamic `pad-added` linking is required, why `valve` elements implement the 10-second stabilisation buffer rather than delaying pipeline state changes, why explicit capsfilters are mandatory for `mxlsink`, why channel count changes require a pipeline rebuild, etc.).

Please write the necessary Dockerfiles, Python backend scripts, React components, and integration code following these guidelines at `./gst-apps/hls2mxl`, modifying content already present.
