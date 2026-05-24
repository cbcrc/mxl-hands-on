# AI Agent Instructions: MXL to WebRTC Gateway

## Project Overview
Build a Dockerized media application that functions as an MXL receiver and WebRTC output gateway. The application uses GStreamer for media processing, FastAPI for the backend API, and React + Vite for the web frontend. It is a **receiver-only** application — it takes one MXL video flow and one MXL audio flow as inputs and publishes them as a low-latency WebRTC stream viewable directly from the browser. There is no NMOS control. Keep code style and patterns consistent with `./gst-apps/test-generator` and `./gst-apps/mxl-info-gui`.

## System Architecture
The application runs as a **single service on one port** inside Docker:
1. **FastAPI (port 9600):** Serves both the REST API and the built React static files (mounted via `StaticFiles`). The `StaticFiles` mount is added **last**, after all API routes, so API paths take precedence.
2. **Frontend:** Built with React + Vite into a `dist/` directory. No separate static file server — FastAPI serves `dist/` directly via `fastapi.staticfiles.StaticFiles`.
3. **Media Engine:** GStreamer via `gi.repository.Gst` Python bindings. The pipeline reads MXL flows via `mxlsrc`, converts and encodes media, then publishes to the **MediaMTX** service using RTSP push (`rtspclientsink`). The browser receives the stream via WebRTC from MediaMTX.
4. **MediaMTX service:** A separate Docker service (from `./gst-apps/mediamtx/`) that ingests the GStreamer RTSP stream and re-serves it as WebRTC via its WHEP endpoint.

This single-port design means the browser always uses the **same origin** for both the UI and the API, so no port number is hardcoded in the frontend JavaScript. The docker-compose host-port mapping (e.g. `9650:9600`) can be changed freely without rebuilding the image.

## Environment & File Specifications
- **MXL src documentation:** `./dmf-mxl/rust/gst-mxl-rs/readme.md` — authoritative reference for `mxlsrc` properties (`flow-id`, `domain`).
- **MXL domain root:** `/mxl-domain` (mounted Docker volume, shared with other services).
- **MXL-info binary:** `/opt/mxl/tools/mxl-info/mxl-info` — used for flow discovery (same as `mxl-info-gui`).
  > ⚠️ In the build tree the tool path is `./dmf-mxl/build/Linux-Clang-Release/tools/mxl-info/mxl-info` — the directory and the executable share the same name.
- **mxlsrc capsfilter requirements:** `mxlsrc` outputs raw media in specific formats. A `capsfilter` with `video/x-raw,format=v210` **must** be placed immediately after every mxl video source, and a `capsfilter` with `audio/x-raw,format=F32LE,layout=interleaved` **must** be placed immediately after every mxl audio source. Without these, auto-negotiation may land on an incompatible format.
- **Signaling / relay:** MediaMTX is configured to ingest RTSP and re-serve WebRTC. GStreamer publishes to `rtsp://mediamtx:8554/mxl2webrtc` (container-internal network name). The browser receives from `http://<host>:8889/mxl2webrtc/whep`.
- **Mode support:** The pipeline must support **video + audio**, **video only**, or **audio only**, depending on which flows the user selects in the Setup section. A mode with no flows selected must not be startable.

## Required API & UI Functionalities

The UI is divided into two distinct sections: **Setup** and **Operation**.

---

### Section 1 — Setup

This section is used to configure and select the MXL input flows before starting the GStreamer pipeline. The pipeline does **not** start until the user clicks the **Start** button. All controls in this section are disabled while the pipeline is running.

1. **MXL Domain Selector:** Scan `/mxl-domain` recursively for `domain_def.json` files. Read the `id` field for the domain UUID and use the containing directory path as the domain path. Provide a dropdown to select the target MXL domain. Changing the domain resets both flow selectors.
2. **Video Flow Selector:** A dropdown populated from the flow list for the selected domain. Each option displays the flow label, group hint, and the first 8 characters of the UUID in the format `(<UUID prefix>…), <Description> — <Label> — <GroupHint> `. Flows are **grouped by group name** (the prefix before `:` in `flow_grouphint`); each group is shown with a coloured header row spanning all four columns. Scales up to 20 rows; scrollable beyond that. Polls `scan_domain` every **30 seconds** when a domain is selected. Includes a **"None"** option at the top — selecting "None" means no video input (audio-only mode). **Only show flows that have Video after : in their group hint**
3. **Audio Flow Selector:** A dropdown populated from the same domain flow list, following the same display format. Includes a **"None"** option at the top — selecting "None" means no audio input (video-only mode). **Only show flows that have Audio after : in their group hint**
4. **Refresh Flow List button** — manually triggers `scan_domain` for the selected domain.
5. **Start / Stop button** — starts the GStreamer pipeline with the selected flows. At least one flow (video or audio) must be selected for the Start button to be enabled. Changes to a **Stop** button while the pipeline is running.

