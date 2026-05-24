# AI Agent Instructions: MXL to WebRTC Gateway

## Project Overview
Build a Dockerized media application that functions as an MXL receiver and WebRTC output gateway. The application uses GStreamer for media processing, FastAPI for the backend API, and React + Vite for the web frontend. It is a **receiver-only** application — it takes one MXL video flow and one MXL audio flow as inputs and publishes them as a low-latency WebRTC stream viewable directly from the browser. There is no NMOS control. Keep code style and patterns consistent with `./gst-apps/test-generator` and `./gst-apps/mxl-info-gui`.

## System Architecture
The application runs as a **single service on one port** inside Docker:
1. **FastAPI (port 9600):** Serves both the REST API and the built React static files (mounted via `StaticFiles`). The `StaticFiles` mount is added **last**, after all API routes, so API paths take precedence.
2. **Frontend:** Built with React + Vite into a `dist/` directory. No separate static file server — FastAPI serves `dist/` directly via `fastapi.staticfiles.StaticFiles`.
3. **Media Engine:** GStreamer via `gi.repository.Gst` Python bindings. The pipeline reads MXL flows via `mxlsrc`, encodes video with `x264enc` and audio with `opusenc`, then publishes directly to **MediaMTX** using the **WHIP protocol** (`webrtcbin` + Python WHIP handshake via `urllib.request`). MediaMTX does a near-passthrough to WebRTC, keeping Opus audio end-to-end with no AAC transcode. Note: `rtspclientsink` is **not** available in standard Ubuntu 24.04 packages and must not be used. `whipsink` from gst-plugins-rs requires a Cargo build stage and is **not** used — use `webrtcbin` instead.
4. **MediaMTX service:** A separate Docker service (`bluenviron/mediamtx:latest`) that ingests the GStreamer WHIP stream and re-serves it as WebRTC via its WHEP endpoint. It must use `network_mode: host` so its WebRTC ICE candidates reflect the real host IP.

This single-port design means the browser always uses the **same origin** for both the UI and the API, so no port number is hardcoded in the frontend JavaScript. The docker-compose host-port mapping (e.g. `9601:9600`) can be changed freely without rebuilding the image.

## Environment & File Specifications
- **MXL src documentation:** `./dmf-mxl/rust/gst-mxl-rs/readme.md` — authoritative reference for `mxlsrc` properties.
- **mxlsrc properties:** Use `video-flow-id` for video sources and `audio-flow-id` for audio sources (not `flow-id`). Set exactly one flow-id property per `mxlsrc` instance. `domain` is always required.
- **MXL domain root:** `/mxl-domain` (mounted Docker volume, shared with other services).
- **MXL-info binary:** `/opt/mxl/tools/mxl-info/mxl-info` — used for flow discovery (same as `mxl-info-gui`).
  > ⚠️ In the build tree the tool path is `./dmf-mxl/build/Linux-Clang-Release/tools/mxl-info/mxl-info` — the directory and the executable share the same name.
- **libgstmxl.so build path:** `./dmf-mxl/rust/target/release/libgstmxl.so` (compiled from the Rust GStreamer plugin source).
- **mxlsrc capsfilter requirements:** A `capsfilter` with `video/x-raw,format=v210` **must** be placed immediately after every MXL video source, and a `capsfilter` with `audio/x-raw,format=F32LE,layout=interleaved` **must** be placed immediately after every MXL audio source. Without these, auto-negotiation may land on an incompatible format.
- **Signaling / relay:** GStreamer publishes to MediaMTX via WHIP (`http://host.docker.internal:8889/mxl2webrtc/whip`). The browser receives from `http://<host>:8889/mxl2webrtc/whep`. The `mxl2webrtc` container reaches the host-networked MediaMTX via `extra_hosts: host.docker.internal:host-gateway`.
- **Mode support:** The pipeline must support **video + audio**, **video only**, or **audio only**, depending on which flows the user selects in the Setup section. A mode with no flows selected must not be startable.

## Required API & UI Functionalities

The UI is divided into two distinct sections: **Setup** and **Operation**.

---

### Section 1 — Setup

