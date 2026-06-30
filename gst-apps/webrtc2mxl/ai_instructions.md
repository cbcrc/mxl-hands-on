# AI Agent Instructions: WebRTC to MXL Gateway

## Project Overview
Build a Dockerized media application that functions as a **WebRTC microphone ingest and MXL
audio producer**. It is the **reverse** of the sibling `./gst-apps/mxl2webrtc` app: instead of
reading MXL flows and publishing WebRTC, it captures the **browser microphone**, transports it as
**Opus over WebRTC** through **MediaMTX**, and writes it into an **MXL audio flow** on the shared
domain. The application uses GStreamer for media processing, FastAPI for the backend API, and
React + Vite for the web frontend. It is a **sender-only, audio-only** application — there is no
video, no closed-caption/SCTE handling, no encoder-settings UI, and no NMOS control. Keep code
style and patterns consistent with `./gst-apps/hls2mxl` (the MXL producer reference) and
`./gst-apps/mxl2webrtc` (the WebRTC/FastAPI/React reference).

## System Architecture
The application runs as a **single service on one port** inside Docker, alongside the shared
`mediamtx` service.

1. **FastAPI (port 9600):** Serves both the REST API and the built React static files (mounted
   via `StaticFiles`). The `StaticFiles` mount is added **last**, after all API routes, so API
   paths take precedence. This single-port design means the browser uses the **same origin** for
   the UI and the API, so no port number is hardcoded in the frontend JavaScript; the
   docker-compose host-port mapping (`9606:9600`) can change freely without rebuilding.
2. **Frontend:** Built with React + Vite into a `dist/` directory and served by FastAPI. The
   browser captures the microphone (`getUserMedia`) and **publishes** it to MediaMTX via the
   **WHIP** protocol (standard browser-offers, send-only Opus).
3. **Media Engine:** GStreamer via `gi.repository.Gst` Python bindings. The pipeline **pulls**
   the published stream from MediaMTX via the **WHEP** protocol (`webrtcbin` as a WHEP consumer +
   a Python WHEP handshake via `urllib.request`), decodes Opus, converts to MXL audio caps, and
   writes the flow via **`mxlsink`**.
4. **MediaMTX service:** A separate Docker service (`bluenviron/mediamtx:latest`, already present
   in `docker-compose.yml`) that **ingests** the browser's WHIP stream and **re-serves** it via
   its WHEP endpoint. It uses `network_mode: host` so its WebRTC ICE candidates reflect the real
   host IP. **Reuse the existing service unchanged.**

### Data flow

```
Browser mic ──getUserMedia(audio)──▶ RTCPeerConnection (sendonly Opus)
        │  WHIP POST  (standard browser-offers)
        ▼
   MediaMTX  http://<host>:8889/webrtc2mxl/whip      (network_mode: host)
        │  WHEP        (GStreamer pulls)
        ▼
 webrtc2mxl container (FastAPI :9600 + GStreamer)
   webrtcbin (WHEP client, recvonly Opus)
     → rtpopusdepay → opusdec → audioconvert → audioresample
     → capsfilter "audio/x-raw,format=F32LE,rate=48000,layout=interleaved"
     → queue → mxlsink(flow-id=<uuid>, domain=<path>, sync=False)
        ▼
   MXL audio flow written into the shared /mxl-domain volume
```

