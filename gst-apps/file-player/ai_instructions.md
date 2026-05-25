# AI Agent Instructions: GStreamer File Player

## Project Overview
Build a Dockerized media application that functions as a looping video/audio file player. The application uses GStreamer for media playback, FastAPI for the backend API, and React + Vite for the web frontend. The application is located at `./gst-apps/file-player`. There is no NMOS node, no nmos_bridge, and no NMOS-related dependencies.

## System Architecture
The application runs as a **single service on one port** inside Docker:
1. **FastAPI (port 9600):** Serves both the REST API and the built React static files (mounted via `StaticFiles`). The `StaticFiles` mount is added **last**, after all API routes, so API paths take precedence.
2. **Frontend:** Built with React + Vite into a `dist/` directory. No separate static file server — FastAPI serves `dist/` directly via `fastapi.staticfiles.StaticFiles`.
3. **Media Engine:** GStreamer via `gi.repository.Gst` and `gi.repository.GstPbutils` Python bindings handles the actual media pipeline.

This single-port design means the browser always uses the **same origin** for both the UI and the API, so no port number is hardcoded in the frontend JavaScript. The docker-compose host-port mapping is `9602:9600` (host:container).

## Environment & File Specifications
- **Media Mount:** Media files are mounted inside the Docker container at `/home/file`. The application lists and plays files from this directory.
- **Media Format:** The player must support `.ts` (MPEG-TS) and `.mp4` (H.264/H.265) containers, at any resolution and frame rate present in the file. No format conversion or re-scaling is applied — the pipeline decodes the file as-is and forwards raw frames to `mxlsink`.
- **Stream Detection:** Before building a pipeline, the backend probes the selected file using `GstPbutils.Discoverer` to determine which streams are present (video, audio, or both). The pipeline and the MXL flow configuration adapt accordingly: a video-only file produces one video MXL flow, an audio-only file produces one audio MXL flow, and a file with both streams produces one of each.
- **Playback Mode:** The pipeline always plays the file in a **continuous loop**. When the GStreamer bus receives an `EOS` message, the backend issues a flushing seek to position 0 to restart seamlessly.
- **GStreamer Pipeline:** The pipeline uses `uridecodebin` to demux and decode the file. Decoded pads are linked dynamically via the `pad-added` signal. A `capsfilter` with `video/x-raw,format=v210` **must** be placed immediately before every mxl video sink, and a `capsfilter` with `audio/x-raw,format=F32LE,layout=interleaved` **must** be placed immediately before every mxl audio sink. Without these explicit capsfilters, auto-negotiation may land on an incompatible format and cause a hard failure at runtime.
- **MXL Flow Identity:** Flow UUIDs are **deterministic** — derived via UUID v5 from a fixed application namespace and the name `"<grouphint>:<role>"` (e.g. `"Clip-Player:video"`). This means restarting the pipeline with the same group hint reuses the same UUIDs and overwrites the existing flow files in the domain, while changing the group hint produces a completely different set of UUIDs. The application namespace UUID is a constant defined in `gst_player.py` (`_MXL_PLAYER_NS`) and must never change after deployment, as doing so would orphan previously written flow directories. Once the pipeline is running and the mxl sink has written the flow to disk, the backend must poll until `{selected-domain-path}/{flow_uuid}.mxl-flow/flow_def.json` exists for each active flow, then patch that file: set `grouphint` to `"<user-grouphint>:Video"` for the video flow and `"<user-grouphint>:Audio"` for the audio flow; also update `tags["urn:x-nmos:tag:grouphint/v1.0"]` to `["<user-grouphint>:Video"]` or `["<user-grouphint>:Audio"]` respectively (if the `tags` object is present); and replace `description` and `label` with the values provided by the user in the Setup section.

## Required API & UI Functionalities

The UI is divided into two distinct sections: **Setup** and **Operation**.

---

### Section 1 — Setup

This section is used to configure the MXL flows and select the file before starting the pipeline. All controls in this section are disabled while the pipeline is running.