> ℹ️ Both flow selectors are populated using the same domain-scanning and parsing logic as `mxl-info-gui` — calling `mxl-info -d <domain_path>` and parsing the output. Only active flows (those reported by `mxl-info`) are shown.

---

### Section 2 — Operation

This section is enabled only once the pipeline is running (greyed-out and non-interactive otherwise).

1. **Pipeline Status indicator:** A coloured badge — green "Running" / grey "Stopped".
2. **MXL Input Status:** Displays the selected video flow UUID and label (or "—" if video-only is not active) and the selected audio flow UUID and label (or "—" if audio is not active).
3. **WebRTC Player:** An embedded HTML5 `<video>` element that receives the low-latency WebRTC stream from MediaMTX using the WHEP protocol. The player connects to `<mediamtx_webrtc_url>/mxl2webrtc/whep` — where `mediamtx_webrtc_url` is returned by the `/config` endpoint. The player starts automatically when the pipeline starts and stops when it stops.

---

## Step-by-Step Implementation Guide

### Step 1: Docker Setup

- Use `./gst-apps/docker-compose.yml` as a baseline.
- The `mxl-domain` named volume is defined in `docker-compose.yml` and shared by all services.
- Add the following two services to `docker-compose.yml`:

  **mxl2webrtc service:**
  ```yaml
  mxl2webrtc:
    platform: linux/amd64
    build:
      context: ..
      dockerfile: gst-apps/mxl2webrtc/Dockerfile
    image: mxl2webrtc:latest
    container_name: mxl2webrtc
    hostname: mxl2webrtc
    domainname: local
    ports:
      - "9601:9600"   # FastAPI serves both API and React frontend; change host port freely
    volumes:
      - type: volume
        source: mxl-domain
        target: /mxl-domain
    environment:
      - MXL_DOMAIN=/mxl-domain
      - MEDIAMTX_RTSP_URL=rtsp://mediamtx:8554/mxl2webrtc
      - MEDIAMTX_WEBRTC_URL=http://localhost:8889
    depends_on:
      - mediamtx
  ```

  **mediamtx service:**
  ```yaml
  mediamtx:
    platform: linux/amd64
    image: bluenviron/mediamtx:latest
    container_name: mediamtx
    hostname: mediamtx
    domainname: local
    ports:
      - "8889:8889"   # WebRTC WHEP/WHIP
      - "8554:8554"   # RTSP ingest from GStreamer
      - "8888:8888"   # HLS (optional fallback)
    network_mode: host  # required for WebRTC ICE candidate discovery
  ```

  > ⚠️ MediaMTX requires `network_mode: host` so that its WebRTC ICE candidates reflect the real host IP. When using `network_mode: host`, `ports:` mappings are ignored — the ports are bound directly on the host. The GStreamer container must reach MediaMTX via the host IP or bridge gateway, so set `MEDIAMTX_RTSP_URL=rtsp://host.docker.internal:8554/mxl2webrtc` or use the host bridge IP (`172.17.0.1`) if `host.docker.internal` is not available.

- The build context is the **repository root** (`..` relative to `./gst-apps/`). All `COPY` paths in the Dockerfile are relative to the repository root (e.g. `COPY gst-apps/mxl2webrtc/backend/ /app/backend/`).
- The Dockerfile uses **two stages**: a `node:18-bullseye-slim` stage to build the React frontend, and an `ubuntu:24.04` runtime stage. There is **no NMOS stage**.
- Add port mapping `9601:9600` only — FastAPI serves both the API and React frontend on the same port. The host-side port (`9601`) can be changed freely in `docker-compose.yml`.

**Stage 1 (Frontend Builder):**
```dockerfile
FROM node:18-bullseye-slim AS frontend-builder
WORKDIR /build
COPY gst-apps/mxl2webrtc/frontend/package.json \
     gst-apps/mxl2webrtc/frontend/package-lock.json* ./
RUN npm install --legacy-peer-deps
COPY gst-apps/mxl2webrtc/frontend/ ./
COPY gst-apps/logo/rgb_cbc-radio-canada-col-coul.png ./public/cbc-logo.png
RUN npm run build
```