This section is used to configure and select the MXL input flows before starting the GStreamer pipeline. The pipeline does **not** start until the user clicks the **Start** button. All controls in this section are disabled while the pipeline is running.

1. **MXL Domain Selector:** Scan `/mxl-domain` recursively for `domain_def.json` files. Read the `id` field for the domain UUID and use the containing directory path as the domain path. Provide a dropdown to select the target MXL domain. Changing the domain resets both flow selectors.
2. **Video Flow Selector:** A dropdown populated from the flow list for the selected domain. Each option displays the first 8 characters of the UUID, description, label, and group hint. Includes a **"None"** option at the top — selecting "None" means no video input (audio-only mode). **Only show flows that have "video" (case-insensitive) after `:` in their `flow_grouphint`.**
3. **Audio Flow Selector:** A dropdown populated from the same domain flow list, following the same display format. Includes a **"None"** option at the top — selecting "None" means no audio input (video-only mode). **Only show flows that have "audio" (case-insensitive) after `:` in their `flow_grouphint`.**
4. **Refresh Flow List button** — manually triggers `scan_domain` for the selected domain.
5. **Start / Stop button** — starts the GStreamer pipeline with the selected flows. At least one flow (video or audio) must be selected for the Start button to be enabled. Changes to a **Stop** button while the pipeline is running.

> ℹ️ Both flow selectors are populated using the same domain-scanning and parsing logic as `mxl-info-gui` — calling `mxl-info -d <domain_path>` and parsing the output. Only active flows (those reported by `mxl-info`) are shown.

---

### Section 2 — Operation

This section is enabled only once the pipeline is running (greyed-out and non-interactive otherwise).

1. **MXL Input Status:** Displays the active video flow UUID (or "—" if video is not active) and the active audio flow UUID (or "—" if audio is not active), each with a coloured presence dot.
2. **WebRTC Player:** An embedded HTML5 `<video>` element that receives the low-latency WebRTC stream from MediaMTX using the WHEP protocol. The player connects to `<mediamtx_webrtc_url>/mxl2webrtc/whep` — where `mediamtx_webrtc_url` is returned by the `/config` endpoint. The player starts automatically when the pipeline starts and stops when it stops.

---

## Step-by-Step Implementation Guide

### Step 1: Docker Setup

