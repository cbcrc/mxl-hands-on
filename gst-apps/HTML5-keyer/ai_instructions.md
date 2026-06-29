# AI Agent Instructions: MXL HTML5 Keyer

## Project Overview
Build a Dockerized media application that functions as an **MXL → HTML5 keyer → MXL** processor. The application takes **one** MXL video flow as a background input and one HTML5 graphics URL (rendered by an embedded Chromium/CEF browser), composites the graphics with an alpha-key over the background using CPU-based mixing, and publishes the composited result as a single MXL video output flow. The application uses GStreamer for media processing, FastAPI for the backend API, and React + Vite for the web frontend. It is located at `./gst-apps/HTML5-keyer`. There is no NMOS control. Keep code style and patterns consistent with `./gst-apps/input-selector` (MXL input flow discovery and selection; deterministic MXL output flow identity; domain scanning).

This application is both an **MXL receiver** (1 video input) and an **MXL publisher** (1 video output). Video only — no audio handling.

> ⚠️ **CPU-only by design.** This app intentionally uses the CPU-based `compositor` element rather than `glvideomixer` so it runs on machines without a discrete GPU. Earlier attempts with GPU compositors hit performance issues on commodity hosts and added a `glupload`/`gldownload` round-trip that ate ~950 MB/s of memory bandwidth. Do not switch the mixer to a GPU equivalent.

## System Architecture
The application runs as a **single service on one port** inside Docker:
1. **FastAPI (port 9600):** Serves both the REST API and the built React static files (mounted via `StaticFiles`). The `StaticFiles` mount is added **last**, after all API routes, so API paths take precedence.
2. **Frontend:** Built with React + Vite into a `dist/` directory. No separate static file server — FastAPI serves `dist/` directly via `fastapi.staticfiles.StaticFiles`.
3. **Media Engine:** GStreamer via `gi.repository.Gst` Python bindings. The pipeline uses one `mxlsrc` (background) and one `cefsrc` (HTML5 overlay) feeding into a single GStreamer `compositor` element, whose source pad feeds a single `mxlsink`. The key is toggled live by setting the `alpha` property on the compositor sink pad that carries the CEF overlay.

This single-port design means the browser always uses the **same origin** for both the UI and the API, so no port number is hardcoded in the frontend JavaScript. The docker-compose host-port mapping (`9605:9600`) can be changed freely without rebuilding the image.

## Environment & File Specifications
- **MXL src documentation:** `./dmf-mxl/rust/gst-mxl-rs/readme.md` — authoritative reference for `mxlsrc` properties.
- **mxlsrc properties:** Use `video-flow-id` for the video source. `domain` is always required.
- **mxlsink format requirements:** A `capsfilter` with `video/x-raw,format=v210` **must** be placed immediately after `mxlsrc` and immediately before `mxlsink`. Inside the mixer, both pads operate on `BGRA` (alpha-aware) so explicit `capsfilter(format=BGRA, ...)` capsfilters are mandatory on both compositor sink pads. Without explicit capsfilters, auto-negotiation may land on an incompatible format and cause a hard failure at runtime.
- **MXL domain root:** `/mxl-domain` (mounted Docker volume, shared with other services).
- **MXL-info binary:** `/opt/mxl/tools/mxl-info/mxl-info` — used for input flow discovery (same as `mxl-info-gui`, `mxl2webrtc`, and `input-selector`).
  > ⚠️ In the build tree the tool path is `./dmf-mxl/build/Linux-Clang-Release/tools/mxl-info/mxl-info` — the directory and the executable share the same name.
- **libgstmxl.so build path:** `./dmf-mxl/rust/target/release/libgstmxl.so` (compiled from the Rust GStreamer plugin source).
- **gst-plugin-cef (`libgstcef.so`):** Not in the Ubuntu 24.04 package set. Must be built in a dedicated Cargo build stage from `https://github.com/centricular/gstcefsrc`, against a downloaded **CEF binary distribution** for `linux64`. The resulting `libgstcef.so` is installed to `/usr/lib/x86_64-linux-gnu/gstreamer-1.0/` in the runtime stage, and the CEF runtime directory (containing `libcef.so`, `*.pak`, `icudtl.dat`, `chrome-sandbox`, the `swiftshader/` and `locales/` directories, and the `Resources/`/`Release/` tree) must be copied to `/opt/cef` and added to `LD_LIBRARY_PATH`. The CEF sandbox helper (`chrome-sandbox`) requires `setuid root` permissions; if that is unavailable, the application must launch the CEF browser with `--no-sandbox` (set `CEF_DISABLE_SANDBOX=1` in the runtime environment so `gstcefsrc` disables sandboxing automatically). The CEF version pinned by `gstcefsrc/Cargo.toml` is authoritative — use exactly that version to avoid ABI drift.
- **Input format reader:** Before starting the pipeline, the backend reads `flow_def.json` for the selected MXL input flow at `{domain-path}/{flow_uuid}.mxl-flow/flow_def.json` and extracts:
  - `frame_width`
  - `frame_height`
  - `grain_rate.numerator` and `grain_rate.denominator`
  - `interlace_mode`
  These values define the **output format** (the keyer is a pure passthrough of geometry — no rescaling or framerate conversion is performed) and also configure the `cefsrc` capsfilter so that the HTML5 graphic is rendered at exactly the same raster and frame rate as the background. If the chosen input flow's `flow_def.json` cannot be read or is missing any of these fields, `POST /pipeline/start` returns HTTP 400 with a descriptive error and the frontend displays an error banner. The pipeline is not started.
