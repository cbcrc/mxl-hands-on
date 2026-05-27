# AI Agent Instructions: MXL Input Selector

## Project Overview
Build a Dockerized media application that functions as a **3-input → 1-output MXL video selector**. The application takes up to three MXL video flows as inputs and routes exactly one of them at a time to a single MXL video output flow. The active input can be switched live from the UI while the pipeline is running. The application uses GStreamer for media processing, FastAPI for the backend API, and React + Vite for the web frontend. It is located at `./gst-apps/input-selector`. There is no NMOS control. Keep code style and patterns consistent with `./gst-apps/file-player` (MXL output flow management) and `./gst-apps/mxl2webrtc` (MXL input flow discovery and selection).

This application is unique among the gst-apps: it is both an **MXL receiver** (3 inputs) and an **MXL publisher** (1 output). Video only — no audio handling.

## System Architecture
The application runs as a **single service on one port** inside Docker:
1. **FastAPI (port 9600):** Serves both the REST API and the built React static files (mounted via `StaticFiles`). The `StaticFiles` mount is added **last**, after all API routes, so API paths take precedence.
2. **Frontend:** Built with React + Vite into a `dist/` directory. No separate static file server — FastAPI serves `dist/` directly via `fastapi.staticfiles.StaticFiles`.
3. **Media Engine:** GStreamer via `gi.repository.Gst` Python bindings. The pipeline uses three `mxlsrc` elements (or `videotestsrc` for empty slots) feeding into the GStreamer `input-selector` element, whose single source pad feeds a single `mxlsink`. Switching is performed live by setting the `active-pad` property on `input-selector`.

This single-port design means the browser always uses the **same origin** for both the UI and the API, so no port number is hardcoded in the frontend JavaScript. The docker-compose host-port mapping (e.g. `9604:9600`) can be changed freely without rebuilding the image.

## Environment & File Specifications
- **MXL src documentation:** `./dmf-mxl/rust/gst-mxl-rs/readme.md` — authoritative reference for `mxlsrc` properties.
- **mxlsrc properties:** Use `video-flow-id` for video sources. `domain` is always required.
- **mxlsink format requirements:** A `capsfilter` with `video/x-raw,format=v210` **must** be placed immediately after every MXL video source and immediately before the MXL video sink. Without explicit capsfilters, auto-negotiation may land on an incompatible format and cause a hard failure at runtime.
- **MXL domain root:** `/mxl-domain` (mounted Docker volume, shared with other services).
- **MXL-info binary:** `/opt/mxl/tools/mxl-info/mxl-info` — used for input flow discovery (same as `mxl-info-gui` and `mxl2webrtc`).
  > ⚠️ In the build tree the tool path is `./dmf-mxl/build/Linux-Clang-Release/tools/mxl-info/mxl-info` — the directory and the executable share the same name.
- **libgstmxl.so build path:** `./dmf-mxl/rust/target/release/libgstmxl.so` (compiled from the Rust GStreamer plugin source).
- **Input format validation:** Before starting the pipeline, the backend reads `flow_def.json` for every selected input flow at `{domain-path}/{flow_uuid}.mxl-flow/flow_def.json` and verifies that the following fields match across all selected inputs:
  - `frame_width`
  - `frame_height`
  - `grain_rate.numerator` and `grain_rate.denominator`
  - `interlace_mode`
  If any selected input differs from the others, the `POST /pipeline/start` call returns HTTP 400 with a descriptive error listing the mismatching fields per input, and the frontend displays an error banner. The pipeline is not started.