- Use `./gst-apps/docker-compose.yml` as a baseline.
- The `mxl-domain` named volume is defined in `docker-compose.yml` and shared by all services.
- Add the following two services to `docker-compose.yml`:

  **mediamtx service:**
  ```yaml
  mediamtx:
    platform: linux/amd64
    image: bluenviron/mediamtx:latest
    container_name: mediamtx
    hostname: mediamtx
    network_mode: host   # required for WebRTC ICE candidate discovery
  ```

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
      - MEDIAMTX_WHIP_URL=http://host.docker.internal:8889/mxl2webrtc/whip
      - MEDIAMTX_WEBRTC_URL=http://localhost:8889
    extra_hosts:
      - "host.docker.internal:host-gateway"
    depends_on:
      - mediamtx
  ```

  > ⚠️ MediaMTX requires `network_mode: host` so that its WebRTC ICE candidates reflect the real host IP. When using `network_mode: host`, `ports:` mappings are ignored — the ports are bound directly on the host. The `mxl2webrtc` bridge container reaches MediaMTX via `host.docker.internal` (resolved to the host gateway via `extra_hosts`).

- The build context is the **repository root** (`..` relative to `./gst-apps/`). All `COPY` paths in the Dockerfile are relative to the repository root (e.g. `COPY gst-apps/mxl2webrtc/backend/ /app/backend/`).
- The Dockerfile uses **two stages**: a `node:18-bullseye-slim` stage to build the React frontend, and an `ubuntu:24.04` runtime stage. There is no Cargo/Rust build stage — `webrtcbin` is used instead of `whipsink`.
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
- Install native runtime packages via `apt-get`:
  ```
  python3 python3-pip python3-gi python3-gi-cairo
  gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 gir1.2-gst-plugins-bad-1.0
  gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good
  gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly gstreamer1.0-libav
  gstreamer1.0-nice gstreamer1.0-x curl
  ```
  > ⚠️ `gir1.2-gst-plugins-bad-1.0` is required for `GstWebRTC` and `GstSdp` Python typelibs used by the `webrtcbin` WHIP handshake.
- Install Python backend dependencies globally:
  ```dockerfile
  RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt
  ```
- Copy the MXL GStreamer plugin and shared libraries:
  ```dockerfile
  COPY dmf-mxl/rust/target/release/libgstmxl.so \
       /usr/lib/x86_64-linux-gnu/gstreamer-1.0/libgstmxl.so

  COPY dmf-mxl/build/Linux-Clang-Release/lib/libmxl.so.1.1 /opt/mxl/lib/libmxl.so.1.1
  RUN cd /opt/mxl/lib \
   && ln -sf libmxl.so.1.1 libmxl.so.1 \
   && ln -sf libmxl.so.1 libmxl.so \
   && ldconfig /opt/mxl/lib /usr/lib/x86_64-linux-gnu/gstreamer-1.0 \
   && mkdir -p /workspace/mxl/build/Linux-Clang-Release/lib \
   && ln -sf /opt/mxl/lib/libmxl.so /workspace/mxl/build/Linux-Clang-Release/lib/libmxl.so
  ```
- Copy the `mxl-info` binary:
  ```dockerfile
  COPY dmf-mxl/build/Linux-Clang-Release/tools/mxl-info/mxl-info /opt/mxl/tools/mxl-info/mxl-info
  RUN chmod +x /opt/mxl/tools/mxl-info/mxl-info
  ```
- Set environment variables and expose port:
  ```dockerfile
  ENV LD_LIBRARY_PATH=/opt/mxl/lib
  ENV MXL_DOMAIN=/mxl-domain
  ENV MEDIAMTX_WHIP_URL=http://host.docker.internal:8889/mxl2webrtc/whip
  ENV MEDIAMTX_WEBRTC_URL=http://localhost:8889
  EXPOSE 9600
  ```
- Copy the compiled frontend and backend:
  ```dockerfile
  COPY gst-apps/mxl2webrtc/backend/ /app/backend/
  COPY --from=frontend-builder /build/dist /app/frontend/dist
  ```

### Step 2: FastAPI & GStreamer Backend

Create a FastAPI application at `backend/main.py` running on **port 9600**.

**Configuration:**
```python
MXL_INFO_BIN     = "/opt/mxl/tools/mxl-info/mxl-info"
MXL_DOMAIN_ROOT  = os.environ.get("MXL_DOMAIN", "/mxl-domain")
MEDIAMTX_WEBRTC  = os.environ.get("MEDIAMTX_WEBRTC_URL", "http://localhost:8889")
```

**Domain and flow scanning** — reuse the same logic as `mxl-info-gui`:
- `scan_domains()`: scan `/mxl-domain` recursively for `domain_def.json`; store UUID and directory path. Called once at startup.
- `scan_domain_path(domain_path)`: call `mxl-info -d <domain_path>` via `subprocess.run`, parse the output using a UUID-anchored regex, return a list of flows with `flow_uuid`, `flow_label`, `flow_grouphint`, and `description` (read from `<uuid>.mxl-flow/flow_def.json`).
- Parsing rules are identical to `mxl-info-gui` — see that app's instructions for the full regex and edge-case handling (empty group name, missing role, URL line disambiguation).

**GStreamer pipeline class (`GstReceiver`) in `backend/gst_mxl2webrtc.py`:**

The pipeline is built programmatically (not via `parse_launch`) using `webrtcbin` with a Python WHIP handshake. This avoids needing `whipsink` from gst-plugins-rs.

- Do **not** start the pipeline at `__init__` — only when `start(domain_path, video_flow_uuid, audio_flow_uuid)` is called.
- Imports required:
  ```python
  gi.require_version("Gst", "1.0")
  gi.require_version("GstWebRTC", "1.0")
  gi.require_version("GstSdp", "1.0")
  from gi.repository import GLib, Gst, GstSdp, GstWebRTC
  ```
- Run a `GLib.MainLoop` in a daemon thread so GStreamer bus messages and signals are dispatched.
- Pipeline construction:
  1. Create `webrtcbin` with `bundle-policy = MAX_BUNDLE`.
  2. For video: chain `mxlsrc(video-flow-id) → capsfilter(v210) → videoconvert → x264enc(zerolatency/ultrafast/key-int-max=30) → h264parse → rtph264pay(pt=96) → queue → webrtcbin sink pad`.
  3. For audio: chain `mxlsrc(audio-flow-id) → capsfilter(F32LE) → audioconvert → audioresample → opusenc → rtpopuspay(pt=97) → queue → webrtcbin sink pad`. The `audioresample` element is required because Opus only accepts 8/12/16/24/48 kHz — without it a 96 kHz or 44.1 kHz source produces no audio.
  4. Use `webrtcbin.request_pad_simple("sink_%u")` to obtain sink pads; link queue src pads to them.

- **WHIP handshake** (triggered by `on-negotiation-needed` signal):
  1. Connect `on-negotiation-needed` signal **before** setting pipeline to PLAYING.
  2. In the signal handler, call `create-offer` with a **lambda closure** promise callback — do **not** pass `webrtcbin` as `user_data` to `Gst.Promise.new_with_change_func` as PyGObject does not forward it correctly. Use `lambda p: self._on_offer_created(p, webrtcbin)` instead.
  3. In `_on_offer_created`: call `set-local-description` with a plain `Gst.Promise.new()` (do **not** call `wait()` on the promise inside a GLib callback — it can block). Then start a background thread to wait for ICE.
  4. In the ICE-wait thread: poll `webrtcbin.get_property("ice-gathering-state")` every 100 ms until `GstWebRTC.WebRTCICEGatheringState.COMPLETE` or a 10-second timeout. Then read `webrtcbin.get_property("local-description").sdp.as_text()` to get the full SDP with inlined ICE candidates.
  5. POST the SDP offer as `Content-Type: application/sdp` to `MEDIAMTX_WHIP_URL` using `urllib.request`. Expect HTTP 200 or 201 with the SDP answer in the response body.
  6. Parse the answer with `GstSdp.SDPMessage.new_from_text()`, wrap in `GstWebRTC.WebRTCSessionDescription.new(ANSWER, ...)`, and call `set-remote-description`.

- Wrap all GLib/GStreamer callbacks in `try/except` with `log.error(..., exc_info=True)` — GLib silently swallows Python exceptions raised inside signal handlers.
- On `stop()`: set pipeline to NULL, wait for state change, clear references, `gc.collect()`.

**API endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/config` | Return `{"mediamtx_webrtc_url": MEDIAMTX_WEBRTC}` for frontend use |
| `POST` | `/get-domains` | Trigger a fresh domain scan; return updated domain list |
| `GET`  | `/domains` | Return the cached domain list |
| `GET`  | `/scan-domain?domain_path=<path>` | Run `mxl-info -d` and return parsed flow list |
| `POST` | `/pipeline/start` | Accept `domain_path`, `video_flow_uuid` (nullable), `audio_flow_uuid` (nullable); build and start pipeline |
| `POST` | `/pipeline/stop` | Stop and tear down the pipeline |
| `GET`  | `/pipeline/status` | Return `{"running": bool, "video_flow_uuid": str\|null, "audio_flow_uuid": str\|null, "mode": "video+audio"\|"video"\|"audio"\|"stopped", "error": str\|null}` |