- **Interlace handling:** Map `flow_def.json`'s `interlace_mode` to the GStreamer `interlace-mode` caps field: `progressive` → `progressive`, `interlaced_tff`/`interlaced_bff` → `interleaved`. Apply this both to the background capsfilter and to the `cefsrc` capsfilter. CEF always renders progressive; if the input is interlaced, document the limitation in the UI (a small grey caption under the URL field reading "CEF renders progressive — interlaced inputs are deinterlaced implicitly by the BGRA conversion").
- **MXL Flow Identity (output):** The single output flow UUID is **deterministic** — derived via UUID v5 from a fixed application namespace and the name `"<grouphint>:video"` (e.g. `"HTML5-Keyer:video"`). Restarting the pipeline with the same group hint reuses the same UUID and overwrites the existing flow directory in the domain. Changing the group hint produces a different UUID. The application namespace UUID is a constant defined in `gst_keyer.py` (`_MXL_KEYER_NS`) and must never change after deployment, as doing so would orphan previously written flow directories. Once the pipeline is running and the mxl sink has written the flow to disk, the backend must poll until `{selected-domain-path}/{flow_uuid}.mxl-flow/flow_def.json` exists, then patch that file: set `grouphint` to `"<user-grouphint>:Video"`; update `tags["urn:x-nmos:tag:grouphint/v1.0"]` to `["<user-grouphint>:Video"]` (if the `tags` object is present); and replace `description` and `label` with the values provided by the user in the Setup section.

## Required API & UI Functionalities

The UI is divided into two distinct sections: **Setup** and **Operation**.

---

### Section 1 — Setup

This section is used to configure the MXL input flow, the HTML5 graphics URL, and the MXL output flow before starting the GStreamer pipeline. The pipeline does **not** start until the user clicks the **Start** button. All controls in this section are disabled while the pipeline is running.