- **Empty input slots — black video fill:** If a user leaves one or more of the three input slots set to "None", that slot is replaced in the pipeline by a `videotestsrc pattern=black is-live=true` element followed by a `capsfilter` set to **the exact same `width`, `height`, `framerate`, and `format=v210`** as the selected MXL inputs. The format used for the black source is derived from the first non-None selected input (and since all selected inputs are validated to match, any of them yields the same answer). **At least one input slot must contain an MXL flow** — the Start button is disabled and `POST /pipeline/start` returns HTTP 400 if all three slots are "None", because in that case the output format is undefined.
- **MXL Flow Identity (output):** The single output flow UUID is **deterministic** — derived via UUID v5 from a fixed application namespace and the name `"<grouphint>:video"` (e.g. `"Input-Selector:video"`). Restarting the pipeline with the same group hint reuses the same UUID and overwrites the existing flow directory in the domain. Changing the group hint produces a different UUID. The application namespace UUID is a constant defined in `gst_selector.py` (`_MXL_SELECTOR_NS`) and must never change after deployment, as doing so would orphan previously written flow directories. Once the pipeline is running and the mxl sink has written the flow to disk, the backend must poll until `{selected-domain-path}/{flow_uuid}.mxl-flow/flow_def.json` exists, then patch that file: set `grouphint` to `"<user-grouphint>:Video"`; update `tags["urn:x-nmos:tag:grouphint/v1.0"]` to `["<user-grouphint>:Video"]` (if the `tags` object is present); and replace `description` and `label` with the values provided by the user in the Setup section.

## Required API & UI Functionalities

The UI is divided into two distinct sections: **Setup** and **Operation**.

---

### Section 1 — Setup

This section is used to configure the three MXL input flows and the single MXL output flow before starting the GStreamer pipeline. The pipeline does **not** start until the user clicks the **Start** button. All controls in this section are disabled while the pipeline is running.