**Stage 2 (Runtime on Ubuntu 24.04):**
- Base image: strictly `ubuntu:24.04`.
- Install native runtime packages via `apt-get`: `python3`, `python3-pip`, `python3-gi`, `python3-gi-cairo`, `gir1.2-gstreamer-1.0`, `gir1.2-gst-plugins-base-1.0`, `gstreamer1.0-tools`, `gstreamer1.0-plugins-base`, `gstreamer1.0-plugins-good`, `gstreamer1.0-plugins-bad`, `gstreamer1.0-plugins-ugly`, `gstreamer1.0-libav`, `gstreamer1.0-nice`, and `curl`.
- Install Python backend dependencies globally:
  ```dockerfile
  RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt
  ```
- Copy the `mxl-info` binary and MXL shared libraries (same pattern as `mxl-info-gui`):
  ```dockerfile
  COPY dmf-mxl/build/Linux-Clang-Release/tools/mxl-info/mxl-info /opt/mxl/tools/mxl-info/mxl-info
  RUN chmod +x /opt/mxl/tools/mxl-info/mxl-info

  COPY dmf-mxl/build/Linux-Clang-Release/lib/libmxl.so.1.1 /opt/mxl/lib/libmxl.so.1.1
  RUN cd /opt/mxl/lib \
   && ln -sf libmxl.so.1.1 libmxl.so.1 \
   && ln -sf libmxl.so.1 libmxl.so \
   && ldconfig /opt/mxl/lib /usr/lib/x86_64-linux-gnu/gstreamer-1.0 \
   && mkdir -p /workspace/mxl/build/Linux-Clang-Release/lib \
   && ln -sf /opt/mxl/lib/libmxl.so /workspace/mxl/build/Linux-Clang-Release/lib/libmxl.so
  ```
- Copy `libgstmxl.so` to the default GStreamer plugin path:
  ```dockerfile
  COPY dmf-mxl/build/Linux-Clang-Release/lib/gstreamer-1.0/libgstmxl.so \
       /usr/lib/x86_64-linux-gnu/gstreamer-1.0/libgstmxl.so
  ```
- Set environment variables:
  ```dockerfile
  ENV LD_LIBRARY_PATH=/opt/mxl/lib
  ```
- Copy the compiled frontend and backend:
  ```dockerfile
  COPY --from=frontend-builder /build/dist /app/frontend/dist
  COPY gst-apps/mxl2webrtc/backend/ /app/backend/
  ```
- Expose port `9600`.

### Step 2: FastAPI & GStreamer Backend

Create a FastAPI application at `backend/main.py` running on **port 9600**.

**Configuration:**
```python
MXL_INFO_BIN     = "/opt/mxl/tools/mxl-info/mxl-info"
MXL_DOMAIN_ROOT  = os.environ.get("MXL_DOMAIN", "/mxl-domain")
MEDIAMTX_RTSP    = os.environ.get("MEDIAMTX_RTSP_URL", "rtsp://localhost:8554/mxl2webrtc")
MEDIAMTX_WEBRTC  = os.environ.get("MEDIAMTX_WEBRTC_URL", "http://localhost:8889")

# FastAPI runs on port 9600 inside the container; the host port is set in docker-compose
```

**Domain and flow scanning** — reuse the same logic as `mxl-info-gui`:
- `get_domains()`: scan `/mxl-domain` recursively for `domain_def.json`; store UUID and directory path. Called once at startup.
- `scan_domain(domain_path)`: call `mxl-info -d <domain_path>` via `subprocess.run`, parse the output using a UUID-anchored regex, return a list of flows with `flow_uuid`, `flow_label`, `flow_grouphint`, and `description` (read from `<uuid>.mxl-flow/flow_def.json`).
- Parsing rules are identical to `mxl-info-gui` — see that app's instructions for the full regex and edge-case handling (empty group name, missing role, URL line disambiguation).

**GStreamer pipeline class (`GstReceiver`):**
- Do **not** start the pipeline at `__init__` — only when `start(config)` is called.
- Build the pipeline string dynamically based on `config.video_flow_uuid` and `config.audio_flow_uuid`:

  ```python
  # Video-only branch (included when video_flow_uuid is not None)
  video_branch = f"""
      mxlsrc name=vsrc flow-id="{video_uuid}" domain="{domain_path}"
      ! capsfilter caps="video/x-raw,format=v210"
      ! videoconvert
      ! x264enc tune=zerolatency speed-preset=ultrafast key-int-max=30
      ! rtspclientsink name=sink location="{MEDIAMTX_RTSP}" protocols=tcp
  """

  # Audio branch (included when audio_flow_uuid is not None)
  audio_branch = f"""
      mxlsrc name=asrc flow-id="{audio_uuid}" domain="{domain_path}"
      ! capsfilter caps="audio/x-raw,format=F32LE,layout=interleaved"
      ! audioconvert
      ! opusenc
      ! sink.
  """
  ```

  When both video and audio are selected, the audio branch links into the same `rtspclientsink` as the video branch (`! sink.`). When only audio is selected, `rtspclientsink` is named `sink` at the end of the audio branch instead. When only video is selected, the audio branch is omitted entirely.

