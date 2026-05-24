# GStreamer MXL Apps — Quick Start

This directory contains three GStreamer-based web applications that produce, inspect, and consume [MXL](../dmf-mxl) flows. Each app runs as a single Docker container: a FastAPI backend (also serving GStreamer pipelines where needed) and a React frontend, both on the same port.

---

## Apps at a glance

| App | Image | Port | What it does |
|-----|-------|------|--------------|
| [Test Generator](#1-test-generator) | `test-generator:latest` | `9600` | Generates synthetic colour-bar video and tone audio, publishes to MXL |
| [MXL Info GUI](#2-mxl-info-gui) | `mxl-info-gui:latest` | `9699` | Probes MXL domains and displays live flow metadata |
| [MXL to WebRTC](#3-mxl-to-webrtc) | `mxl2webrtc:latest` | `9601` | Reads MXL flows and relays them as a low-latency WebRTC stream in the browser |

Pre-built images are published to `ghcr.io/cbcrc` — see [Exercise 5](../Exercises/Exercise5.md) to spin up the whole system without compiling anything.

---

## Prerequisites

- Docker with Compose V2 (`docker compose`)
- A host directory containing a `domain_def.json` file (the MXL domain root). Set the path in `docker-compose.yml` under the `mxl-domain` volume `device:` field, or export `MXL_DOMAIN_DEVICE` before running.

---

## Running the apps

```bash
cd gst-apps

# Point the shared volume at your MXL domain directory
export MXL_DOMAIN_DEVICE=/path/to/your/mxl-domain

# Start all services
docker compose up -d

# Or start only what you need
docker compose up -d test-generator mxl-info-gui
docker compose up -d mediamtx mxl2webrtc
```

Open the UIs in a browser once the containers are up:

| App | URL |
|-----|-----|
| Test Generator | http://localhost:9600 |
| MXL Info GUI | http://localhost:9699 |
| MXL to WebRTC | http://localhost:9601 |

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

For the GStreamer pipeline details see [gstreamer-pipeline.md — Section 2](./gstreamer-pipeline.md#2-test-generator-gst_generatorpy).

---

## 2. MXL Info GUI

**Image:** `ghcr.io/cbcrc/mxl-info-gui:latest`

A read-only monitoring tool. Wraps the `mxl-info` CLI command and presents its output as a live web dashboard.

**What it shows:**
- All MXL domains found under `/mxl-domain`.
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

For the full GStreamer pipeline breakdown see [gstreamer-pipeline.md — Section 6](./gstreamer-pipeline.md#6-mxl-to-webrtc-gst_mxl2webrtcpy).

---

## Building from source

If you want to build the Docker images yourself (requires the MXL SDK compiled first), follow [how_to_build.md](../how_to_build.md).

```bash
# After building the MXL SDK (see how_to_build.md Steps 1–2)
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