1. **MXL Domain Selector:** Scan `/mxl-domain` recursively for `domain_def.json` files. Read the `id` field for the domain UUID and use the containing directory path as the domain path (passed to both `mxlsrc` and `mxlsink`'s `domain` property). Provide a dropdown to select the target MXL domain. Changing the domain resets all three input selectors. Inputs and the output use the same domain.
2. **Input Flow Selectors (Input 1 / Input 2 / Input 3):** Three independent dropdowns populated from the flow list for the selected domain. Each option displays the first 8 characters of the UUID, description, label, and group hint. Each dropdown includes a **"None"** option at the top — selecting "None" means that slot will be replaced by a generated black video source in the pipeline. **Only show flows that have "video" (case-insensitive) after `:` in their `flow_grouphint`** (same filtering rule as `mxl2webrtc`). The same flow may be selected in multiple input slots; the UI does not prevent this.
3. **Refresh Flow List button** — manually triggers a flow re-scan for the selected domain.
4. **Group Hint:** A text input for the output flow group hint. Default value: `Input-Selector`.
5. **Output Flow Configuration:** Two text inputs for the single output video flow:
   - **Description** — text input. Default: `selector-out-1`.
   - **Label** — text input. Default: `input-selector-video`.
   - Both are mandatory; the Start button is disabled until both are non-empty.
6. **Start / Stop button** — validates input flow formats (see "Input format validation" above), builds and starts the GStreamer pipeline when clicked. Changes to a **Stop** button once the pipeline is running. Clicking Stop tears down the pipeline. The Start button is enabled only when (a) a domain is selected, (b) at least one of the three input slots is not "None", and (c) Description and Label are both non-empty.
7. **Format validation error banner** — when `POST /pipeline/start` returns HTTP 400 because of mismatched input formats, display a dismissible red banner above the Setup section listing each input slot and its detected `WxH @ num/den` (or "—" for None), with the mismatching fields highlighted.

> ℹ️ Input flow selectors are populated using the same domain-scanning and parsing logic as `mxl-info-gui` and `mxl2webrtc` — calling `mxl-info -d <domain_path>` and parsing the output. Only active flows (those reported by `mxl-info`) are shown, then filtered to video-only by group hint.

---

### Section 2 — Operation

This section is enabled only once the pipeline is running (greyed-out and non-interactive otherwise).

1. **Active Input selector:** Three radio buttons (or a segmented control) labelled **Input 1**, **Input 2**, **Input 3**. Clicking one immediately switches the active input via `POST /pipeline/active-input`. The currently active input is highlighted. Each radio button is labelled with the underlying source: either the first 8 characters of the input flow UUID (when a real MXL input is wired), or **"⬛ Black fill (disabled)"** (when that slot is a generated black source). **Switching to a black-fill slot is disabled at the UI level** — the button is rendered at 40 % opacity with `cursor: not-allowed` and a tooltip explaining "Black-fill slots cannot be switched to live". The black-fill source is still wired into the pipeline graph (so the format-validation invariant continues to hold and the pipeline structure is uniform), but the live output is restricted to MXL-source slots. See the Known Pitfalls below for why.
2. **Input Status Row:** Three small status cards (one per input slot) showing for each slot: the source type ("MXL flow" or "Black fill"), the flow UUID (or "—" for Black), and a green presence dot when that slot is the currently active source (grey otherwise).
3. **Output Flow Info panel:**
   - **Output Flow UUID:** Display the deterministic UUID written to the domain.
   - **Format:** Display `WxH @ num/den fps, <interlace_mode>` derived from the validated input format.
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
- Add the `input-selector` service to `docker-compose.yml`:
  ```yaml
  input-selector:
    platform: linux/amd64
    build:
      context: ..
      dockerfile: gst-apps/input-selector/Dockerfile
    image: input-selector:latest
    container_name: input-selector
    hostname: input-selector
    domainname: local
    ports:
      - "9604:9600"   # FastAPI serves both API and React frontend; change host port freely
    volumes:
      - type: volume
        source: mxl-domain
        target: /mxl-domain
    environment:
      - MXL_DOMAIN=/mxl-domain
  ```
- Add port mapping `9604:9600` only — FastAPI serves both the API and the React frontend on the same port. No separate frontend port is needed.
- The build context is the **repository root** (`..` relative to `./gst-apps/`). All `COPY` paths in the Dockerfile are relative to the repository root (e.g. `COPY gst-apps/input-selector/backend/ /app/backend/`).
- The Dockerfile uses **two stages**: a `node:18-bullseye-slim` stage to build the React frontend, and an `ubuntu:24.04` runtime stage.

**Stage 1 (Frontend Builder):**
```dockerfile
FROM node:18-bullseye-slim AS frontend-builder
WORKDIR /build
COPY gst-apps/input-selector/frontend/package.json \
     gst-apps/input-selector/frontend/package-lock.json* ./
RUN npm install --legacy-peer-deps
COPY gst-apps/input-selector/frontend/ ./
COPY gst-apps/logo/rgb_cbc-radio-canada-col-coul.png ./public/cbc-logo.png
RUN npm run build
```

**Stage 2 (Runtime on Ubuntu 24.04):**
- Base image: strictly `ubuntu:24.04`.
- Install native runtime packages via `apt-get`:
  ```
  python3 python3-pip python3-gi python3-gi-cairo
  gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0
  gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good
  gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly gstreamer1.0-libav
  ```
  > `input-selector` is part of `gstreamer1.0-plugins-bad`.
- Install Python backend dependencies globally:
  ```dockerfile
  RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt
  ```
- Copy the MXL GStreamer plugin and shared libraries (same recipe as `mxl2webrtc`):
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
  EXPOSE 9600
  ```
- Copy the compiled frontend and backend:
  ```dockerfile
  COPY gst-apps/input-selector/backend/ /app/backend/
  COPY --from=frontend-builder /build/dist /app/frontend/dist
  ```

### Step 2: FastAPI & GStreamer Backend

Create a FastAPI application at `backend/main.py` running on **port 9600**.

**Configuration:**
```python
MXL_INFO_BIN     = "/opt/mxl/tools/mxl-info/mxl-info"
MXL_DOMAIN_ROOT  = os.environ.get("MXL_DOMAIN", "/mxl-domain")
```

**Domain and flow scanning** — reuse the same logic as `mxl-info-gui` and `mxl2webrtc`:
- `scan_domains()`: scan `/mxl-domain` recursively for `domain_def.json`; store UUID and directory path. Called once at startup.
- `scan_domain_path(domain_path)`: call `mxl-info -d <domain_path>` via `subprocess.run`, parse the output using a UUID-anchored regex, return a list of flows with `flow_uuid`, `flow_label`, `flow_grouphint`, and `description` (read from `<uuid>.mxl-flow/flow_def.json`).
- Parsing rules are identical to `mxl-info-gui` — see that app's instructions for the full regex and edge-case handling.

**Flow format reader:**
- `read_flow_format(domain_path, flow_uuid)`: load `{domain_path}/{flow_uuid}.mxl-flow/flow_def.json` and return a dict containing `frame_width`, `frame_height`, `grain_rate` (the full dict with `numerator` and `denominator`), and `interlace_mode`. Raise `FileNotFoundError` if the flow does not exist, and `KeyError` if any required field is missing.

**Format validation:**
- `validate_inputs(domain_path, input_flow_uuids)`: takes a list of three values (each either a UUID string or `None`). For each non-None UUID, call `read_flow_format`. Return `(common_format, errors)`. `common_format` is `None` when validation fails or when all slots are None; otherwise it is the format dict from the first non-None input. `errors` is a list of human-readable strings, one per mismatching slot, e.g. `"Input 2 has 1280x720 @ 60000/1001 while Input 1 has 1920x1080 @ 30000/1001"`. If all selected inputs match, `errors` is empty.

**GStreamer pipeline class (`GstSelector`) in `backend/gst_selector.py`:**

The pipeline is built programmatically (not via `parse_launch`) and uses the GStreamer **`input-selector`** element to perform live switching between the three branches.

- Do **not** start the pipeline at `__init__` — only when `start(domain_path, input_flow_uuids, grouphint, description, label)` is called.
- Imports required:
  ```python
  gi.require_version("Gst", "1.0")
  from gi.repository import GLib, Gst
  ```
- Run a `GLib.MainLoop` in a daemon thread so GStreamer bus messages are dispatched.
- Pipeline construction:
  1. Create a single `input-selector` element. Keep a Python dict `self._sink_pads = {0: pad0, 1: pad1, 2: pad2}` mapping slot index to the corresponding `input-selector` request pad (`request_pad_simple("sink_%u")`).
  2. For each of the three input slots:
     - If the slot has an MXL UUID: `mxlsrc(domain=<path>, video-flow-id=<uuid>) → capsfilter(video/x-raw,format=v210) → queue → input-selector.sink_N`.
     - If the slot is None (black fill): `videotestsrc pattern=2 is-live=true → capsfilter(video/x-raw,format=v210,width=W,height=H,framerate=num/den) → queue → input-selector.sink_N`. `W`, `H`, `num`, `den` come from the validated common format derived from the non-None inputs. Map `interlace_mode` to GStreamer's `interlace-mode` cap (`progressive` → `progressive`, `interlaced_tff`/`interlaced_bff` → `interleaved`) when constructing the caps string.
  3. Output branch: `input-selector.src → capsfilter(video/x-raw,format=v210) → queue → mxlsink(domain=<path>, ...flow metadata)`. The output `mxlsink` is configured to declare a flow whose `frame_width`, `frame_height`, `grain_rate`, and `interlace_mode` match the validated input format.
  4. Set the initial active input by setting `selector.set_property("active-pad", self._sink_pads[0])`. The active input on start is always **Input 1**.
- Provide a method `set_active_input(slot_index)` that sets `active-pad` to `self._sink_pads[slot_index]`. This method is callable while the pipeline is in PLAYING state and switches input live with no rebuild.
- Wait for the output `mxlsink` to write its `flow_def.json` (poll every 100 ms, time out at 10 s) and then patch `grouphint`, `tags["urn:x-nmos:tag:grouphint/v1.0"]`, `description`, and `label` in a background thread (same pattern as `file-player`).
- On `stop()`: set pipeline to NULL, wait for state change, release request pads, clear references, `gc.collect()`.

**API endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/get-domains` | Trigger a fresh domain scan; return updated domain list |
| `GET`  | `/domains` | Return the cached domain list |
| `GET`  | `/scan-domain?domain_path=<path>` | Run `mxl-info -d` and return parsed flow list |
| `POST` | `/pipeline/start` | Accept `domain_path`, `input_flow_uuids` (list of 3, each UUID-string or null), `grouphint`, `description`, `label`. Validates input formats first; on mismatch returns **HTTP 400** with `{"detail": "Input formats do not match", "errors": [...], "per_slot": [...]}`. On success, builds and starts the pipeline and returns `{"running": true, "output_flow_uuid": "...", "format": {...}}`. |
| `POST` | `/pipeline/stop` | Stop and tear down the pipeline |
| `GET`  | `/pipeline/status` | Return `{"running": bool, "domain_path": str\|null, "input_flow_uuids": [str\|null, str\|null, str\|null], "active_input": 0\|1\|2\|null, "output_flow_uuid": str\|null, "format": {...}\|null, "error": str\|null}` |
| `POST` | `/pipeline/active-input` | Body `{"slot": 0\|1\|2}`. Only valid while running. Calls `set_active_input` on the running pipeline and updates the cached active-input state. Returns the new status. |

- Serve the React static files as the last statement:
  ```python
  app.mount("/", StaticFiles(directory="/app/frontend/dist", html=True), name="static")
  ```
- `aiofiles>=23.0.0` must be in `requirements.txt` (required by `StaticFiles`).
- Call `scan_domains()` in the FastAPI `startup` event.

**GStreamer pipeline structure (summary):**
```
mxlsrc(input1) → capsfilter(v210) → queue ┐
mxlsrc(input2) → capsfilter(v210) → queue ┼→ input-selector → capsfilter(v210) → queue → mxlsink(output)
mxlsrc(input3) → capsfilter(v210) → queue ┘

(any "None" slot is replaced by:
 videotestsrc pattern=black is-live=true → capsfilter(v210,WxH,num/den) → queue → input-selector.sink_N)
```

### Step 3: React + Vite Frontend

Initialize a React + Vite project inside the `frontend/` directory targeting Vite 5 and React 18.

**Header branding:** At the very top of the page, display the CBC Radio-Canada logo (`gst-apps/logo/rgb_cbc-radio-canada-col-coul.png`) inline beside the "MXL Input Selector" h1 title. Copy the logo into `frontend/public/cbc-logo.png` so Vite serves it as a static asset; reference it in JSX as `<img src="/cbc-logo.png" />` with `height: 2.2rem`. The logo and title must share a flex row (`display: flex; align-items: center; gap: 1rem`).

**Dark theme** consistent with `./gst-apps/test-generator`, `./gst-apps/file-player`, and `./gst-apps/mxl2webrtc` (background `#0f0f0f`, section cards `#1c1c1c`).

**Setup section** (always visible, disabled while pipeline is running):
- MXL domain dropdown (populated from `GET /domains`; refresh button calls `POST /get-domains`).
- A 3-row Input Configuration Table with rows labelled **Input 1**, **Input 2**, **Input 3**. Each row contains a single column: an input flow dropdown populated from `GET /scan-domain?domain_path=...` when the domain changes. First option of each dropdown is **"None — black fill"**. Only include flows where the part after `:` in `flow_grouphint` contains "video" (case-insensitive). Selecting the same flow in multiple rows is allowed.
- Group hint text input (default `Input-Selector`).
- Output flow row with Description (default `selector-out-1`) and Label (default `input-selector-video`) text inputs.
- Start/Stop button. The Start button is **disabled** when: no domain is selected, all three inputs are "None", Description is empty, or Label is empty. Label changes to "Stop" while running.
- A dismissible **red error banner** rendered above the Setup section when `POST /pipeline/start` returns 400. The banner shows the `errors` array verbatim and a `per_slot` summary table (`Input 1: 1920x1080 @ 30000/1001 progressive | Input 2: 1280x720 @ 60000/1001 progressive | Input 3: —`).
- Always guard API responses with `Array.isArray(d) ? d : []` before calling `.map()` on flow lists.

**Operation section** (greyed-out until pipeline is running):
- A pipeline status badge (green "● PUBLISHING" / grey "○ STOPPED").
- **Active Input selector:** Three large radio-style buttons (one per slot). Each button shows the slot number, the source kind ("MXL flow" or "Black fill"), and, for MXL flows, the first 8 characters of the input UUID. Clicking a button calls `POST /pipeline/active-input` with the slot index and immediately reflects the new active slot. The active button has a green highlight; the others are neutral.
- **Input Status Row:** Three compact cards (one per slot) showing the slot's source kind, UUID (or "—"), and a presence dot (green when active, grey when standby).
- **Output Flow Info panel:** Output flow UUID, format (`WxH @ num/den fps, <interlace_mode>`), and the "PUBLISHING" badge.

**`vite.config.js`** — proxy all API paths to `http://localhost:9600` for local development (Vite dev server only — not used in the Docker image). Set the Vite dev port to the app's docker-compose host port + 100 (convention across all gst-apps: host 9604 → dev 9704).
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

Once the application is fully working, update **Section 4 — Input Selector** in `./gst-apps/gstreamer-pipeline.md` (create the section if it does not exist) to reflect the actual pipeline built. That section must contain:
- A `gst-launch-1.0` CLI equivalent showing the full pipeline (three input branches + the `input-selector` + the output branch), including all capsfilters and the `mxlsrc`/`mxlsink` elements with representative property values. Show two example variations: (a) all three inputs as MXL flows, and (b) two MXL flows + one black-fill slot.
- A Mermaid `flowchart` diagram with one subgraph per input branch (Input 1, Input 2, Input 3) and one for the output, showing how all three feed into a single `input-selector` and the output `mxlsink`.
- A prose explanation covering: why `input-selector` is used for live switching (zero-rebuild, sub-frame latency), how the `active-pad` property changes the routed input, why explicit `capsfilter(format=v210)` is mandatory both upstream of `input-selector` and downstream before `mxlsink`, how the format-matching invariant is enforced (so `input-selector` never has to re-negotiate caps mid-stream), and how empty input slots are replaced by `videotestsrc pattern=black` with caps cloned from the validated common format.

## Known Pitfalls

| Issue | Root cause | Fix |
|-------|-----------|-----|
| `no property "flow-id" in element "mxlsrc"` | `mxlsrc` has no `flow-id` property | Use `video-flow-id` (audio is not used in this app) |
| `Internal data stream error` after switching inputs | Two input branches deliver different caps (e.g. different framerate), so `input-selector` cannot reuse the negotiated downstream caps | Reject mismatched inputs at start time (see Format validation). Never allow heterogeneous inputs to be wired into the same `input-selector` |
| `Internal data stream error` when switching live to a black-fill slot | `mxlsrc` runs on the live MXL clock and `videotestsrc` runs on the pipeline clock; their buffer caps also differ in fields the format-validation invariant doesn't pin down (`colorimetry`, `chroma-site`, `pixel-aspect-ratio`). Attempts to paper over this with `sync-streams`/`sync-mode`/`cache-buffers` properties on `input-selector` and a downstream `videoconvert` did not eliminate the error in practice | **Disable the Active-Input button for black-fill slots in the UI.** The black-fill branch is still kept in the pipeline graph (so the format-matching invariant holds and the slot layout stays uniform), but the live output can only be switched among real MXL slots. This is the pragmatic fix; switching among only MXL sources of identical format works flawlessly with the default `input-selector` properties |
| Black fill slot stalls the pipeline when wired | `videotestsrc` defaults to `is-live=false`, which does not match `mxlsrc` (live source) | Set `is-live=true` on every `videotestsrc` used as a fill slot |
| Output flow has wrong raster after switch to black | Black fill caps were derived from a hard-coded default rather than from the validated common format | Always read `frame_width`, `frame_height`, `grain_rate`, `interlace_mode` from the validated input format and inject them into the black-fill capsfilter |
| Switching is not seamless (visible glitch) | `input-selector` requires both the active and inactive branches to be producing buffers in PLAYING state | Keep all three branches running in PLAYING state at all times; only change `active-pad` to switch — never set unused branches to NULL/PAUSED |

Please write the necessary Dockerfile, Python backend scripts, React components, and integration code following these guidelines at `./gst-apps/input-selector`, modifying content already present.