- Use `Gst.parse_launch()` to build the pipeline.
- On `start()`: set pipeline to PLAYING; monitor the GStreamer bus for errors in a background thread.
- On `stop()`: set pipeline to NULL and release it.

**API endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/config` | Return `{"mediamtx_webrtc_url": MEDIAMTX_WEBRTC}` for frontend use |
| `POST` | `/get-domains` | Trigger a fresh domain scan; return updated domain list |
| `GET`  | `/domains` | Return the cached domain list |
| `GET`  | `/scan-domain?domain_path=<path>` | Run `mxl-info -d` and return parsed flow list |
| `POST` | `/pipeline/start` | Accept `domain_path`, `video_flow_uuid` (nullable), `audio_flow_uuid` (nullable); build and start pipeline |
| `POST` | `/pipeline/stop` | Stop and tear down the pipeline |
| `GET`  | `/pipeline/status` | Return `{"running": bool, "video_flow_uuid": str|null, "audio_flow_uuid": str|null, "mode": "video+audio"|"video"|"audio"|"stopped"}` |

- Serve the React static files as the last statement:
  ```python
  app.mount("/", StaticFiles(directory="/app/frontend/dist", html=True), name="static")
  ```
- `aiofiles` must be in `requirements.txt`.
- Call `get_domains()` in the FastAPI `startup` event.

### Step 3: React + Vite Frontend

Initialize a React + Vite project inside the `frontend/` directory targeting Vite 5 and React 18.

**Header branding:** At the very top of the page, display the CBC Radio-Canada logo (`/cbc-logo.png`) inline beside the "MXL to WebRTC" h1 title. Reference it as `<img src="/cbc-logo.png" />` with `height: 2.2rem`. The logo and title must share a flex row (`display: flex; align-items: center; gap: 1rem`).

**Dark theme** consistent with `./gst-apps/test-generator` (background `#0f0f0f`, section cards `#1c1c1c`).

**Setup section** (always visible, disabled while pipeline is running):
- MXL domain dropdown (populated from `GET /domains`; refresh button calls `POST /get-domains`).
- Video flow dropdown (populated from `GET /scan-domain?domain_path=...` when domain changes). First option is **"None — video disabled"**.
- Audio flow dropdown (same source as video). First option is **"None — audio disabled"**.
- Start/Stop button — disabled when both flow selectors are "None". Label changes to "Stop" while running.
- Always guard API responses with `Array.isArray(d) ? d : []` before calling `.map()` on flow lists.

**Operation section** (greyed-out until pipeline is running):
- Pipeline status badge (green "Running" / grey "Stopped"). Polled every 2 seconds via `GET /pipeline/status`.
- MXL input status row: video UUID + label and audio UUID + label (or "—" when not active).
- **WebRTC player panel:**
  - On mount, fetch `GET /config` to get `mediamtx_webrtc_url`.
  - When the pipeline starts (status transitions to running), initiate a WHEP connection to `{mediamtx_webrtc_url}/mxl2webrtc/whep`:
    1. Create `RTCPeerConnection` with no ICE servers (MediaMTX uses host-local addresses).
    2. Add `recvonly` transceivers for video and/or audio based on the active mode.
    3. Create an SDP offer, `setLocalDescription`.
    4. POST the SDP offer as `application/sdp` to the WHEP URL; receive the SDP answer.
    5. `setRemoteDescription` with the answer.
    6. Attach the resulting `MediaStream` to a `<video autoPlay playsInline muted>` element.
  - When the pipeline stops, close the peer connection and clear the `<video>` `srcObject`.

**`vite.config.js`** — proxy all API paths to `http://localhost:9600` for local development:
```js
proxy: {
  '/config': 'http://localhost:9600',
  '/domains': 'http://localhost:9600',
  '/get-domains': 'http://localhost:9600',
  '/scan-domain': 'http://localhost:9600',
  '/pipeline': 'http://localhost:9600',
}
```

In `App.jsx`, set `const API = ""` so all fetch calls use relative paths (e.g. `${API}/pipeline/status`).

**Entrypoint** — single process, single port:
```bash
cd /app
exec python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 9600
```

Please write the necessary Dockerfile, Python backend scripts, React components, `docker-compose.yml` additions, and integration code at `./gst-apps/mxl2webrtc/`, modifying content already present.
