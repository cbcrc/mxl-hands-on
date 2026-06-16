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

Pre-built images are published to `ghcr.io/cbcrc` — see [Exercise 4](../Exercises/Exercise4.md) to spin up the whole system without compiling anything.

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
cd ~/mxl-hands-on/docker/exercise-4
./start.sh
```

On Mac
```sh
cd ~/mxl-hands-on/docker/exercise-4
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

Generates one synthetic video flow and two independent audio flows — and, optionally, an **Ancillary Data** flow that carries both **closed captions** and **SCTE-104** triggers — and writes them to an MXL domain on disk. No external signal source required.

**Setup panel** (before starting the pipeline):
- Select the MXL domain, output raster (720p / 1080p / 2160p), and frame rate (24–60 fps).
- Name each flow (group hint, description, label) and set the channel count for each audio flow (1–64 ch, fixed for the pipeline lifetime).
- Optionally enable the **Ancillary Data** flow (off by default) to also publish captions + SCTE-104 over one data flow.

**Operation panel** (while running):
- Switch the video test pattern live (SMPTE bars, solid colours, etc.).
- Toggle the timecode burn-in and change the ident overlay without stopping the pipeline.
- Adjust the audio level (−60 … 0 dBFS in 0.5 dB steps) and wave type per audio flow.
- Type caption text — it wraps and scrolls as looping closed captions (a **LOOPING** tally lights while active) — and fire a **SCTE-104 trigger** with a button (shows the count and last-trigger time).

For the GStreamer pipeline details see [gstreamer-pipeline.md — Section 1](./gstreamer-pipeline.md#2-test-generator-gst_generatorpy).

---

## 2. MXL Info GUI

**Image:** `ghcr.io/cbcrc/mxl-info-gui:latest`

A read-only monitoring tool. Wraps the `mxl-info` CLI command and presents its output as a live web dashboard.

**What it shows:**
- All MXL domains found under `/mxl-domain`, with their **buffer depth** displayed alongside the path. The depth is read from `options.json` in each domain directory (`urn:x-mxl:option:history_duration/v1.0`, stored in nanoseconds, shown in ms). Domains without an `options.json` show `200 ms (default)`.
- All active flows in the selected domain (those `mxl-info` confirms as `Active: true`), grouped by group-hint name, with UUID, label, description, and role columns. Refreshes automatically every 30 seconds.
- Inactive / stale flows — a separate table with a status tag per row: **inactive** = reported by `mxl-info` but currently `Active: false`; **stale** = a `.mxl-flow` directory on disk that `mxl-info` does not report at all (leftover or unreadable from a previous session).
- Detailed per-flow info (version, format, grain rate, head index, latency, active state) for up to two flows simultaneously, with optional 500 ms live polling.

---

## 3. MXL to WebRTC

**Images:** `ghcr.io/cbcrc/mxl2webrtc:latest` (+ `bluenviron/mediamtx:latest` for relay mode)

Reads one MXL video flow and/or one MXL audio flow and relays them as a low-latency WebRTC stream viewable directly in the browser — no browser plugin required.

### Two delivery modes

The UI checkbox **"Use MediaMTX relay"** selects how the stream reaches the browser. The modes differ in latency **and in how they scale with viewer count** — pick by what you need:

| Mode | Encoding | Best for | Intra-refresh |
|------|----------|----------|---------------|
| **MediaMTX relay** (ticked, default) | **once** — MediaMTX fans out to every viewer | **many** viewers | no |
| **Direct WHEP** (unticked) | **once per viewer** (CPU grows with viewers) | a **few**, lowest-latency viewers | yes |

In **both** modes every viewer watches the **same** MXL flow(s) chosen at Start (the app is a relay of one selection, not a per-viewer source picker).

**MediaMTX relay** (checkbox ticked — the default):

```
mxlsrc → x264enc → rtph264pay ──┐
                                 ├─ webrtcbin ─(WHIP)→ MediaMTX ─(WHEP)→ N Browsers
mxlsrc → opusenc → rtpopuspay ──┘
```

A **single** pipeline encodes the MXL flows (H.264 + Opus) **once** and pushes to a local [MediaMTX](https://github.com/bluenviron/mediamtx) instance via **WHIP**; MediaMTX acts as the SFU and fans the stream out to every browser via **WHEP** — so CPU does **not** grow with viewer count. This is the path for many viewers. Works everywhere (including Mac/WSL Docker Desktop). Cannot carry intra-refresh, so it keeps regular IDR keyframes.

**Direct WHEP** (checkbox unticked — lowest latency):

```
N Browsers ◀─(WebRTC)─ N × ( webrtcbin ◀─ x264enc / opusenc ◀─ mxlsrc )   (one full pipeline per viewer)
```

No MediaMTX: the app is its own signalling + media server, and **each viewer gets its own complete `mxlsrc → encode → webrtcbin` pipeline** — so CPU grows linearly with the viewer count (each opens its own reader on the same MXL flow). In return you get the **lowest latency** (no relay hop) and **intra-refresh** (no big periodic keyframes → smooth low-latency video). Signalling is *server-offers*: the server creates the SDP offer and the browser answers (webrtcbin reliably negotiates H.264 as the offerer). Intra-refresh is enabled automatically in this mode only — so it can never be selected in a mode that can't carry it. Best for one or a few simultaneous viewers.

### Direct-mode networking — one config, local or remote (MediaMTX mode needs none of this)

In Direct mode the browser connects straight to the `mxl2webrtc` container's `webrtcbin`, so its ICE **UDP** port must be reachable. The base `docker-compose.yml` already handles this and **the same config works whether the viewer is on this machine or another**: the service publishes the UDP range `8200-8210/udp` 1:1, and the backend rewrites the offer's ICE host candidate to **whatever host the browser opened the app on** — `localhost` for a local viewer (incl. Docker Desktop), the host's LAN IP for a remote viewer. No override file, no per-host env.

```sh
docker compose up -d mediamtx mxl2webrtc
```

Then open `http://<host>:9601` from any machine that can reach the Docker host (where `<host>` is `localhost` on the host itself, or the host's IP/name from another machine).

> In **MediaMTX mode** you don't touch `mxl2webrtc` networking at all — the browser connects to the separate `mediamtx` service (always `network_mode: host`), and `mxl2webrtc` only WHIP-pushes to it. For **many** viewers, use MediaMTX relay mode (it encodes once); Direct mode re-encodes per viewer, so keep it to a few.

**Setup panel:** select the MXL domain, then pick a video flow and/or an audio flow (only flows matching the correct role are shown; supports video+audio, video-only, or audio-only). Optionally also pick an **Ancillary Data** flow to decode its captions + SCTE alongside. Choose the delivery mode with the **Use MediaMTX relay** checkbox, and optionally adjust the H.264 encoder settings (tune, speed preset, bitrate, key-int max).

**Operation panel:** shows the active flow UUIDs, pipeline status, connected viewer count, and the live WebRTC player with a mute/unmute toggle. If an ancillary flow is selected, the decoded captions scroll as an overlay on the player and a SCTE-104 indicator light flashes for 5 s on each trigger (showing the event id and time).

For the full GStreamer pipeline breakdown see [gstreamer-pipeline.md — Section 2](./gstreamer-pipeline.md#2-mxl-to-webrtc-gst_mxl2webrtcpy).

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