1. **MXL Domain Selector:** Scan `/mxl-domain` recursively for `domain_def.json` files. Read the `id` field for the domain UUID, the `label` and `description` fields, and use the containing directory path as the domain path (passed to both `mxlsrc` and `mxlsink`'s `domain` property). Provide a dropdown to select the target MXL domain — **each option displays the domain `label`** (falling back to the directory path when the label is absent) instead of the raw UUID. Changing the domain resets the input flow selector. Input and output use the same domain.
2. **MXL Input Flow Selector (Background):** A single dropdown populated from the flow list for the selected domain. Each option displays the first 8 characters of the UUID, description, label, and group hint. **Only show flows that have "video" (case-insensitive) after `:` in their `flow_grouphint`** (same filtering rule as `input-selector` and `mxl2webrtc`). The dropdown does **not** include a "None" option — a real MXL background is mandatory because the output format is derived from it.
3. **Refresh Flow List button** — manually triggers a flow re-scan for the selected domain.
4. **HTML5 Graphics URL:** A text input that accepts the full URL of the HTML5 graphics source to be keyed (e.g. `http://spx-server:5660/renderer/`). The URL is passed to `cefsrc` as its `url` property. Default value: empty. The field accepts any string that begins with `http://` or `https://`; URLs that do not match are flagged with a red border and the Start button is disabled. The URL is fixed for the lifetime of the pipeline — to change it, the user must Stop and Start. A small grey caption underneath reads "Pages must allow embedding (no `X-Frame-Options: deny`) and should render with a transparent background so the alpha channel keys correctly".
   > ⚠️ **Inside Docker, `localhost` refers to the container.** To reach a service on the host machine (e.g. SPX Server at port 5660) use `http://host.docker.internal:5660/renderer/`. The `host.docker.internal` hostname is pre-configured in the `html5-keyer` service in `docker-compose.yml` via `extra_hosts: host-gateway`. Your browser on the host continues to use `http://localhost:5660/renderer/` as normal.
5. **Group Hint:** A text input for the output flow group hint. Default value: `HTML5-Keyer`.
6. **Output Flow Configuration:** Two text inputs for the single output video flow:
   - **Description** — text input. Default: `keyer-out-1`.
   - **Label** — text input. Default: `html5-keyer-video`.
   - Both are mandatory; the Start button is disabled until both are non-empty.
7. **Start / Stop button** — reads the input flow's format, builds and starts the GStreamer pipeline when clicked. Changes to a **Stop** button once the pipeline is running. Clicking Stop tears down the pipeline. The Start button is enabled only when (a) a domain is selected, (b) an input flow is selected, (c) the HTML5 URL is a syntactically valid `http(s)://` URL, and (d) Description and Label are both non-empty.
8. **Format-read error banner** — when `POST /pipeline/start` returns HTTP 400 because the input flow's `flow_def.json` is missing or malformed, display a dismissible red banner above the Setup section quoting the backend's error message and showing the chosen input UUID.

> ℹ️ Input flow selector is populated using the same domain-scanning and parsing logic as `mxl-info-gui`, `mxl2webrtc`, and `input-selector` — calling `mxl-info -d <domain_path>` and parsing the output. Only active flows (those reported by `mxl-info`) are shown, then filtered to video-only by group hint.

---

### Section 2 — Operation

This section is enabled only once the pipeline is running (greyed-out and non-interactive otherwise).

1. **Key ON / OFF toggle button:** A single large button that toggles the alpha key on the CEF overlay pad. The button has two states:
   - **OFF** — neutral background, label "● Key OFF". Clicking calls `POST /pipeline/key` with `{"on": true}` which sets `compositor.sink_1.alpha = 1.0`.
   - **ON** — green background, label "● Key ON". Clicking calls `POST /pipeline/key` with `{"on": false}` which sets `compositor.sink_1.alpha = 0.0`.
   The button is the **only** interactive control on the page while the pipeline is running. The initial state on pipeline start is **OFF** (alpha = 0.0) — the overlay is hidden until the operator deliberately enables it.
2. **Status panel:**
   - **Background Input:** Display the input flow UUID (first 8 chars + tooltip with full UUID) and a green presence dot when the pipeline is running.
   - **HTML5 Overlay:** Display the currently rendered URL (truncated with ellipsis if long, full URL in tooltip).
   - **Output Flow UUID:** Display the deterministic UUID written to the domain.
   - **Format:** Display `WxH @ num/den fps, <interlace_mode>` derived from the input format.
   - **Status badge:** A small badge that reads "● PUBLISHING" while the pipeline is running.

---

## Step-by-Step Implementation Guide

### Step 1: Docker Setup

- Use `./gst-apps/docker-compose.yml` as a baseline. The `mxl-domain` named volume is defined there and shared by all services.
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
- Add the `html5-keyer` service to `docker-compose.yml`:
  ```yaml
  html5-keyer:
    platform: linux/amd64
    build:
      context: ..
      dockerfile: gst-apps/HTML5-keyer/Dockerfile
    image: html5-keyer:latest
    container_name: html5-keyer
    hostname: html5-keyer
    domainname: local
    ports:
      - "9605:9600"   # FastAPI serves both API and React frontend; change host port freely
    volumes:
      - type: volume
        source: mxl-domain
        target: /mxl-domain
    environment:
      - MXL_DOMAIN=/mxl-domain
      - CEF_DISABLE_SANDBOX=1
    # CEF launches helper processes; bump shared memory so Chromium does not crash
    shm_size: "1gb"
  ```
- Add port mapping `9605:9600` only — FastAPI serves both the API and the React frontend on the same port. No separate frontend port is needed.
- The build context is the **repository root** (`..` relative to `./gst-apps/`). All `COPY` paths in the Dockerfile are relative to the repository root (e.g. `COPY gst-apps/HTML5-keyer/backend/ /app/backend/`).
- The Dockerfile uses **three stages**: a `node:18-bullseye-slim` stage to build the React frontend, a `rust:1.79-bookworm` (or compatible) stage to build `gst-plugin-cef`, and an `ubuntu:24.04` runtime stage.

**Stage 1 (Frontend Builder):**
```dockerfile
FROM node:18-bullseye-slim AS frontend-builder
WORKDIR /build
COPY gst-apps/HTML5-keyer/frontend/package.json \
     gst-apps/HTML5-keyer/frontend/package-lock.json* ./
RUN npm install --legacy-peer-deps
COPY gst-apps/HTML5-keyer/frontend/ ./
COPY gst-apps/logo/rgb_cbc-radio-canada-col-coul.png ./public/cbc-logo.png
RUN npm run build
```

**Stage 2 (CEF / gstcefsrc Builder):**
Build `gst-plugin-cef` against a downloaded CEF binary distribution. Pin the CEF version to whatever `gstcefsrc/Cargo.toml` declares (do not float `master` — the CEF C ABI changes between minor releases).
```dockerfile
FROM rust:1.79-bookworm AS cef-builder

# GStreamer dev headers required to compile gst-plugin-cef
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential pkg-config python3 \
      libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev \
      libglib2.0-dev \
      curl ca-certificates xz-utils bzip2 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Download the CEF binary distribution (pin to the version gstcefsrc expects)
ARG CEF_VERSION=120.1.10+g3ce3184+chromium-120.0.6099.129
RUN curl -L --fail \
      "https://cef-builds.spotifycdn.com/cef_binary_${CEF_VERSION}_linux64_minimal.tar.bz2" \
      -o cef.tar.bz2 \
 && mkdir -p /opt/cef \
 && tar -xjf cef.tar.bz2 --strip-components=1 -C /opt/cef \
 && rm cef.tar.bz2

# Build the cef wrapper library and gst-plugin-cef itself
RUN git clone https://github.com/centricular/gstcefsrc.git
WORKDIR /build/gstcefsrc
ENV CEF_PATH=/opt/cef
RUN cmake -B build -DCMAKE_BUILD_TYPE=Release \
 && cmake --build build --parallel \
 && cargo build --release --manifest-path Cargo.toml
```
The exact build commands depend on the upstream `gstcefsrc` README at the time of build — adapt them as needed. The expected outputs are:
- `libgstcef.so` (the GStreamer plugin) — copied to `/usr/lib/x86_64-linux-gnu/gstreamer-1.0/libgstcef.so` in the runtime stage.
- `libcef.so` and the rest of `/opt/cef` — copied wholesale to `/opt/cef` in the runtime stage.

**Stage 3 (Runtime on Ubuntu 24.04):**
- Base image: strictly `ubuntu:24.04`.
- Install native runtime packages via `apt-get`:
  ```
  python3 python3-pip python3-gi python3-gi-cairo
  gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0
  gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good
  gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly gstreamer1.0-libav
  libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0
  libxcomposite1 libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 libcairo2
  libasound2t64 libxshmfence1 fonts-liberation
  ```
  > `compositor` is part of `gstreamer1.0-plugins-base`. The `lib*` and `fonts-*` packages on lines 4-6 are CEF runtime dependencies (Chromium needs them at process start; without them the CEF helper process exits with `error while loading shared libraries`).
- Install Python backend dependencies globally:
  ```dockerfile
  RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt
  ```
- Copy the MXL GStreamer plugin and shared libraries (same recipe as `input-selector`):
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
- Copy `gst-plugin-cef` and the CEF runtime tree from Stage 2:
  ```dockerfile
  COPY --from=cef-builder /opt/cef /opt/cef
  COPY --from=cef-builder /build/gstcefsrc/target/release/libgstcef.so \
       /usr/lib/x86_64-linux-gnu/gstreamer-1.0/libgstcef.so
  RUN ldconfig /opt/cef
  ```
- Set environment variables and expose port:
  ```dockerfile
  ENV LD_LIBRARY_PATH=/opt/mxl/lib:/opt/cef
  ENV MXL_DOMAIN=/mxl-domain
  ENV CEF_DISABLE_SANDBOX=1
  ENV GST_DEBUG=2
  EXPOSE 9600
  ```
- Copy the compiled frontend and backend:
  ```dockerfile
  COPY gst-apps/HTML5-keyer/backend/ /app/backend/
  COPY --from=frontend-builder /build/dist /app/frontend/dist
  ```

### Step 2: FastAPI & GStreamer Backend

Create a FastAPI application at `backend/main.py` running on **port 9600**.

**Configuration:**
```python
MXL_INFO_BIN     = "/opt/mxl/tools/mxl-info/mxl-info"
MXL_DOMAIN_ROOT  = os.environ.get("MXL_DOMAIN", "/mxl-domain")
```

**Domain and flow scanning** — reuse the same logic as `mxl-info-gui`, `mxl2webrtc`, and `input-selector`:
- `scan_domains()`: scan `/mxl-domain` recursively for `domain_def.json`; store `id` (UUID), `label`, `description`, and directory path. Called once at startup.
- `scan_domain_path(domain_path)`: call `mxl-info -d <domain_path>` via `subprocess.run`, parse the output using a UUID-anchored regex, return a list of flows with `flow_uuid`, `flow_label`, `flow_grouphint`, and `description` (read from `<uuid>.mxl-flow/flow_def.json`).
- Parsing rules are identical to `mxl-info-gui` — see that app's instructions for the full regex and edge-case handling. **Note:** `mxl-info -d` now emits a leading `Domain Definition:` block (echoing the domain `id`/`label`/`description`) before the flow listing; the parser must detect that header and skip its indented lines so they are not mistaken for flows.

**Flow format reader:**
- `read_flow_format(domain_path, flow_uuid)`: load `{domain_path}/{flow_uuid}.mxl-flow/flow_def.json` and return a dict containing `frame_width`, `frame_height`, `grain_rate` (the full dict with `numerator` and `denominator`), and `interlace_mode`. Raise `FileNotFoundError` if the flow does not exist, and `KeyError` if any required field is missing.

**GStreamer pipeline class (`GstKeyer`) in `backend/gst_keyer.py`:**

The pipeline is built programmatically (not via `parse_launch`) and uses the GStreamer **`compositor`** element to alpha-key the CEF overlay over the MXL background.

- Do **not** start the pipeline at `__init__` — only when `start(domain_path, input_flow_uuid, html5_url, grouphint, description, label)` is called.
- Imports required:
  ```python
  gi.require_version("Gst", "1.0")
  from gi.repository import GLib, Gst
  ```
- Run a `GLib.MainLoop` in a daemon thread so GStreamer bus messages are dispatched.
- Pipeline construction:
  1. Read the input flow's `flow_def.json` to extract `W`, `H`, `num`, `den`, and the GStreamer `interlace-mode` value.
  2. Create a single `compositor` element. Keep references to its two sink pads: `self._bg_pad` (request_pad_simple("sink_%u")) for the background and `self._cef_pad` (request_pad_simple("sink_%u")) for the CEF overlay.
  3. **Background branch:** `mxlsrc(domain=<path>, video-flow-id=<uuid>) → capsfilter(video/x-raw,format=v210) → videoconvert → capsfilter(video/x-raw,format=BGRA,width=W,height=H,framerate=num/den) → queue(leaky=2,max-size-buffers=2,max-size-time=0,max-size-bytes=0) → compositor.sink_0`. The intermediate `v210` capsfilter pins the upstream negotiation; the BGRA capsfilter after `videoconvert` is what `compositor` actually sees.
  4. **CEF overlay branch:** `cefsrc(url=<url>) → capsfilter(video/x-raw,format=BGRA,width=W,height=H,framerate=cef_fps/1) → videorate → capsfilter(video/x-raw,format=BGRA,framerate=num/den) → queue(leaky=2,max-size-buffers=2,max-size-time=0,max-size-bytes=0) → compositor.sink_1`. `cefsrc` requires an **integer** framerate — compute `cef_fps = max(1, round(num/den))` and use it in the first capsfilter. `videorate` then re-paces the output to the exact `num/den` grain rate; the second capsfilter pins that rate downstream. Without `videorate` the mixer drops/duplicates frames irregularly when the browser renders at a different cadence.
  5. **Output branch:** `compositor.src → identity(signal-handoffs=True) → videoconvert → capsfilter(video/x-raw,format=v210,width=W,height=H,framerate=num/den,colorimetry=bt709) → queue(leaky=2,max-size-buffers=2) → mxlsink(domain=<path>, ..., sync=false)`.
     - **`identity` PTS fix:** The compositor normalises all input timestamps to *running time* (elapsed ns since pipeline start). `mxlsink` expects timestamps in the pipeline clock's absolute domain (so its one-shot `mxl_pts_offset` maps them onto TAI correctly). Without correction the grain indices land near the epoch. The `identity` element has `signal-handoffs=True`; its Python `handoff` callback adds `pipeline.get_base_time()` to each buffer's PTS/DTS, converting running time back to absolute clock time. Leave `mxlsink` at its default (`async`/preroll) behaviour: the compositor produces no output until the pipeline is PLAYING and `base_time` is set, so the first buffer the sink ever sees is already corrected.
     - **`colorimetry=bt709` in the output capsfilter:** Pins the colorimetry field so that the two CAPS events the compositor emits (one before the CEF pad becomes active, one after) are identical from `mxlsink`'s perspective. Without this pin, the colorimetry changes from `bt709` to a composite value once CEF's sRGB frames join the mix; `GstBaseSink` calls `set_caps` a second time, `mxlsink`'s `create_flow_writer` returns `was_created=false`, and the pipeline aborts with "another active writer".
     - **`sync=false` on `mxlsink`:** Prevents `GstBaseSink` from trying to clock-synchronise the absolute-domain PTS values (which it would otherwise interpret as a running-time offset far in the future). The write thread inside `mxlsink` still waits for the correct TAI wall time before committing each grain.
  6. **Set `sync=false` on the CEF sink pad of the compositor** — this is the property on the **pad**, not the element. The CEF branch runs on the system clock; the MXL background runs on the live MXL clock. Without `sync=false` on `compositor.sink_1`, the mixer stalls waiting to align CEF buffer timestamps with the MXL clock domain. Set it programmatically:
     ```python
     self._cef_pad.set_property("sync", False)
     ```
     Note: some versions of the `compositor` element do not expose `sync` on its pads. Wrap the call in a try/except and log a warning if unavailable.
  7. Set the initial alpha on the CEF pad: `self._cef_pad.set_property("alpha", 0.0)` (key **OFF** by default — the operator enables it explicitly).
  8. **Do NOT set `ignore-inactive-pads` on the `compositor`.** Leave it at its default (`False`) so the compositor **waits** for the CEF pad's first buffer before producing output. This is critical: CEF takes 1–3 s to spin up Chromium, and if `ignore-inactive-pads=True` the compositor's source task fires an aggregate at PLAYING while `base_time` is still 0 (deadline already "in the past"), races ahead of the CEF pad before its serialized caps/allocation-query are consumed, and the aggregate thread deadlocks on those un-consumed events — the compositor then produces **no output for the entire session** and mxl-info-gui reports the output flow at the epoch (~56 years). It is intermittent because it depends on how far along CEF is when that first aggregate fires. With the flag left off, the compositor waits for CEF and consumes its caps/query while waiting, so there is no deadlock. (An earlier "stalls at caps negotiation" symptom that this flag appeared to fix was actually the malformed `interlace-mode` caps `mxlsrc` used to emit — fixed in the plugin, commit `04399b5`. Rebuild `libgstmxl.so` from current source so `mxlsrc` emits clean `interlace-mode=progressive`.)
  9. Set `zorder` so the CEF pad sits above the background: `self._bg_pad.set_property("zorder", 0)`; `self._cef_pad.set_property("zorder", 1)`.
- Provide a method `set_key(on: bool)` that sets `alpha` on `self._cef_pad` to `1.0` (ON) or `0.0` (OFF). This method is callable while the pipeline is in PLAYING state and toggles the key live with no rebuild.
- Wait for the output `mxlsink` to write its `flow_def.json` (poll every 100 ms, time out at 10 s) and then patch `grouphint`, `tags["urn:x-nmos:tag:grouphint/v1.0"]`, `description`, and `label` in a background thread (same pattern as `input-selector` and `file-player`).
- On `stop()`: set pipeline to NULL, wait for state change, release request pads, clear references, `gc.collect()`. Note: CEF takes ~1-2 s to tear down its helper processes — log progress at each step so a hung teardown is diagnosable.

**API endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/get-domains` | Trigger a fresh domain scan; return updated domain list |
| `GET`  | `/domains` | Return the cached domain list (each entry: `id`, `label`, `description`, `path`) |
| `GET`  | `/scan-domain?domain_path=<path>` | Run `mxl-info -d` and return parsed flow list |
| `POST` | `/pipeline/start` | Accept `domain_path`, `input_flow_uuid`, `html5_url`, `grouphint`, `description`, `label`. Reads the input format first; on a missing/malformed `flow_def.json` returns **HTTP 400** with `{"detail": "Input flow format could not be read", "errors": [...]}`. On success, builds and starts the GStreamer pipeline and returns `{"running": true, "output_flow_uuid": "...", "format": {...}, "key_on": true}`. |
| `POST` | `/pipeline/stop` | Stop and tear down the pipeline |
| `GET`  | `/pipeline/status` | Return `{"running": bool, "domain_path": str\|null, "input_flow_uuid": str\|null, "html5_url": str\|null, "output_flow_uuid": str\|null, "format": {...}\|null, "key_on": bool, "error": str\|null}` |
| `POST` | `/pipeline/key` | Body `{"on": bool}`. Only valid while running. Calls `set_key(on)` on the running pipeline and updates the cached key state. Returns the new status. |

- Serve the React static files as the last statement:
  ```python
  app.mount("/", StaticFiles(directory="/app/frontend/dist", html=True), name="static")
  ```
- `aiofiles>=23.0.0` must be in `requirements.txt` (required by `StaticFiles`).
- Call `scan_domains()` in the FastAPI `startup` event.

**GStreamer pipeline structure (summary):**
```
mxlsrc(input)  → videoconvert → capsfilter(BGRA) → queue(leaky) ──────────────────────────────────────────────────────────┐
                                                                                                                            ├→ compositor → identity(pts-fix) → videoconvert → capsfilter(v210, WxH, num/den, colorimetry=bt709) → queue(leaky) → mxlsink(output, sync=false)
cefsrc(url)    → capsfilter(BGRA, WxH, int-fps) → videorate → capsfilter(BGRA, num/den) → queue(leaky) ────────────────────┘
       (compositor: ignore-inactive-pads NOT set — wait for CEF's first buffer; sink_1: sync=false, alpha=0.0 at start)
```

### Step 3: React + Vite Frontend

Initialize a React + Vite project inside the `frontend/` directory targeting Vite 5 and React 18.

**Header branding:** At the very top of the page, display the CBC Radio-Canada logo (`gst-apps/logo/rgb_cbc-radio-canada-col-coul.png`) inline beside the "MXL HTML5 Keyer" h1 title. Copy the logo into `frontend/public/cbc-logo.png` so Vite serves it as a static asset; reference it in JSX as `<img src="/cbc-logo.png" />` with `height: 2.2rem`. The logo and title must share a flex row (`display: flex; align-items: center; gap: 1rem`).

**Dark theme** consistent with `./gst-apps/test-generator`, `./gst-apps/file-player`, `./gst-apps/mxl2webrtc`, and `./gst-apps/input-selector` (background `#0f0f0f`, section cards `#1c1c1c`).

**Setup section** (always visible, disabled while pipeline is running):
- MXL domain dropdown (populated from `GET /domains`; refresh button calls `POST /get-domains`). Each option displays the domain `label` (fallback to path) rather than the UUID.
- A single Input Flow dropdown labelled **MXL Background Input**, populated from `GET /scan-domain?domain_path=...` when the domain changes. Only include flows where the part after `:` in `flow_grouphint` contains "video" (case-insensitive). No "None" option.
- **HTML5 Graphics URL** text input with placeholder `http://spx-server:5660/renderer/`. Validate that the value begins with `http://` or `https://`; if not, draw a red border and disable Start. Below the field, a small grey caption: `Pages must allow embedding (no X-Frame-Options: deny) and should render with a transparent background so the alpha channel keys correctly`.
- Group hint text input (default `HTML5-Keyer`).
- Output flow row with Description (default `keyer-out-1`) and Label (default `html5-keyer-video`) text inputs.
- Start/Stop button. The Start button is **disabled** when: no domain is selected, no input flow is selected, the URL is empty or syntactically invalid, Description is empty, or Label is empty. Label changes to "Stop" while running.
- A dismissible **red error banner** rendered above the Setup section when `POST /pipeline/start` returns 400. The banner shows the `errors` array verbatim.
- Always guard API responses with `Array.isArray(d) ? d : []` before calling `.map()` on flow lists.

**Operation section** (greyed-out until pipeline is running):
- A pipeline status badge (green "● PUBLISHING" / grey "○ STOPPED").
- **Key ON / OFF toggle button** — the only interactive control on the page while the pipeline is running. Large (e.g. `padding: 1rem 2.5rem; font-size: 1.4rem`). Background is green when ON, neutral grey when OFF. Label is `● Key ON` or `● Key OFF`. Clicking calls `POST /pipeline/key` with the new state and waits for the response before updating the UI.
- **Status panel** (read-only): rows for Background Input UUID (first 8 chars + tooltip), HTML5 Overlay URL (truncated + tooltip), Output Flow UUID, Format (`WxH @ num/den fps, <interlace_mode>`), and the "PUBLISHING" badge.

**`vite.config.js`** — proxy all API paths to `http://localhost:9600` for local development (Vite dev server only — not used in the Docker image). Set the Vite dev port to the app's docker-compose host port + 100 (convention across all gst-apps: host 9605 → dev 9705).
```js
proxy: {
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

### Step 4: Pipeline Documentation

**Section 6 — HTML5 Keyer** already exists in `./gst-apps/gstreamer-pipeline.md` and is the source of truth for the pipeline this app must build. Once the application is fully working, **review and update** that section so the documented pipeline matches the actual implementation. In particular, the section must contain:
- A `gst-launch-1.0` CLI equivalent showing the full pipeline (background branch + CEF branch + compositor + output branch), including all capsfilters (`v210`, `BGRA`, `width`, `height`, `framerate`, `interlace-mode`), the leaky queues, `videorate`, `sync=false` on `mxlsink`, `sync=false` on `compositor.sink_1`, and the `mxlsrc`/`cefsrc`/`mxlsink` elements with representative property values.
- A Mermaid `flowchart` diagram with one subgraph per branch (Background, CEF Overlay, Output) showing how both branches feed into the single `compositor` and how the output `mxlsink` is fed.
- A prose explanation covering: why `compositor` (CPU) is used instead of `glvideomixer` (GPU performance hit + memory bandwidth), why `sync=false` is required on the CEF pad of the compositor (CEF runs on the system clock, MXL runs on the live MXL clock), why `videorate` is in the CEF branch (steady frame rate to the mixer regardless of browser rendering jitter), why the leaky queues are mandatory on every branch (live pipeline — never accumulate stale frames), why `ignore-inactive-pads` must **not** be set so the compositor waits for CEF's first buffer instead of deadlocking against its un-consumed startup events, why the `identity` handoff re-adds `base_time` to convert compositor running-time back to the absolute clock domain `mxlsink` expects, how the key is toggled live by setting `alpha` on `compositor.sink_1`, how the output format is derived from the input flow's `flow_def.json`, and the implications of CEF rendering progressive when the input is interlaced.

## Known Pitfalls

| Issue | Root cause | Fix |
|-------|-----------|-----|
| `no element "cefsrc"` | `gst-plugin-cef` is not in Ubuntu 24.04's package set | Build it from `centricular/gstcefsrc` in a dedicated Cargo build stage against a downloaded CEF binary distribution; copy `libgstcef.so` and the `/opt/cef` tree to the runtime image |
| `chrome-sandbox` permission denied at CEF startup | The CEF sandbox helper requires `setuid root`, which Docker images cannot grant without `--privileged` | Set `CEF_DISABLE_SANDBOX=1` so `gstcefsrc` passes `--no-sandbox` to CEF |
| CEF helper process exits with `error while loading shared libraries: libnss3.so` (or similar) | Chromium has a large native-library dependency tree that is not pulled in by GStreamer | Install `libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2t64 libxshmfence1 fonts-liberation` |
| CEF crashes immediately with `Failed to allocate shared memory` | Docker default `/dev/shm` is 64 MB — Chromium needs much more | Set `shm_size: "1gb"` on the docker-compose service |
| `Internal data stream error` from `compositor` after start | The CEF branch and the MXL branch produce buffers with mismatched timestamps relative to the pipeline clock | Set `sync=false` on the **CEF sink pad** of the compositor (the pad object, not the element) so the mixer composites the CEF frame as soon as it arrives |
| Output stutters or holds stale frames after a network blip on the MXL background | Default `queue` blocks when full, accumulating frames whose timestamps then arrive in a clump | Use `queue leaky=2 max-size-buffers=2 max-size-time=0 max-size-bytes=0` on every branch (background, CEF, output) so backlogs drop oldest frames rather than buffering them |
| CEF renders at an irregular rate, mixer drops/duplicates frames unevenly | Browser rendering cadence depends on page load and animations, not on a steady clock | Insert `videorate` between the two CEF capsfilters so the mixer always sees a steady cadence; the first capsfilter sets an integer fps for `cefsrc`, the second pins the exact `num/den` rate downstream |
| `cefsrc` refuses caps or pipeline fails to negotiate | `cefsrc` only accepts integer framerates on its src pad — fractional rates like `60000/1001` cause a caps negotiation error | Compute `cef_fps = max(1, round(num/den))` and set `framerate=cef_fps/1` in the capsfilter immediately after `cefsrc`; use a second capsfilter after `videorate` to pin the exact `num/den` |
| Alpha key has no effect | `alpha` was set on the `compositor` element rather than on the sink pad | Set `alpha` on the `Gst.Pad` returned by `request_pad_simple("sink_%u")` for the CEF branch (`compositor.sink_1`) — the `alpha` property lives on the pad, not the element |
| Output is letterboxed/pillarboxed | CEF rendered at a different resolution than the background and `compositor` resized one of them | Configure the `cefsrc` capsfilter with the **same** `width`, `height` as the validated input format so both pads enter the mixer at identical raster |
| mxl-info-gui shows latency ≈ 56 years / grains written near index 0 | The compositor converts TAI-domain PTSes to running time (0 … N ns). `mxlsink` computes `grain_index = buffer.pts + (mxl_now − clock.time())`; since both `mxl_now` and `clock.time()` are TAI (≈ 1.748 × 10¹⁸ ns), their difference ≈ 0, so `mxl_pts ≈ running_time ≈ 0` | Insert an `identity(signal-handoffs=True)` element after the compositor. In the Python `handoff` callback add `pipeline.get_base_time()` to `buf.pts` (and `buf.dts` if set), converting running time back to the TAI domain. Also set `sync=false` on `mxlsink` so `GstBaseSink` does not try to use the TAI PTS as a running-time offset |
| Latency ≈ 56 years **intermittently** — the output flow stays empty (no grains, head at epoch); some Start cycles are fine, others bad, with no clean pattern | `ignore-inactive-pads=True` lets the compositor's source task fire an aggregate at PLAYING while `base_time` is still 0 (deadline already in the past). It races ahead of the slow CEF pad before its serialized caps/allocation-query are consumed, and the aggregate thread then deadlocks on those un-consumed events — the compositor produces **no output at all** for the session, so `mxlsink` writes an empty flow that reads as the epoch. Intermittent because it depends on how far along CEF (1–3 s Chromium startup) is when the first aggregate fires | **Do not set `ignore-inactive-pads`** — leave it at default `False` so the compositor *waits* for CEF's first buffer and consumes its caps/query while waiting (no deadlock). Diagnose with `GST_DEBUG=aggregator:6,compositor:5`: a bad cycle shows `Actually aggregating, timeout: 1` at `base 0:00:00.000000000` followed by the src thread never returning while `sink_1` is `Waiting for query to be consumed` |
| "another active writer" — pipeline crashes on second CAPS event | Without `colorimetry=bt709` in the output capsfilter, the compositor emits two different CAPS events (colorimetry changes from `bt709` to a composite value when the CEF pad becomes active). `GstBaseSink` calls `set_caps` twice; the second call finds the flow directory already exists and `mxlsink` aborts | Add `colorimetry=bt709` to the output `capsfilter` that feeds `mxlsink`; this makes both CAPS events identical so `set_caps` is only called once |
| Pipeline fails to negotiate / `videoconvert` rejects the background caps | A **stale** `libgstmxl.so` emits a malformed, doubly-quoted `interlace-mode=(string)"\"progressive\""`. Downstream elements (and compositor caps reconciliation against the clean CEF pad) handle it only intermittently | Rebuild `libgstmxl.so` from current source — `mxlsrc` emits clean `interlace-mode=progressive` since commit `04399b5` (`InterlaceMode::as_str()`). Verify the built `.so` is newer than that commit and that the image actually copies it. Do **not** paper over it with `ignore-inactive-pads` (see the row above) |
| `compositor.sink_N has no property 'sync'` | Some GStreamer versions do not expose `sync` on compositor pads | Wrap `cef_pad.set_property("sync", False)` in a `try/except` and log a warning — the pipeline still works without it in most configurations |
| `http://localhost:5660/renderer/` fails from inside the container (connection refused) | Inside Docker, `localhost` refers to the container itself, not the host machine | Use `http://host.docker.internal:5660/renderer/` — the `html5-keyer` service has `extra_hosts: - "host.docker.internal:host-gateway"` in `docker-compose.yml` to make this hostname resolve |
| GPU-mixed variants (`glvideomixer`) struggle on commodity hosts | GPU compositing requires `glupload`/`gldownload` round-trips and a GPU that the target hardware may not have | Use the CPU-based `compositor` element — this is a deliberate design choice for portability and is not negotiable |

## Teleprompter (Prompting) Mode

A second, **toggleable mode** turns the same app into a studio **teleprompter**. The mode is
chosen in the UI before Start; **keying mode is unchanged**. The two modes share one
`GstKeyer` instance, one output `mxlsink`, the deterministic output-UUID scheme, and the
`identity` PTS-fix → v210 → `mxlsink` output branch.

### What prompt mode does differently
1. **No MXL video input.** The background is a synthetic **black picture** at an
   operator-chosen **resolution/framerate preset** (`videotestsrc pattern=2 is-live=true` →
   `capsfilter(BGRA, WxH, num/den, interlace-mode=progressive)`). Geometry comes from the
   preset, not from `read_flow_format`.
2. **The overlay is the teleprompter graphic**, hosted by this app and loaded into CEF, with
   the CEF compositor pad's **`alpha` fixed at 1.0** (always visible — there is no Key toggle
   in prompt mode; `set_key` raises if called).
3. **An optional MXL audio flow** drives voice tracking via server-side speech-to-text.

### OGraf graphic + host page (`prompter/`, served by FastAPI, copied to `/app/prompter`)
The teleprompter is the OGraf template from `ograph-template/teleprompter.{js,ograf.json}`.
`cefsrc` is one-way (it only takes a `url` and has **no JS-injection channel**), so the page
must be driven from the page side:
- **`prompter/index.html`** — an OGraf host page. It imports `./teleprompter.js`, registers
  it as a custom element, mounts it **empty** (no baked-in script), and opens a WebSocket to
  `/prompter-ws`. Inbound messages are dispatched to the element's OGraf methods:
  `{type:"update",data}` → `updateAction({data})`; `play`/`stop` → `playAction`/`stopAction`;
  `{type:"action",action}` → `customAction(action)`; `{type:"transcript",text}` →
  `pushTranscript(text)`.
- **`prompter/teleprompter.js`** — the template **plus one minimal addition**: a
  `pushTranscript(text)` method that sets `voiceTrackingActive = true` and calls the existing
  `matchTranscriptToScript(text)`. The browser Web Speech path is left intact (it is a
  harmless no-op in headless CEF, where `window.SpeechRecognition` is undefined), so the
  template still works in a desktop-browser preview.
- `cefsrc.url` in prompt mode is the **internal** URL `http://localhost:9600/prompter/`
  (FastAPI serves it on the same in-container port). Do **not** use `host.docker.internal`
  here — the page is local to the container.

> ⚠️ **Script text is never baked in.** The prompter content is supplied at runtime only, via
> `POST /prompter-api/update` (the UI paste window **or** an automation system). The
> `scriptText` default in `teleprompter.ograf.json` is inert and must not be relied on.

### Control channel & API (OGraf control points)
A `PrompterHub` keeps the set of connected `/prompter-ws` sockets, retains the latest
`update` data and play/stop state, and **replays that state on connect** (so a script set
before CEF finished loading still lands). It exposes a thread-safe `broadcast()` so both the
REST endpoints and the voice tracker can push to the page from any thread. The control
endpoints map 1:1 to `teleprompter.ograf.json`:

| Method | Path | Maps to |
|--------|------|---------|
| `GET`  | `/prompter-api/presets` | resolution/framerate preset list (single source of truth, frontend mirrors it) |
| `POST` | `/prompter-api/update`  | `updateAction(data)` — any subset of `scriptText`, `scrollSpeed`, `mirrored`, `enableVoiceTracking`, `voiceLanguage`, `fontSize`, `enableCountdown`, `showStatusBar` |
| `POST` | `/prompter-api/play`    | `playAction` |
| `POST` | `/prompter-api/stop`    | `stopAction` |
| `POST` | `/prompter-api/action`  | `customAction` — body `{action: "pause"\|"resume"\|"speedUp"\|"speedDown"}` |
| `WS`   | `/prompter-ws`          | the CEF host page connects here for commands + transcripts |

`enableVoiceTracking`/`voiceLanguage` in `/update` also toggle/configure the voice tracker.
Routes and the `/prompter-ws` socket are registered **before** the `/prompter` static mount
and the catch-all `/` mount; their distinct prefixes mean the `Mount("/prompter")` regex
never shadows `/prompter-api` or `/prompter-ws`.

### Voice tracking — server-side Vosk (`backend/voice_tracker.py`)
The template's voice tracking relies on the browser Web Speech API, which **cannot run in the
headless CEF render** (no microphone device, no Google cloud speech backend). Instead:
- The pipeline adds an **audio branch** when an audio flow is selected:
  `mxlsrc(audio-flow-id) → audioconvert → audioresample → capsfilter(audio/x-raw,
  format=S16LE,channels=1,rate=16000) → appsink(emit-signals, sync=false, drop, max-buffers)`.
  It is independent — **not** linked to the compositor/mxlsink — and `sync=false` keeps the
  live MXL audio clock from stalling the video output. `mxlsrc` already supports
  `audio-flow-id` (caps `audio/x-raw,F32LE`); no plugin change.
- The appsink `new-sample` callback feeds raw PCM to a `VoiceTracker` whose worker thread runs
  **Vosk** (offline). It loads the model for the selected language lazily
  (`en-US` → `/opt/vosk/en`, `fr-CA` → `/opt/vosk/fr`), reloads on language change, and only
  transcribes while voice tracking is enabled. Partial + final transcripts are broadcast as
  `{type:"transcript",text}` to the prompter page, which feeds them into `pushTranscript`.

### Backend wiring (`gst_keyer.py`, `main.py`)
- `gst_keyer.start_prompt(domain_path, audio_flow_uuid|None, W, H, num, den, html5_url,
  grouphint, description, label, audio_cb)` builds `_build_prompt_pipeline()`. The delicate
  output / CEF / compositor / start blocks are factored into shared helpers
  (`_add_output_branch`, `_add_cef_branch`, `_make_compositor`, `_start_pipeline`) reused by
  both key and prompt builds, so the proven epoch/clock behaviour is identical. `get_status()`
  adds `mode` and `audio_flow_uuid`; `main.py`'s status merges `voice_tracking`/`voice_language`.
- `StartConfig` gains `mode` (`"key"`|`"prompt"`), `audio_flow_uuid`, `resolution_preset`, and
  `voice_language`. `/pipeline/start` branches on `mode`: prompt mode validates the preset
  (resolves to W,H,num,den), resets the hub + voice tracker, and calls `start_prompt` with the
  internal `PROMPTER_URL`.

### Frontend (`App.jsx`)
- A **Keying / Teleprompter** mode toggle at the top of Setup (disabled while running).
- **Prompt Setup:** resolution-preset dropdown, an **optional** MXL Audio Input dropdown
  (flows filtered to a role containing "audio" — `isAudioFlow`), and group hint / description /
  label. No HTML5 URL field.
- **Prompt Operation** (a `PrompterControls` panel): a script **paste window** + "Load Script",
  scroll speed / font size, mirror / countdown / status-bar checkboxes, voice-language dropdown,
  an **Enable Voice Tracking** checkbox, and **Play / Stop / Pause / Resume / Speed −/+** — all
  calling `/prompter-api/*`. No Key ON/OFF in this mode.

### Docker
- A `vosk-models` stage downloads the small en-US and fr models into `/opt/vosk/{en,fr}` and
  the runtime copies them in; `vosk` is added to `requirements.txt`.
- `prompter/` is copied to `/app/prompter/`. `videotestsrc`, `appsink`, `audioconvert`, and
  `audioresample` are already present in the installed GStreamer plugin sets.

---

Please write the necessary Dockerfile, Python backend scripts, React components, and integration code following these guidelines at `./gst-apps/HTML5-keyer`, modifying content already present.
