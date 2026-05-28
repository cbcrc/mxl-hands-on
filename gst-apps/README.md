# GStreamer MXL Apps — Quick Start

This directory contains six GStreamer-based web applications that produce, inspect, and consume [MXL](../dmf-mxl) flows. Each app runs as a single Docker container: a FastAPI backend (also serving GStreamer pipelines where needed) and a React frontend, both on the same port.

---

## Apps at a glance

| App | Image | Port | What it does |
|-----|-------|------|--------------|
| [Test Generator](#1-test-generator) | `test-generator:latest` | `Depending on Docker compose config` | Generates synthetic colour-bar video and tone audio, publishes to MXL |
| [MXL Info GUI](#2-mxl-info-gui) | `mxl-info-gui:latest` | `Depending on Docker compose config` | Probes MXL domains and displays live flow metadata |
| [MXL to WebRTC](#3-mxl-to-webrtc) | `mxl2webrtc:latest` | `Depending on Docker compose config` | Reads MXL flows and relays them as a low-latency WebRTC stream in the browser |
| [File Player](#4-file-player) | `file-player:latest` | `Depending on Docker compose config` | Loops a media file (MP4/TS) and publishes its video and/or audio streams to MXL |
| [HLS to MXL Gateway](#5-hls-to-mxl-gateway) | `hls2mxl:latest` | `Depending on Docker compose config` | Ingests a live HLS stream and republishes it as MXL video and audio flows |
| [Input Selector](#6-input-selector) | `input-selector:latest` | `Depending on Docker compose config` | Live-switches between three MXL video inputs and publishes the active one to a single MXL video output |
| [HTML5 Keyer](#7-html5-keyer) | `html5-keyer:latest` | `9605` | Composites an HTML5 graphics overlay (CEF/Chromium) over a live MXL video background and publishes the result as an MXL output flow |

Pre-built images are published to `ghcr.io/cbcrc` — see [Exercise 5](../Exercises/Exercise5.md) to spin up the whole system without compiling anything.

---

## Prerequisites

- Docker with Compose V2 (`docker compose`)
- A host directory containing a `domain_def.json` file (the MXL domain root). Set the path in `docker-compose.yml` under the `mxl-domain` volume `device:` field, or export `MXL_DOMAIN_DEVICE` before running.

---

## Environment setup

Docker Compose reads a `.env` file in this directory to resolve variables used in `docker-compose.yml`. A template is provided — copy it and fill in your local paths before starting any service:

```sh
cd ~/mxl-hands-on/gst-apps
cp .env.template .env
# then open .env in your editor and set MEDIA_DEVICE
```

> **Note:** `.env` is git-ignored so your local paths are never committed to the repository.

### Variables

| Variable | Used by | Description |
|----------|---------|-------------|
| `MEDIA_DEVICE` | `file-player` | Absolute path to a host directory containing the `.mp4` / `.ts` files you want the File Player to loop. Mounted read-only inside the container at `/home/file`. |

---

## Running the apps

**Make sure you have your tmpfs volume mounted as in the preparation steps for [Linux](../Preparation/WSL-Ubuntu.md) or [Mac](../Preparation/MAC.md)**

On linux
```sh
cd ~/mxl-hands-on/docker/exercise-5
./start.sh
```

On Mac
```sh
cd ~/mxl-hands-on/docker/exercise-5
./start-mac.sh
```

## Or start only what you need (Domain need to be setup as per the starting script)
```sh
docker compose up -d test-generator mxl-info-gui
docker compose up -d mediamtx mxl2webrtc
```

Open the UIs in a browser once the containers are up:

| App | URL | API Swagger Page |
|-----|-----|-----|
| Test Generator | http://localhost:9600 | http://localhost:9600/docs |
| MXL Info GUI | http://localhost:9699 | http://localhost:9699/docs |
| MXL to WebRTC | http://localhost:9601 | http://localhost:9601/docs |
| File Player | http://localhost:9602 | http://localhost:9602/docs |
| HLS to MXL Gateway | http://localhost:9603 | http://localhost:9603/docs |
| Input Selector | http://localhost:9604 | http://localhost:9604/docs |
| HTML5 Keyer | http://localhost:9605 | http://localhost:9605/docs |

---

## 1. Test Generator

**Image:** `ghcr.io/cbcrc/test-generator:latest`

Generates one synthetic video flow and two independent audio flows and writes them to an MXL domain on disk. No external signal source required.

**Setup panel** (before starting the pipeline):
- Select the MXL domain, output raster (720p / 1080p / 2160p), and frame rate (24–60 fps).
- Name each flow (group hint, description, label) and set the channel count for each audio flow (1–64 ch, fixed for the pipeline lifetime).

**Operation panel** (while running):
- Switch the video test pattern live (SMPTE bars, solid colours, etc.).
- Toggle the timecode burn-in and change the ident overlay without stopping the pipeline.
- Adjust the audio level (−60 … 0 dBFS in 0.5 dB steps) and wave type per audio flow.

For the GStreamer pipeline details see [gstreamer-pipeline.md — Section 1](./gstreamer-pipeline.md#2-test-generator-gst_generatorpy).

---

## 2. MXL Info GUI

**Image:** `ghcr.io/cbcrc/mxl-info-gui:latest`

A read-only monitoring tool. Wraps the `mxl-info` CLI command and presents its output as a live web dashboard.

**What it shows:**
- All MXL domains found under `/mxl-domain`, with their **buffer depth** displayed alongside the path. The depth is read from `options.json` in each domain directory (`urn:x-mxl:option:history_duration/v1.0`, stored in nanoseconds, shown in ms). Domains without an `options.json` show `200 ms (default)`.
- All active flows in the selected domain, grouped by group-hint name, with UUID, label, description, and role columns. Refreshes automatically every 30 seconds.
- Orphan flows — `.mxl-flow` directories on disk that `mxl-info` no longer reports (inactive or leftover from a previous session).
- Detailed per-flow info (version, format, grain rate, head index, latency, active state) for up to two flows simultaneously, with optional 500 ms live polling.

---

## 3. MXL to WebRTC

**Images:** `ghcr.io/cbcrc/mxl2webrtc:latest` + `bluenviron/mediamtx:latest`

Reads one MXL video flow and/or one MXL audio flow and relays them as a low-latency WebRTC stream viewable directly in the browser — no browser plugin required.

**How it works:**

```
mxlsrc → x264enc → rtph264pay ──┐
                                 ├─ webrtcbin ─(WHIP)→ MediaMTX ─(WHEP)→ Browser
mxlsrc → opusenc → rtpopuspay ──┘
```

1. The GStreamer pipeline encodes the MXL flows (H.264 zero-latency + Opus) and publishes to a local [MediaMTX](https://github.com/bluenviron/mediamtx) instance via **WHIP**.
2. The embedded browser player receives the stream from MediaMTX via **WHEP** and renders it in an HTML5 `<video>` element.
3. MediaMTX runs with `network_mode: host` so WebRTC ICE candidates reflect the real host IP — required for the browser to connect.

**Setup panel:** select the MXL domain, then pick a video flow and/or an audio flow from the discovered list. Only flows matching the correct role (video / audio) in their group hint are shown. Supports video+audio, video-only, or audio-only modes.

**Operation panel:** shows the active flow UUIDs, pipeline status, and the live WebRTC player with a mute/unmute toggle.

For the full GStreamer pipeline breakdown see [gstreamer-pipeline.md — Section 2](./gstreamer-pipeline.md#6-mxl-to-webrtc-gst_mxl2webrtcpy).

---

## 4. File Player

**Image:** `ghcr.io/cbcrc/file-player:latest`

Reads a local media file (`.mp4` or `.ts`) and publishes its video and/or audio streams as continuously looping MXL flows. No external signal source required — the pipeline decodes the file as-is and forwards raw frames to `mxlsink`. When the end of file is reached, a flushing seek restarts playback seamlessly from the beginning.

> **Before starting:** make sure `MEDIA_DEVICE` is set in your `.env` file (see [Environment setup](#environment-setup)). It must point to the host directory that contains your clip files.

**Setup panel** (before starting the pipeline):
- Select the MXL domain and the media file to play (files are loaded from a host directory mounted at `/home/file` inside the container). A **Refresh** button re-scans the directory.
- Set the **Group Hint** shared by all flows (default: `Clip-Player`).
- The **Flow Configuration Table** is populated automatically when a file is selected: up to one Video row and one Audio row, each with an **Active** checkbox, a **Description**, and a **Label** field. Individual streams can be excluded from the pipeline by unchecking their Active box.

**Operation panel** (while running):
- **Now Playing:** the filename currently loaded in the pipeline.
- **Stream Info:** codec, resolution, and frame rate for the video stream; codec, sample rate, and channel count for the audio stream.
- **Loop indicator:** a badge confirming that playback is looping.

For the GStreamer pipeline details see [gstreamer-pipeline.md — Section 3](./gstreamer-pipeline.md#1-file-player-gst_playerpy).

---

## 5. HLS to MXL Gateway

**Image:** `ghcr.io/cbcrc/hls2mxl:latest`

Ingests any live HLS stream and republishes it as one video flow and one audio flow in an MXL domain. No manual resolution, frame rate, or channel-count selection is required — the pipeline adapts automatically to whatever the HLS source delivers.

**How it works:**

```
uridecodebin (HLS URI)
  ├─ [pad-added] → videoconvert → capsfilter(v210) → queue → mxlsink   (Video flow)
  └─ [pad-added] → audioconvert → audioresample → capsfilter(F32LE 48k) → queue → mxlsink   (Audio flow)
```

A **two-phase stabilisation** approach is used to let the HLS adaptive-bitrate logic settle before writing to the MXL domain:
1. **Phase 1 — Warmup (10 s):** the stream is decoded into `fakesink` elements so the HLS client can ramp up to the highest quality variant.
2. **Phase 2 — Real pipeline:** the warmup is torn down and a fresh pipeline with `mxlsink` is started. Flow UUIDs are **deterministic** (UUID v5 derived from the group hint), so restarting or switching HLS URLs does not orphan existing flow directories.

**Setup panel** (before starting the pipeline):
- Select the MXL domain and enter the HLS stream URL.
- Set the **Group Hint** shared by both flows (default: `HLS2MXL`).
- Fill in per-flow **Description** and **Label** for the Video and Audio flows. Both flows are always active; channel count is detected automatically from the stream.
- A status badge shows `Stabilising…` for the first 10 seconds, then `Running`.

**Operation panel** (while running):
- Update the **HLS URL** field and press **Apply** to switch to a new stream source. The pipeline tears down, re-enters the 10-second stabilisation window, and resumes with the same flow UUIDs and metadata.

For the GStreamer pipeline details see [gstreamer-pipeline.md — Section 4](./gstreamer-pipeline.md#4-hls-to-mxl-gateway-gst_hls2mxlpy).

---

## 6. Input Selector

**Image:** `ghcr.io/cbcrc/input-selector:latest`

A live 3-to-1 MXL video switcher. Reads up to three MXL video flows from the same domain, routes exactly one of them at a time to a single MXL video output flow, and lets the user switch the active input from the UI without rebuilding the pipeline.

**How it works:**

```
mxlsrc (input 1) → capsfilter(v210) → queue ┐
mxlsrc (input 2) → capsfilter(v210) → queue ┼→ input-selector ─→ capsfilter(v210) → queue → mxlsink (output)
mxlsrc (input 3) → capsfilter(v210) → queue ┘
```

All three input branches stay in `PLAYING` at all times. Switching the live output is a property change on the `input-selector` element (`active-pad`) — sub-frame, no pipeline rebuild. This works only when every selected input shares the same raster, frame rate, and interlace mode, so the backend reads each input's `flow_def.json` at Start time and rejects mismatches with a per-slot error banner.

**Setup panel** (before starting the pipeline):
- Select the MXL domain. The same domain is used for the three inputs and the output.
- Pick an MXL video flow for **Input 1**, **Input 2**, and **Input 3**. Any slot left as **"None — black fill"** is wired to a synthetic black `videotestsrc` cloned from the validated common format (so the slot is structurally present but black). At least one slot must be a real MXL flow — the output format is derived from the selected inputs.
- Set the output **Group Hint** (default `Input-Selector`), **Description** (default `selector-out-1`), and **Label** (default `input-selector-video`). The output flow UUID is **deterministic** (UUID v5 from the group hint), so restarting with the same group hint reuses the same flow directory.
- Format mismatches between selected inputs are displayed as a dismissible red banner listing each slot's detected `WxH @ num/den` and what differs.

**Operation panel** (while running):
- A three-button **Active Input** switcher — click a button to make that input the live output. The active button is highlighted in green.
- **Black-fill slots are visible but disabled** in the switcher (greyed out with a tooltip) because switching the live output between a live MXL clock and the synthetic `videotestsrc` clock domain trips `input-selector`'s caps/timing invariants. Switching among real MXL slots of identical format works flawlessly.
- The **Input Status** row shows each slot's source kind (MXL flow / black fill), its UUID, and a presence dot for the currently routed source.
- The **Output Flow** panel shows the deterministic UUID written to the domain and the active raster / frame rate / interlace mode.

For the GStreamer pipeline details see [gstreamer-pipeline.md — Section 5](./gstreamer-pipeline.md#5-input-selector-gst_selectorpy).

---

## 7. HTML5-Keyer

**Image:** `html5-keyer:latest` (build from source — not yet published to GHCR)

Composites an HTML5 graphics page rendered by an embedded Chromium/CEF browser as an alpha-keyed overlay over a live MXL video background, and publishes the composited result as a single MXL video output flow.

**How it works:**

```
mxlsrc (input) → videoconvert → BGRA capsfilter → queue ─────────────────────────────────┐
                                                                                           ├→ compositor (CPU) → identity (PTS fix) → videoconvert → v210 capsfilter → queue → mxlsink (output)
cefsrc (URL)   → BGRA capsfilter → videorate → BGRA capsfilter → queue  ─────────────────┘
                                                        (compositor.sink_1: sync=false, alpha toggles key)
```

**Setup panel** (before starting the pipeline):
- Select the MXL domain and a video input flow. The output format (resolution, frame rate) is derived from the selected input — no rescaling is performed.
- Enter the **HTML5 Graphics URL** rendered by the embedded Chromium browser and composited as the overlay.
  > ⚠️ **URL inside Docker:** `localhost` inside the container refers to the container itself, not your machine. To reach a service running on the host (e.g. SPX Server on port 5660), use `http://host.docker.internal:5660/renderer/`. The `host.docker.internal` hostname is pre-configured in the `html5-keyer` service via `extra_hosts: host-gateway`. Your browser on the host uses `http://localhost:5660/renderer/` as normal — only the URL entered in the keyer UI needs the `host.docker.internal` form.
- Set the output **Group Hint** (default `HTML5-Keyer`), **Description** (default `keyer-out-1`), and **Label** (default `html5-keyer-video`). The output flow UUID is **deterministic** (UUID v5 from the group hint), so restarting with the same group hint reuses the same MXL flow directory.

**Operation panel** (while running):
- **Key ON / OFF** — a large toggle button. The pipeline starts with the key **OFF** (overlay hidden, `compositor.sink_1.alpha = 0.0`). Clicking **Key OFF** switches to **Key ON** (`alpha = 1.0`), compositing the HTML5 graphic over the background. Toggling is live — no pipeline rebuild.
- Status panel shows the input flow UUID (first 8 chars), overlay URL, output flow UUID, format (`WxH @ num/den fps`), and a **● PUBLISHING** badge.

For the GStreamer pipeline details see [gstreamer-pipeline.md — Section 6](./gstreamer-pipeline.md#6-html5-keyer-gst_keyerpy).

---

## Building from source

If you want to build the Docker images yourself you can run these commands and read more on our process [here](../how_to_build.md)

```sh
cd ~/mxl-hands-on/dmf-mxl
git checkout main
git pull origin main
cd ..
./build_linux.sh
cd gst-apps
docker compose build
```

---

## Further reading

| Resource | What it covers |
|----------|---------------|
| [gstreamer-pipeline.md](./gstreamer-pipeline.md) | `gst-launch-1.0` equivalents, Mermaid diagrams, and prose explanations for every pipeline |
| [how_to_build.md](../how_to_build.md) | Full build guide: MXL SDK → portable apps → Docker images → push to GHCR |
| [Exercises/Exercise5.md](../Exercises/Exercise5.md) | Step-by-step walkthrough using the pre-built images from `ghcr.io/cbcrc` |