1. **MXL Domain Selector:** Scan `/mxl-domain` recursively for `domain_def.json` files. Read the `id` field for the domain UUID and use the containing directory path as the domain path (passed to `mxlsink`'s `domain` property). Provide a dropdown to select the target MXL domain.
2. **File Selector:** List all files present in `/home/file` and provide a dropdown to select one. Display the filename only (not the full path). A **Refresh** button re-scans the directory.
3. **Group Hint:** A text input shared across all flows produced by this file. Default value: `Clip-Player`.
4. **Flow Configuration Table:** Rows are populated dynamically after a file is probed (see `GET /files/probe`). Up to two rows — one for a Video flow (if the file has a video stream) and one for an Audio flow (if the file has an audio stream) — each with:
   - **Active checkbox** — tick to include the flow in the pipeline. Both are active by default.
   - **Description** — text input unique to each flow. Defaults: `video-out-1`, `audio-out-1`.
   - **Label** — text input unique to each flow. Defaults: `clip-player-video`, `clip-player-audio`.
   - Description and Label are mandatory fields; the Start button is disabled until all active flows have non-empty values.
5. **Start / Stop button** — probes the selected file, builds the GStreamer pipeline, and begins looping playback when clicked. Changes to a **Stop** button once the pipeline is running. Clicking Stop tears down the pipeline. Only active flows are included in the pipeline.

> The Probe step is implicit: when the user clicks Start, the backend probes the file first. The frontend also calls `GET /files/probe?path=<filename>` when the user selects a file in the dropdown, so that the Flow Configuration Table can be pre-populated before the user clicks Start.

---

### Section 2 — Operation

This section is enabled only once the pipeline is running (greyed-out and non-interactive otherwise).

**Playback Info panel:**
1. **Now Playing:** Display the filename currently loaded in the pipeline.
2. **Stream Info:** Show detected stream details returned by the probe (codec, resolution, frame rate for video; codec, sample rate, channel count for audio).
3. **Loop indicator:** A small badge that reads "Looping" while playback is active.

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
  `MXL_DOMAIN_DEVICE` must be set to an absolute path on the host that contains `domain_def.json` before running `docker compose up` (e.g. via a `.env` file next to `docker-compose.yml` or an exported shell variable).
- **Media files volume:** Add a bind mount for `/home/file` so the host directory containing media clips is accessible inside the container:
  ```yaml
  volumes:
    - ${MEDIA_DEVICE}:/home/file:ro
  ```
  `MEDIA_DEVICE` must be set to the absolute host path containing the media files (e.g. `./Clips`).
- Add port mapping `9602:9600` only — FastAPI serves both the API and the React frontend on the same port. No separate frontend port is needed.
- The Dockerfile uses **two stages**: a `node:18-bullseye-slim` stage to build the React frontend, and an `ubuntu:24.04` runtime stage.
- The build context is the repository root (`..` relative to `./gst-apps/`). All `COPY` paths in the Dockerfile are therefore relative to the repository root (e.g. `COPY gst-apps/file-player/backend/ /app/backend/`).
- The runtime stage installs: `python3`, `python3-pip`, `python3-gi`, `python3-gi-cairo`, `gir1.2-gstreamer-1.0`, `gir1.2-gst-plugins-base-1.0`, `gstreamer1.0-tools`, `gstreamer1.0-plugins-base`, `gstreamer1.0-plugins-good`, `gstreamer1.0-plugins-bad`, `gstreamer1.0-plugins-ugly`, `gstreamer1.0-libav`.
- Copy `libgstmxl.so` to `/usr/lib/x86_64-linux-gnu/gstreamer-1.0/` (the default GStreamer plugin path on Ubuntu 24.04 — no `GST_PLUGIN_PATH` env var is needed). Copy `libmxl.so.1.1` and `libmxl-common.so.1.1` to `/opt/mxl/lib/` and create the expected symlinks. Set `ENV LD_LIBRARY_PATH=/opt/mxl/lib`.
- Create the symlink that `libgstmxl.so` needs (it `dlopen()`s `libmxl.so` from its compile-time build path):
  ```
  && mkdir -p /workspace/mxl/build/Linux-Clang-Release/lib \
  && ln -sf /opt/mxl/lib/libmxl.so /workspace/mxl/build/Linux-Clang-Release/lib/libmxl.so
  ```

**Step 2: FastAPI & GStreamer Backend**
- Create a FastAPI application on port 9600. No NMOS bridge or startup event is needed.
- Implement a `GstPlayer` class using `gi.repository.Gst`. The pipeline is **not** started at init — only when `start(config)` is called explicitly.
- **Looping:** Inside the GStreamer bus watch callback, intercept `GST_MESSAGE_EOS` and immediately issue:
  ```python
  pipeline.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, 0)
  ```
  to restart playback from the beginning without rebuilding the pipeline.
- **Dynamic pad linking:** Connect to the `pad-added` signal of `uridecodebin`. In the callback, inspect the new pad's caps: if the media type starts with `video/`, link to the video branch; if it starts with `audio/`, link to the audio branch. Ignore pads for streams whose flow is not active in the current config.
- **Setup endpoints:**
  - `GET /domains` — scan `/mxl-domain` recursively for `domain_def.json` files; return `path` (containing directory), `id` (UUID from JSON `id` field), and `label` per domain.
  - `GET /files` — list all files in `/home/file`; return filenames only (no subdirectory recursion needed). Return an empty list if the directory is empty or does not exist.
  - `GET /files/probe` — accepts `?path=<filename>` query parameter (filename only, resolved against `/home/file`). Uses `GstPbutils.Discoverer` to probe the file and returns: `has_video` (bool), `has_audio` (bool), and `streams` (list of dicts with `type`, `codec`, and format-specific fields such as `width`, `height`, `framerate` for video or `sample_rate`, `channels` for audio).
  - `POST /pipeline/start` — accepts `domain` (path), `grouphint`, `file` (filename, resolved against `/home/file`), and per-flow config. `video` and `audio` each have `active` (bool), `description`, and `label`. On each call: (1) probe the file to confirm available streams, (2) generate deterministic UUIDs for each active flow, (3) build and start the GStreamer pipeline with loop-on-EOS enabled, (4) return once the pipeline reaches PLAYING state, (5) in a background thread, poll until `{domain-path}/{flow_uuid}.mxl-flow/flow_def.json` exists for each active flow, then patch `grouphint`, `tags["urn:x-nmos:tag:grouphint/v1.0"]`, `description`, and `label`.
  - `POST /pipeline/stop` — stops and tears down the GStreamer pipeline, releasing all resources.
  - `GET /pipeline/status` — returns the full runtime state: `running` (bool), `file` (current filename or null), `flow_uuids` (dict with `video` and/or `audio` keys), and `streams` (probe result for the loaded file).
- **GStreamer pipeline structure:**
  - Source/decode: `filesrc location=<path> → uridecodebin` (or equivalent; `uridecodebin` handles both `.ts` and `.mp4`).
  - Video branch (when active): `uridecodebin[video pad] → queue → videoconvert → capsfilter(video/x-raw,format=v210) → queue → mxlsink`
  - Audio branch (when active): `uridecodebin[audio pad] → queue → audioconvert → audioresample → capsfilter(audio/x-raw,format=F32LE,layout=interleaved,rate=48000) → queue → mxlsink`

**Step 3: React + Vite Frontend**
- Initialize a React + Vite project inside `frontend/`.
- **Header branding:** At the very top of the page, display the CBC Radio-Canada logo (`gst-apps/logo/rgb_cbc-radio-canada-col-coul.png`) inline beside the "MXL File Player" h1 title. Copy the logo into `frontend/public/cbc-logo.png` so Vite serves it as a static asset; reference it in JSX as `<img src="/cbc-logo.png" />` with `height: 2.2rem`. The logo and title must share a flex row (`display: flex; align-items: center; gap: 1rem`).
- Structure the UI into two clearly labelled sections:
  - **Setup section** (always visible, disabled while running): MXL domain dropdown, file selector dropdown with Refresh button, group hint input, a dynamic flow configuration table (Video row and/or Audio row depending on probe result) each with an active checkbox, description, and label inputs, and a Start/Stop button. The Start button is disabled until a domain is selected, a file is selected, the file has been probed successfully, and all active flows have non-empty description and label values. When the user selects a file from the dropdown, the frontend automatically calls `GET /files/probe` and populates the flow table.
  - **Operation section** (greyed-out and non-interactive until the pipeline is running): a Playback Info panel (filename, stream info, loop badge).
- In `App.jsx`, set `const API = ""` so all fetch calls use relative paths (e.g. `${API}/pipeline/status`). This means the browser always calls the same origin as the page — no port number hardcoded, works regardless of the docker-compose host-port mapping.
- FastAPI serves the React static files directly: `app.mount("/", StaticFiles(directory="/app/frontend/dist", html=True), name="static")` must be the **last** statement in `backend/main.py` so API routes take precedence. Add `aiofiles>=23.0.0` to `requirements.txt` (required by `StaticFiles`).
- Update `vite.config.js` to proxy all API paths (`/domains`, `/files`, `/pipeline`) to `http://localhost:9600` for local development (Vite dev server only — not used in the Docker image).

**Entrypoint** — single process, single port:
```bash
cd /app
exec python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 9600
```

**Step 4: Pipeline Documentation**
Once the application is fully working, update **Section 3 — File Player** in `./gst-apps/gstreamer-pipeline.md` (create the section if it does not exist) to reflect the actual pipeline built. That section must contain:
- A `gst-launch-1.0` CLI equivalent showing the full pipeline (video branch + audio branch), including all capsfilters, overlays, and mxl sinks with representative property values.
- A Mermaid `flowchart` diagram with one subgraph per branch (Video, Audio) clearly showing each element and its connections.
- A prose explanation of what the pipeline does, with justification for key plugin choices (e.g. why `uridecodebin` is used instead of a fixed demuxer, why `videoconvert` is needed before the v210 capsfilter, why `audioconvert` + `audioresample` are needed before the F32LE capsfilter, why explicit capsfilters are mandatory for `mxlsink`, how looping is implemented via EOS seek, and how dynamic pad linking handles video-only and audio-only files).

Please write the necessary Dockerfiles, Python backend scripts, React components, and integration code following these guidelines at `./gst-apps/file-player`, modifying content already present.