- Serve the React static files as the last statement:
  ```python
  app.mount("/", StaticFiles(directory="/app/frontend/dist", html=True), name="static")
  ```
- `aiofiles` must be in `requirements.txt`.
- Call `scan_domains()` in the FastAPI `startup` event.

### Step 3: React + Vite Frontend

Initialize a React + Vite project inside the `frontend/` directory targeting Vite 5 and React 18.

**Header branding:** At the very top of the page, display the CBC Radio-Canada logo (`/cbc-logo.png`) inline beside the "MXL to WebRTC" h1 title. Reference it as `<img src="/cbc-logo.png" />` with `height: 2.2rem`. The logo and title must share a flex row (`display: flex; align-items: center; gap: 1rem`).

**Dark theme** consistent with `./gst-apps/test-generator` (background `#0f0f0f`, section cards `#1c1c1c`).

**Setup section** (always visible, disabled while pipeline is running):
- MXL domain dropdown (populated from `GET /domains`; refresh button calls `POST /get-domains`).
- Video flow dropdown (populated from `GET /scan-domain?domain_path=...` when domain changes). First option is **"None — video disabled"**. Only include flows where the part after `:` in `flow_grouphint` contains "video" (case-insensitive).
- Audio flow dropdown (same source as video). First option is **"None — audio disabled"**. Only include flows where the part after `:` in `flow_grouphint` contains "audio" (case-insensitive).
- Start/Stop button — disabled when both flow selectors are "None". Label changes to "Stop" while running.
- Always guard API responses with `Array.isArray(d) ? d : []` before calling `.map()` on flow lists.