Both ends are **clients** of MediaMTX. The browser publishes WHIP; the GStreamer app is a WHEP
consumer (its `webrtcbin` is the **offerer**, recvonly). The app dials **out** to MediaMTX via
`host.docker.internal:8889`, exactly like mxl2webrtc's MediaMTX-relay mode — so **no inbound UDP
port range, no ICE port pinning, and no host-candidate rewriting are needed**. (That complexity
existed only for mxl2webrtc's Direct-WHEP server mode, which this app does not have.)

## What this app deliberately does NOT have
Do **not** build any of the following (they exist in mxl2webrtc but are out of scope here):
video branch; H.264/x264 encoder settings and `/encoder-defaults`; ancillary captions/SCTE side
pipeline; the `rscc-builder` (gst-plugin-closedcaption) Dockerfile stage; a Direct-WHEP server
mode (`POST/DELETE /whep`, `WEBRTC_ICE_PORT_MIN/MAX`, candidate rewriting); the `8200-8210/udp`
port mapping; the `mxl-info` binary and flow-listing (this app **creates** a flow, it does not
select from existing ones — domain selection only needs the `domain_def.json` walk).

## Environment & File Specifications
- **MXL plugin documentation:** `./dmf-mxl/rust/gst-mxl-rs/README.md` — authoritative reference
  for `mxlsink`/`mxlsrc`.
- **`mxlsink` properties (write side):** exactly two — **`flow-id`** (the flow UUID string) and
  **`domain`** (the domain directory path). It is a `GstBaseSink`, so it also accepts `sync`
  (set `sync=False`). There is **no** `audio-flow-id` property on `mxlsink` (that naming is
  `mxlsrc`-only); use `flow-id`.
- **`mxlsink` audio caps (required):** the buffer feeding `mxlsink` **must** be
  `audio/x-raw, format=F32LE, layout=interleaved`. `mxlsink` derives sample rate, channel count,
  and bit depth from the negotiated caps and **auto-creates** the flow directory and
  `flow_def.json` (media_type `audio/float32`, `bit_depth` 32). Pin `rate=48000`; **omit
  `channels`** so the flow inherits the microphone's native channel count.
- **One active writer per flow UUID:** `mxlsink` refuses a second writer on the same `flow-id`
  ("the UUID belongs to a flow with another active writer"). Never create two `mxlsink`s on the
  same id, and avoid pipeline shapes that re-emit CAPS (a second `set_caps`) on the same flow.
- **MXL domain root:** `/mxl-domain` (mounted Docker volume, shared with other services). A flow
  lives at `<domain>/<flow_uuid>.mxl-flow/flow_def.json`.
- **libgstmxl.so build path:** `./dmf-mxl/rust/target/release/libgstmxl.so` (compiled Rust
  GStreamer plugin). Copied into the GStreamer plugin dir by the Dockerfile.
- **Signaling URLs:**
  - GStreamer pulls from MediaMTX WHEP at `http://host.docker.internal:8889/webrtc2mxl/whep`
    (`MEDIAMTX_WHEP_URL`). The `webrtc2mxl` bridge container reaches the host-networked MediaMTX
    via `extra_hosts: host.docker.internal:host-gateway`.
  - The browser publishes to MediaMTX WHIP at `http://<host>:8889/webrtc2mxl/whip`. The backend
    `/config` endpoint returns `MEDIAMTX_WHIP_URL` (`http://localhost:8889/webrtc2mxl/whip`); the
    **frontend substitutes its own `window.location.hostname`** (keeping the port) so it works
    for both local and remote browsers.
- **Mode:** audio-only. A start with no microphone selected must not be startable.

## WHEP-client signalling (the one genuinely new mechanism)
`webrtcbin` is used as a **WHEP consumer** — the inverse of mxl2webrtc's WHIP push. The pipeline
is built programmatically (not `parse_launch`).
1. Create `webrtcbin` with `bundle-policy = MAX_BUNDLE` and `stun-server = ""`.
2. Add a **recvonly** Opus transceiver **before** offering, so the offer requests receive-audio:
   ```python
   caps = Gst.Caps.from_string(
       "application/x-rtp,media=audio,encoding-name=OPUS,clock-rate=48000,payload=111"
   )
   webrtcbin.emit("add-transceiver", GstWebRTC.WebRTCRTPTransceiverDirection.RECVONLY, caps)
   ```
   Adding the transceiver triggers `on-negotiation-needed`.
3. In `on-negotiation-needed`: call `create-offer` with a **lambda-closure** promise callback —
   do **not** pass `webrtcbin` as `user_data` to `Gst.Promise.new_with_change_func` (PyGObject
   does not forward it). Use `lambda p: self._on_offer_created(p, webrtcbin)`.
4. In `_on_offer_created`: call `set-local-description` with a plain `Gst.Promise.new()` (never
   call `wait()` on a promise inside a GLib callback — it can deadlock). Then start a background
   thread to wait for ICE.
5. In the ICE-wait thread: poll `webrtcbin.get_property("ice-gathering-state")` every 100 ms
   until `GstWebRTC.WebRTCICEGatheringState.COMPLETE` or a 10 s timeout. Read
   `webrtcbin.get_property("local-description").sdp.as_text()`.
6. POST the offer SDP as `Content-Type: application/sdp` to `MEDIAMTX_WHEP_URL`. **Retry with
   backoff** — MediaMTX returns **404 until the browser's WHIP publish is live**. On success
   (200/201) the body is the SDP answer.
7. Parse the answer with `GstSdp.SDPMessage.new_from_text()`, wrap in
   `GstWebRTC.WebRTCSessionDescription.new(ANSWER, ...)`, and call `set-remote-description`.
8. The incoming media arrives on a `webrtcbin` **`pad-added`** (src pad, `application/x-rtp`).
   In the handler, link the MXL write branch:
   ```
   rtpopusdepay → opusdec → audioconvert → audioresample
     → capsfilter "audio/x-raw,format=F32LE,rate=48000,layout=interleaved"
     → queue → mxlsink(flow-id=<uuid>, domain=<path>, sync=False)
   ```

Required imports and main loop:
```python
gi.require_version("Gst", "1.0")
gi.require_version("GstWebRTC", "1.0")
gi.require_version("GstSdp", "1.0")
from gi.repository import GLib, Gst, GstSdp, GstWebRTC
```
Run a `GLib.MainLoop` in a daemon thread so bus messages and signals are dispatched. Wrap **every**
GLib/GStreamer callback in `try/except` with `log.error(..., exc_info=True)` — GLib silently
swallows Python exceptions raised inside signal handlers.

## Required API & UI Functionalities

The UI is divided into **Setup** and **Operation**.

### Section 1 — Setup
Used to configure the capture before starting. The pipeline does **not** start until **Start** is
clicked. All controls are disabled while running.

1. **MXL Domain Selector:** Scan `/mxl-domain` recursively for `domain_def.json` files. Read the
   `id` (UUID), `label`, and `description`, and use the containing directory as the domain path.
   Dropdown displays the domain **`label`** (falling back to the directory path). Populated from
   `GET /domains`; a **Refresh** button calls `POST /get-domains`.
2. **Microphone Selector:** Enumerate audio input devices with
   `navigator.mediaDevices.enumerateDevices()` and present them in a dropdown. Pass the chosen
   `deviceId` to `getUserMedia({ audio: { deviceId } })`. Device labels and `deviceId`s are only
   populated after a `getUserMedia` permission grant, so **prime the permission once on mount**
   (request `getUserMedia({audio:true})`, stop the tracks, then re-enumerate). A specific mic is
   **not** required to start — when no `deviceId` is selected, fall back to `{ audio: true }` (the
   default input).
3. **Flow metadata inputs (match the other MXL-producer apps, e.g. `hls2mxl`):** a **Group Hint**
   text field (default `WEBRTC2MXL`), a **Label** field (default `webrtc-audio`), and a
   **Description** field (default `webrtc-audio-out`). The backend derives a **deterministic UUID**
   (`uuid.uuid5(_NS, f"{grouphint}:audio")`) from the group hint so restarts reuse the same flow
   directory, patches the flow's `grouphint` to `<grouphint>:Audio`, and writes the `label` and
   `description` into `flow_def.json`.
4. **Start / Stop button** — enabled when a domain is selected and group hint / label / description
   are non-empty. Changes to **Stop** while running.

### Section 2 — Operation
Enabled only while running (greyed-out otherwise).

1. **MXL Output Status:** the active flow **name** and **UUID**, with a coloured presence dot.
2. **Connection status:** publish/connection state text (`publishing…` / `● LIVE` / error).
3. **Mic level meter:** a small VU meter driven by the Web Audio API (`AnalyserNode` on the
   captured `MediaStream`) so the user can confirm the microphone is live — this is the only
   feedback in an audio-only app.
4. **Error banner:** if the backend pipeline failed to start (from `GET /pipeline/status`
   `error`), show it in an amber banner.

## Step-by-Step Implementation Guide

### Step 1: Docker Setup
- Use `./gst-apps/docker-compose.yml` as the baseline. The `mxl-domain` named volume and the
  `mediamtx` service already exist — **reuse `mediamtx` unchanged**.
- Add this service to `docker-compose.yml`:
  ```yaml
  webrtc2mxl:
    platform: linux/amd64
    build:
      context: ..
      dockerfile: gst-apps/webrtc2mxl/Dockerfile
    image: webrtc2mxl:latest
    container_name: webrtc2mxl
    hostname: webrtc2mxl
    domainname: local
    ports:
      - "9606:9600"   # next free host port; change freely
    volumes:
      - type: volume
        source: mxl-domain
        target: /mxl-domain
    environment:
      - MXL_DOMAIN=/mxl-domain
      - MEDIAMTX_WHEP_URL=http://host.docker.internal:8889/webrtc2mxl/whep
      - MEDIAMTX_WHIP_URL=http://localhost:8889/webrtc2mxl/whip  # /config; browser swaps hostname
    extra_hosts:
      - "host.docker.internal:host-gateway"
    depends_on:
      - mediamtx
  ```
  > ⚠️ No UDP port range — the app dials **out** to MediaMTX, so its `webrtcbin` only needs
  > outbound connectivity. ICE works because MediaMTX (host-networked) advertises real host-IP
  > candidates that the bridge container can reach.

- Build context is the **repository root** (`..` relative to `./gst-apps/`); all Dockerfile
  `COPY` paths are repo-root-relative (e.g. `COPY gst-apps/webrtc2mxl/backend/ /app/backend/`).
- The Dockerfile uses **two stages**: a `node:18-bullseye-slim` frontend builder and an
  `ubuntu:24.04` runtime stage. There is **no Cargo/`rscc-builder` stage** — `webrtcbin` and the
  MXL plugin are both prebuilt, and there are no closed captions.

**Stage 1 — Frontend builder:**
```dockerfile
FROM node:18-bullseye-slim AS frontend-builder
WORKDIR /build
COPY gst-apps/webrtc2mxl/frontend/package.json \
     gst-apps/webrtc2mxl/frontend/package-lock.json* ./
RUN npm install --legacy-peer-deps
COPY gst-apps/webrtc2mxl/frontend/ ./
COPY gst-apps/logo/rgb_cbc-radio-canada-col-coul.png ./public/cbc-logo.png
RUN npm run build
```

**Stage 2 — Runtime on Ubuntu 24.04:**
- Base image: strictly `ubuntu:24.04`.
- Install native runtime packages via `apt-get`:
  ```
  python3 python3-pip python3-gi python3-gi-cairo
  gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 gir1.2-gst-plugins-bad-1.0
  gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good
  gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly gstreamer1.0-libav
  gstreamer1.0-nice curl
  ```
  > ⚠️ `gir1.2-gst-plugins-bad-1.0` provides the `GstWebRTC`/`GstSdp` typelibs and the
  > `webrtcbin` element; `gstreamer1.0-nice` provides the ICE agent; `gstreamer1.0-plugins-good`
  > provides `rtpopusdepay`; Opus decode (`opusdec`) is in `gstreamer1.0-plugins-base`.
- Install Python deps globally:
  ```dockerfile
  COPY gst-apps/webrtc2mxl/backend/requirements.txt /tmp/requirements.txt
  RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt
  ```
- Copy the MXL GStreamer plugin and shared libraries:
  ```dockerfile
  COPY dmf-mxl/rust/target/release/libgstmxl.so \
       /usr/lib/x86_64-linux-gnu/gstreamer-1.0/libgstmxl.so

  COPY dmf-mxl/build/Linux-Clang-Release/lib/libmxl.so.1.2 /opt/mxl/lib/libmxl.so.1.2
  RUN cd /opt/mxl/lib \
   && ln -sf libmxl.so.1.2 libmxl.so.1 \
   && ln -sf libmxl.so.1 libmxl.so \
   && ldconfig /opt/mxl/lib /usr/lib/x86_64-linux-gnu/gstreamer-1.0 \
   && mkdir -p /workspace/mxl/build/Linux-Clang-Release/lib \
   && ln -sf /opt/mxl/lib/libmxl.so /workspace/mxl/build/Linux-Clang-Release/lib/libmxl.so
  ```
  > ⚠️ `libgstmxl.so` `dlopen()`s `libmxl.so` from its compile-time build path — the
  > `/workspace/mxl/build/...` symlink satisfies that. **No `mxl-info` copy** (not used).
- Environment and port:
  ```dockerfile
  ENV LD_LIBRARY_PATH=/opt/mxl/lib
  ENV MXL_DOMAIN=/mxl-domain
  ENV MEDIAMTX_WHEP_URL=http://host.docker.internal:8889/webrtc2mxl/whep
  ENV MEDIAMTX_WHIP_URL=http://localhost:8889/webrtc2mxl/whip
  EXPOSE 9600
  ```
- Copy app code and frontend, set entrypoint:
  ```dockerfile
  COPY gst-apps/webrtc2mxl/backend/ /app/backend/
  COPY --from=frontend-builder /build/dist /app/frontend/dist
  COPY gst-apps/webrtc2mxl/entrypoint.sh /entrypoint.sh
  RUN chmod +x /entrypoint.sh
  ENTRYPOINT ["/entrypoint.sh"]
  ```

### Step 2: FastAPI & GStreamer Backend

Create a FastAPI app at `backend/main.py` on **port 9600**.

**Configuration:**
```python
MXL_DOMAIN_ROOT = os.environ.get("MXL_DOMAIN", "/mxl-domain")
MEDIAMTX_WHIP   = os.environ.get("MEDIAMTX_WHIP_URL", "http://localhost:8889/webrtc2mxl/whip")
```
And in `backend/gst_webrtc2mxl.py`:
```python
MEDIAMTX_WHEP = os.environ.get("MEDIAMTX_WHEP_URL",
                               "http://localhost:8889/webrtc2mxl/whep")
```

**Domain scanning** — reuse the same logic as mxl2webrtc/hls2mxl:
- `scan_domains()`: walk `/mxl-domain` recursively for `domain_def.json`; store `id`, `label`,
  `description`, and directory path. Called once at startup (FastAPI `startup` event).

**GStreamer class (`GstWriter`) in `backend/gst_webrtc2mxl.py`:**
- Do **not** start at `__init__` — only when `start(domain_path, grouphint, label, description)` is
  called. Derive the flow UUID deterministically: `uuid.uuid5(_NS, f"{grouphint}:audio")` with a
  fixed module namespace constant (mirror hls2mxl's `_derive_uuids` / `_MXL_HLS_NS`).
- Build the WHEP-client pipeline as in "WHEP-client signalling" above. Set `mxlsink` `flow-id`
  and `domain`, `sync=False`.
- After the pipeline reaches PLAYING, **patch `flow_def.json`** in a background thread that polls
  `<domain>/<uuid>.mxl-flow/flow_def.json` (up to ~15 s, because `mxlsink` creates it lazily on
  the first buffer), then writes `grouphint` = `<grouphint>:Audio`, `label`, `description`, and
  `tags["urn:x-nmos:tag:grouphint/v1.0"] = [<grouphint>:Audio]`. Copy `_patch_flow_defs` from
  `./gst-apps/hls2mxl/backend/gst_hls2mxl.py` (audio-only).
- On `stop()`: set pipeline to NULL, wait for the state change, clear references, `gc.collect()`.

**API endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/config` | Return `{"mediamtx_whip_url": MEDIAMTX_WHIP}` for the frontend (browser swaps the hostname) |
| `POST` | `/get-domains` | Trigger a fresh domain scan; return the updated domain list |
| `GET`  | `/domains` | Return the cached domain list (each entry: `id`, `label`, `description`, `path`) |
| `POST` | `/pipeline/start` | Body `{domain_path, grouphint, label, description}`; derive the UUID, build and start the WHEP-client pipeline |
| `POST` | `/pipeline/stop` | Stop and tear down the pipeline |
| `GET`  | `/pipeline/status` | Return `{"running": bool, "flow_uuid": str\|null, "grouphint": str\|null, "label": str\|null, "description": str\|null, "error": str\|null}` |
| (mount) | `/` | `StaticFiles(directory="/app/frontend/dist", html=True)` — **added last** |

- CORS wide open (`allow_origins=["*"]`). `aiofiles` in `requirements.txt`.
- Signalling work (the WHEP handshake) must not block the event loop — run blocking GStreamer
  work off the event loop (`run_in_threadpool`) or entirely inside the GLib thread.

### Step 3: React + Vite Frontend
Initialize a React 18 + Vite 5 project in `frontend/`.

- **Header branding:** CBC Radio-Canada logo (`/cbc-logo.png`, `height: 2.2rem`) inline beside a
  "WebRTC to MXL" `h1`, sharing a flex row (`display:flex; align-items:center; gap:1rem`).
- **Dark theme** consistent with the other gst-apps (background `#0f0f0f`, cards `#1c1c1c`).
- `const API = ""` so all fetches are same-origin relative paths. Guard list responses with
  `Array.isArray(d) ? d : []` before `.map()`.
- **Setup section** (disabled while running): domain dropdown (`GET /domains`, refresh →
  `POST /get-domains`, displaying `label`), microphone dropdown (`enumerateDevices`), Group Hint /
  Label / Description text inputs (prefilled), Start/Stop button (enabled when a domain is selected
  and the three text fields are non-empty).
- **Operation section** (greyed-out until running): label + group hint + UUID with a presence dot,
  connection-state text, the `AnalyserNode` mic level meter, and the error banner.
- **Start sequence (ordering matters):**
  1. `getUserMedia({ audio: { deviceId } })` → keep the `MediaStream` (also feed it to the
     `AnalyserNode` meter).
  2. Create `RTCPeerConnection({ iceServers: [] })`, `addTrack` the audio track (send-only),
     `createOffer`, `setLocalDescription`, wait for ICE gathering (or ~5 s timeout).
  3. **Publish first:** POST the offer SDP as `application/sdp` to
     `{whipBase-with-window.location.hostname}` (from `GET /config`); `setRemoteDescription` with
     the 201 answer. Keep the returned `Location` resource URL for teardown.
  4. **Then** `POST /pipeline/start` with `{domain_path, grouphint, label, description}` so the backend's WHEP pull
     finds a live publisher (its retry covers any residual race).
- **Stop sequence:** `POST /pipeline/stop`; close the `RTCPeerConnection`; `DELETE` the WHIP
  `Location` resource; stop the `MediaStream` tracks and the meter.
- Poll `GET /pipeline/status` every ~2 s to drive the running badge and error banner.
- **`vite.config.js`** — proxy API paths to `http://localhost:9600` for local dev (dev server
  only, not used in Docker). Dev port = host port + 100 → **9706** (gst-apps convention).
  ```js
  proxy: {
    '/config':   'http://localhost:9600',
    '/domains':  'http://localhost:9600',
    '/get-domains': 'http://localhost:9600',
    '/pipeline': 'http://localhost:9600',
  }
  ```

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
| `no property "audio-flow-id" in element "mxlsink"` | `mxlsink` has no flow-type-specific property | Use **`flow-id`** (plus `domain`); the media type comes from the caps |
| `mxlsink` rejects the audio caps | `mxlsink` audio requires 32-bit float interleaved | Feed `audio/x-raw,format=F32LE,layout=interleaved`; pin `rate=48000`, omit `channels` |
| "UUID belongs to a flow with another active writer" | Two `mxlsink`s (or a second `set_caps`) on the same `flow-id` | One writer per flow; don't double-create; avoid pipeline shapes that re-emit CAPS |
| WHEP handshake returns 404 | MediaMTX has no publisher yet (browser hasn't published) | Publish WHIP from the browser first; have the backend **retry** the WHEP POST with backoff |
| No media ever arrives at `pad-added` | `webrtcbin` offered send/inactive instead of recvonly | `add-transceiver` with `RECVONLY` Opus caps **before** `create-offer` |
| Promise callback never fires | `Gst.Promise.new_with_change_func(func, user_data)` doesn't forward `user_data` in PyGObject | Use `lambda p: callback(p, captured_var)` |
| `set_promise.wait()` blocks | Calling `wait()` inside a GLib promise callback can deadlock | Pass `Gst.Promise.new()` to `set-local-description`; never `wait()` inside a callback |
| Callback failure is silent | GLib swallows Python exceptions in signal handlers | Wrap every GLib/GStreamer callback in `try/except` with `exc_info=True` |
| `opusdec` fails to negotiate / no audio | sample-rate mismatch into `mxlsink` | Keep `audioresample` before the F32LE/48k capsfilter |
| Microphone blocked / `getUserMedia` throws | `getUserMedia` needs a **secure context** | OK on `localhost`; a remote LAN IP over plain HTTP blocks mic access — use localhost or serve over HTTPS |
| Empty mic dropdown labels | Device labels are hidden until permission is granted | Request `getUserMedia` once on mount, then re-`enumerateDevices()` |
| `no element "webrtcbin"` / `GstWebRTC` import fails | `gst-plugins-bad` typelib/ICE missing | Install `gir1.2-gst-plugins-bad-1.0` and `gstreamer1.0-nice` |