**Operation section** (greyed-out until pipeline is running):
- Pipeline status badge (green "● RUNNING" / grey "○ STOPPED").
- MXL input status row: video UUID and audio UUID (or "—" when not active), each with a coloured presence dot.
- **WebRTC player panel:**
  - On mount, fetch `GET /config` to get `mediamtx_webrtc_url`.
  - When the pipeline starts (status transitions to running), initiate a WHEP connection to `{mediamtx_webrtc_url}/mxl2webrtc/whep`:
    1. Create `RTCPeerConnection({ iceServers: [] })`.
    2. Add `recvonly` transceivers for video and audio.
    3. Create an SDP offer, `setLocalDescription`.
    4. Wait for ICE gathering to complete (or 5-second timeout) before sending.
    5. POST the SDP offer as `application/sdp` to the WHEP URL; receive the SDP answer (201).
    6. `setRemoteDescription` with the answer.
    7. Attach the resulting `MediaStream` to a `<video autoPlay playsInline muted>` element.
  - Retry up to 12 times at 2-second intervals (MediaMTX may not have the stream immediately after the pipeline starts). Start with a 1.5-second initial delay to allow the WHIP handshake to complete first.
  - When the pipeline stops, close the peer connection and clear the `<video>` `srcObject`.
  - Show player state (`connecting…` / `● LIVE` / error message) below the video element.
  - The `<video>` element must start with `muted` set to allow browser autoplay. Once the player reaches "playing" state, display a **Mute / Unmute** button in the player header that toggles `videoRef.current.muted` and reflects the current state.

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
#!/bin/bash
set -e
cd /app
exec python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 9600
```

## Known Pitfalls

| Issue | Root cause | Fix |
|-------|-----------|-----|
| `no property "flow-id" in element "mxlsrc"` | `mxlsrc` has no `flow-id` property | Use `video-flow-id` for video sources and `audio-flow-id` for audio sources |
| `no element "rtspclientsink"` | Not in Ubuntu 24.04 packages | Use `webrtcbin` + WHIP or RTMP (`flvmux` + `rtmpsink`) instead |
| `no element "whipsink"` | `libgstrswebrtc.so` requires a Cargo build stage (slow, fragile) | Use `webrtcbin` + Python WHIP handshake; no Rust build needed |
| Promise callback never fires | `Gst.Promise.new_with_change_func(func, user_data)` does not forward `user_data` correctly in PyGObject | Use `lambda p: callback(p, captured_var)` instead of passing `user_data` |
| WHEP returns 404 | WHIP handshake failed silently (callback exception swallowed by GLib) | Wrap all GLib callbacks in `try/except`; use lambda closures for promise callbacks |
| `set_promise.wait()` blocks | Calling `wait()` inside a GLib promise callback can deadlock | Pass `Gst.Promise.new()` to `set-local-description` and never call `wait()` on it inside a callback |
| No audio in browser despite green status dot | `<video muted>` permanently silences audio; browser autoplay blocks unmuted streams | Start the video element muted, then show a Mute/Unmute button once playing so the user can enable audio |
| No audio from Opus even though pipeline starts | `opusenc` only accepts 8/12/16/24/48 kHz; a 96 kHz or 44.1 kHz MXL source fails to negotiate | Add `audioresample` between `audioconvert` and `opusenc` in the audio branch |
