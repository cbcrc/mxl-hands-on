# AI Agent Instructions: GStreamer Test Generator

## Project Overview
Build a Dockerized media application that functions as a video and audio test generator. The application uses GStreamer for media functionalities, FastAPI for the backend API, and React + Vite for the web frontend. The final goal is illustrated in the mermaid diagram located here `./Exercises/Exercise5.md`. The application is located at `./gst-apps/test-generator`. NMOS has been removed entirely — there is no NMOS node, no nmos_bridge, and no NMOS-related dependencies.

## System Architecture
The application runs as a **single service on one port** inside Docker:
1. **FastAPI (port 9600):** Serves both the REST API and the built React static files (mounted via `StaticFiles`). The `StaticFiles` mount is added **last**, after all API routes, so API paths take precedence.
2. **Frontend:** Built with React + Vite into a `dist/` directory. No separate static file server — FastAPI serves `dist/` directly via `fastapi.staticfiles.StaticFiles`.
3. **Media Engine:** GStreamer via `gi.repository.Gst` Python bindings handles the actual media pipeline.

This single-port design means the browser always uses the **same origin** for both the UI and the API, so no port number is hardcoded in the frontend JavaScript. The docker-compose host-port mapping (`9600:9600`) can be changed freely without rebuilding the image.

## Environment & File Specifications
- **Media Format:** The test generator must offer the following video raster resolutions: **1280x720**, **1920x1080**, and **3840x2160**, and the following frame rates: **24 Hz**, **25 Hz**, **29.97 Hz**, **30 Hz**, **50 Hz**, **59.94 Hz**, and **60 Hz**. For audio it must offer a choice of **1 to 64 channels at 48 kHz 24-bit** (delivered as F32LE to mxlsink).
- **GStreamer Pipeline:** The pipeline is user-controlled (start/stop). It can dynamically change video and audio test patterns and outputs them via the purpose-built mxl sink (see `./dmf-mxl/rust/gst-mxl-rs/readme.md` for usage). The pipeline produces **one video flow**, **two independent audio flows** (Audio Flow 1 and Audio Flow 2), and **one optional Ancillary Data flow** that carries **both closed captions and SCTE-104** ANC, muxed together (mirroring a real VANC space).
- **mxlsink format requirements:** `mxlsink` only accepts specific formats. A `capsfilter` with `video/x-raw,format=v210` **must** be placed immediately before every mxl video sink, a `capsfilter` with `audio/x-raw,format=F32LE,layout=interleaved` **must** be placed immediately before every mxl audio sink, and a `capsfilter` with `meta/x-st-2038,alignment=frame,framerate=<fn>/<fd>` **must** be placed immediately before the mxl **data** sink (the Ancillary Data flow). Without these explicit capsfilters, auto-negotiation may land on an incompatible format and cause a hard failure at runtime.
- **The Ancillary Data flow lives in its OWN pipeline:** the captions + SCTE branches are NOT added to the video/audio pipeline. Their `appsrc`-fed, manually-timestamped branches otherwise perturb the shared pipeline clock/base-time at startup and corrupt the *first-buffer* time mapping of the video/audio mxlsinks — every flow then writes grains at the 1970 epoch (mxl-info shows latency ≈ today's Unix time, i.e. "~56 years"). It gets its **own** `Gst.Pipeline`(s), separate from A/V. `start()` must **not** block on `get_state()` for it — it holds the instance lock across the build and the push-buffer timer **and the playout thread** also take that lock, so blocking would stop them feeding the appsrc (the pipeline stalls ~10 s, then renders its first grain late, at the epoch). Set it PLAYING, register the `_push_caption` timer, start the `_anc_playout` thread, and return; they feed the live pipeline once the lock releases.
- **CEA-608 / ST 2038 elements (two pipelines + frame-paced playout):** captions + SCTE share one flow via a **harvest** pipeline and an **output** pipeline bridged in Python. Harvest: `appsrc(text) → tttocea608 pop-on → ccconverter → closedcaption/x-cea-608 → cctost2038anc → capsfilter(meta/x-st-2038) → appsink(ccsink)`. Output: `appsrc(ancsrc) → queue → mxlsink`. `tttocea608` emits each text buffer's `frames` (≈`CAPTION_PUSH_MS`) caption-ANC grains in an instant **burst**, so `ccsink`'s callback (`_on_caption_to_anc`) just **appends** their bytes to a bounded FIFO (`_anc_fifo`); a **frame-paced playout thread** (`_anc_playout`) pops ONE grain per frame at a monotonic cadence and pushes it to `ancsrc` with its own monotonic PTS, **appending one SCTE-104 ANC packet to the single grain emitted at trigger time** (`_scte_pending_event_id`, one-shot — *no burst*). This decouples SCTE injection from the bursty harvest: a trigger rides the **next grain (~1 frame, tens of ms)** instead of waiting up to `CAPTION_PUSH_MS` (~1 s avg) for the next harvest burst. A startup cushion of one burst (`_anc_cushion = frames`) keeps the FIFO from underflowing at the trough; on a rare underflow it repeats the last grain and still advances PTS so `mxlsink` never ratchets latency. **Tradeoff:** caption *display* gains ~`CAPTION_PUSH_MS` (~2 s) latency from the cushion (the SCTE event is what's optimised). `ccconverter` is in `gstreamer1.0-plugins-bad`; `tttocea608` / `cctost2038anc` come from **`gst-plugin-closedcaption`** in gst-plugins-rs (NOT apt-packaged), built in a dedicated Docker stage (see Step 1). (Dead ends, do not retry: `st2038ancmux` → jittery/undecodable grains; a single combined pipeline → epoch grains; pacing the harvest with `appsink sync=true` does **not** de-burst this chain — the playout thread reclocks it instead.)
- **SCTE-104:** the SCTE trigger is a **conformant SCTE-104** `multiple_operation_message` (one `splice_request_data` op, opID `0x0101`, incrementing `splice_event_id`) wrapped in an ST 2038 ANC packet on DID `0x41` / SDID `0x07` (SMPTE ST 2010). See `build_scte104_message` / `build_scte104_anc_packet`.
- **MXL Flow Identity:** Flow UUIDs are **deterministic** — derived via UUID v5 from a fixed application namespace and the name `"<grouphint>:<role>"` (e.g. `"Test-Generator:video"`). This means restarting the pipeline with the same group hint reuses the same UUIDs and overwrites the existing flow files in the domain, while changing the group hint produces a completely different set of UUIDs. The application namespace UUID is a constant defined in `gst_generator.py` (`_MXL_TGEN_NS`) and must never change after deployment, as doing so would orphan previously written flow directories. Once the pipeline is running and the mxl sink has written the flow to disk, the backend must poll until `{selected-domain-path}/{flow_uuid}.mxl-flow/flow_def.json` exists for each active flow, then patch that file: set `grouphint` to `"<user-grouphint>:Video"` for the video flow and `"<user-grouphint>:Audio"` for each audio flow; also update `tags["urn:x-nmos:tag:grouphint/v1.0"]` to `["<user-grouphint>:Video"]` or `["<user-grouphint>:Audio"]` respectively (if the `tags` object is present) so that `mxl-info` picks up the correct group name and role; and replace `description` and `label` with the values provided by the user in the Setup section.

## Required API & UI Functionalities

The UI is divided into two distinct sections: **Setup** and **Operation**.

---

### Section 1 — Setup

This section is used to configure the MXL flows before starting the GStreamer pipeline. The pipeline does **not** start until the user clicks the **Start** button. All controls in this section are disabled while the pipeline is running.

1. **MXL Domain Selector:** Scan `/mxl-domain` recursively for `domain_def.json` files. Read the `id` field for the domain UUID, the `label` and `description` fields, and use the containing directory path as the domain path (passed to `mxlsink`'s `domain` property). Provide a dropdown to select the target MXL domain — **each option displays the domain `label`** (falling back to the directory path when the label is absent) instead of the raw UUID.
2. **Resolution Selector:** A dropdown to select the output video raster: `1280x720`, `1920x1080`, or `3840x2160`. Default: `1920x1080`.
3. **Frame Rate Selector:** A dropdown to select the output frame rate: `24`, `25`, `29.97`, `30`, `50`, `59.94`, or `60` fps. Default: `30`.
4. **Group Hint:** A text input shared across all three flows. Default value: `Test-Generator`.
5. **Flow Configuration Table:** Four rows — Video, Audio Flow 1, Audio Flow 2, and **Ancillary Data** — each with:
   - **Active checkbox** — tick to include the flow in the pipeline. Video and the two audio flows are active by default; Ancillary Data is **off** by default.
   - **Channels** — numeric input (1–64) for audio flows only (Video / Ancillary Data rows show "—"). Default: `2`. Channel count is fixed for the lifetime of the pipeline; changing it requires a Stop + Start.
   - **Description** — text input unique to each flow. Defaults: `video-out-1`, `audio-out-1`, `audio-out-2`, `ancillary-out`.
   - **Label** — text input unique to each flow. Defaults: `video-test-pattern`, `audio-test-pattern-1`, `audio-test-pattern-2`, `ancillary-data`.
   - Description and Label are mandatory fields; the Start button is disabled until all active flows have non-empty values.
   - The Ancillary Data flow is tagged with the role `Ancillary Data` in its grouphint (so the consumer can filter it), e.g. `"<grouphint>:Ancillary Data"`.
6. **Start / Stop button** — starts the GStreamer pipeline with the configured flow metadata when clicked. Changes to a **Stop** button once the pipeline is running. Only active flows are included in the pipeline.

---

### Section 2 — Operation

This section is enabled only once the pipeline is running (greyed-out and non-interactive otherwise). It is divided into a **Video** panel and two independent **Audio Flow** panels.

**Video panel:**
1. **Select Video Test Pattern:** A dropdown to select any available GStreamer video test pattern. Default: `100% bars`.
2. **Timecode overlay:** A checkbox to burn timecode onto the video. Default: **on**.
3. **Ident:** A text box for an identification string overlaid on the video test pattern, applied via an **Apply** button (or Enter key).

**Audio Flow 1 panel & Audio Flow 2 panel** (independent controls for each):
1. **Select Audio Test Pattern:** A dropdown to select any available GStreamer audio test pattern. Default: `1 kHz tone`.
2. **Audio level:** A fader with 0.5 dB increments and a numerical readout. Default: `−20 dBFS`.

**Ancillary Data panel** (one flow carries captions + SCTE; all controls disabled when the flow is not active):
1. **Closed Captions:** A multi-line text box plus an **Apply Caption** button. The entered text is word-wrapped into ≤32-char rows (CEA-608's hard per-row limit) and **cycled one row at a time, looping**, so the player shows continuously scrolling, repeating captions. A pulsing green **LOOPING** tally next to the button lights while a caption is actively looping (running + ancillary active + non-empty text).
2. **SCTE-104 Trigger:** A **⚡ Trigger SCTE** button that injects one conformant SCTE-104 message (ANC packet) into the same Ancillary Data flow. A readout shows the running trigger count and the last-trigger timestamp (`HH:MM:SS.mmm`).

> Channel count is configured in the **Setup** section before starting the pipeline and cannot be changed while it is running.

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
- Add port mapping `9600:9600` only — FastAPI serves both the API and the React frontend on the same port. No separate frontend port is needed.
- The Dockerfile uses **three stages**: a `node:18-bullseye-slim` stage to build the React frontend, an `ubuntu:24.04` **`rscc-builder`** stage that compiles the closed-caption GStreamer plugin, and an `ubuntu:24.04` runtime stage. There is no NMOS stage.
- **`rscc-builder` stage:** clones `gst-plugins-rs` tag `0.14.5` and runs `cargo build --release -p gst-plugin-closedcaption`, producing `libgstrsclosedcaption.so` (the source of `tttocea608` / `cctost2038anc`). 0.14.x is the first series with the ST 2038 elements and targets the GStreamer 1.24 ABI from Ubuntu 24.04 apt. It needs rust (installed via rustup) plus the dev packages `libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev libpango1.0-dev libcairo2-dev` (the plugin bundles `cea608overlay`, which links pango/cairo). The runtime stage then `COPY --from=rscc-builder`s the `.so` into `/usr/lib/x86_64-linux-gnu/gstreamer-1.0/`. The pango/cairo runtime libs it needs are already pulled in by `gstreamer1.0-plugins-base`.
- The build context is the repository root (`..` relative to `./gst-apps/`). All `COPY` paths in the Dockerfile are therefore relative to the repository root (e.g. `COPY gst-apps/test-generator/backend/ /app/backend/`).
- The runtime stage installs: `python3`, `python3-pip`, `python3-gi`, `python3-gi-cairo`, `gir1.2-gstreamer-1.0`, `gir1.2-gst-plugins-base-1.0`, `gstreamer1.0-tools`, `gstreamer1.0-plugins-base`, `gstreamer1.0-plugins-good`, `gstreamer1.0-plugins-bad`, `gstreamer1.0-plugins-ugly`, `gstreamer1.0-libav`.
- Copy `libgstmxl.so` to `/usr/lib/x86_64-linux-gnu/gstreamer-1.0/` (the default GStreamer plugin path on Ubuntu 24.04 — no `GST_PLUGIN_PATH` env var is needed). Copy `libmxl.so.1.2` to `/opt/mxl/lib/` and create the expected symlinks (`libmxl.so.1` → `libmxl.so.1.2`, `libmxl.so` → `libmxl.so.1`). Set `ENV LD_LIBRARY_PATH=/opt/mxl/lib`.
- Create the symlink that `libgstmxl.so` needs (it `dlopen()`s `libmxl.so` from its compile-time build path):
  ```
  && mkdir -p /workspace/mxl/build/Linux-Clang-Release/lib \
  && ln -sf /opt/mxl/lib/libmxl.so /workspace/mxl/build/Linux-Clang-Release/lib/libmxl.so
  ```

**Step 2: FastAPI & GStreamer Backend**
- Create a FastAPI application on port 9600. No NMOS bridge or startup event is needed.
- Implement a `GstGenerator` class using `gi.repository.Gst`. The pipeline is **not** started at init — only when `start(config)` is called explicitly.
- **Setup endpoints:**
  - `GET /domains` — scan `/mxl-domain` recursively for `domain_def.json` files; return `path` (containing directory), `id` (UUID from JSON `id` field), `label`, and `description` per domain. The frontend dropdown displays the `label` (fallback to path) instead of the UUID.
  - `GET /patterns` — return lists of available video and audio test pattern names.
  - `GET /options` — return lists of available resolutions and frame rates.
  - `POST /pipeline/start` — accepts `domain` (path), `grouphint`, `resolution`, `framerate`, and per-flow config. `video` has `active`, `description`, `label`. `audio1` and `audio2` additionally have `channels` (int, 1–64, default 2). On each call: (1) set audio channel counts from config, (2) generate a fresh `uuid4` for every active flow, (3) build and start the GStreamer pipeline, (4) return once the pipeline reaches PLAYING state, (5) in a background thread, poll until `{domain-path}/{flow_uuid}.mxl-flow/flow_def.json` exists for each active flow, then patch `grouphint` (as `"<grouphint>:Video"` for the video flow and `"<grouphint>:Audio"` for audio flows), `tags["urn:x-nmos:tag:grouphint/v1.0"]` (same value, as a one-element array, if the `tags` key is present), `description`, and `label`.
  - `POST /pipeline/stop` — stops and tears down the GStreamer pipeline.
  - `GET /pipeline/status` — returns the full runtime state: `running` (bool), `flow_uuids` (dict), current video settings (`pattern`, `timecode`, `ident`, `resolution`, `framerate`), and per-flow audio settings (`pattern`, `channels`, `level_db`).
- **Operation endpoints (only functional while pipeline is running):**
  - `POST /video/test-pattern` — set the video test pattern `{"pattern": "..."}`.
  - `POST /video/timecode` — enable/disable timecode overlay `{"enabled": true}`.
  - `POST /video/ident` — set the ident overlay text `{"text": "..."}`.
  - `POST /audio/flow1/test-pattern` — set audio test pattern for Flow 1.
  - `POST /audio/flow1/level` — set audio level (dBFS) for Flow 1.
  - `GET  /audio/flow1/level` — read current audio level for Flow 1.
  - `POST /audio/flow2/test-pattern` — set audio test pattern for Flow 2.
  - `POST /audio/flow2/level` — set audio level (dBFS) for Flow 2.
  - `GET  /audio/flow2/level` — read current audio level for Flow 2.
  - `POST /captions/text` — set the looping caption text `{"text": "..."}` (re-wrapped and cycled; empty string stops the caption).
  - `POST /scte/trigger` — inject one SCTE-104 message into the Ancillary Data flow (409 if the flow is not active).
  - `GET /pipeline/status` additionally returns `ancillary: {active}`, `captions: {text}`, and `scte: {trigger_count, last_trigger_ts}`.
  - *(Channel count is not a runtime endpoint — it is passed in `POST /pipeline/start` and fixed for the pipeline lifetime.)*

- **`POST /pipeline/start`** also accepts an `ancillary` flow config (`{active, description, label}`, same shape as `video`).
- **GStreamer pipeline structure:**
  - Video: `videotestsrc → capsfilter(res+fps) → timeoverlay → textoverlay → videoconvert → capsfilter(v210) → queue → mxlsink`
  - Audio (per flow): `audiotestsrc → audioconvert → capsfilter(F32LE, N ch, 48 kHz, interleaved) → queue → mxlsink`
  - Ancillary Data (captions + SCTE on one flow; **two** separate pipelines bridged in Python + a frame-paced playout thread):
    ```
    harvest:  appsrc(text/x-raw,utf8) → tttocea608 pop-on → ccconverter → cea-608 caps
              → cctost2038anc → anc caps → appsink(ccsink)      # bursts ~CAPTION_PUSH_MS of grains
    _on_caption_to_anc: append each grain's bytes → _anc_fifo (bounded, leaky)
    _anc_playout (thread): pop ONE grain/frame at a monotonic cadence,
              append a pending SCTE-104 ANC packet, push → ancsrc
    output:   appsrc(ancsrc) → queue → mxlsink
    ```
    A GLib timer (`_push_caption`, every `CAPTION_PUSH_MS` = 2000 ms) pushes one wrapped caption row into the harvest chain and advances/wraps an index so the rows scroll and repeat; when there is no caption text it pushes a blank row so the flow keeps producing per-frame CC ANC grains. The harvest emits each row's grains in a **burst**, so they're queued in `_anc_fifo`; the **playout thread** reclocks them to frame rate and, on a trigger, appends one conformant SCTE-104 ANC packet to the **single grain** emitted at that moment (ST 2038 packets are byte-aligned, so concatenation is a valid multi-ANC grain). This makes the SCTE marker land within ~1 frame of the trigger rather than waiting for the next burst. **Pop-on** (not roll-up): pop-on loads each caption off-screen and flips it complete, so a single ≤32-char chunk round-trips cleanly; roll-up smears ~2 chars at each carriage-return boundary (e.g. `"to check"` → `"toheck"`). The cadence must stay ≥ the row's CEA-608 transmit time (~16 frames for 32 chars at 2 chars/frame).
  - Both `appsrc`s use a **manual PTS counter** (`do-timestamp=false`, `is-live=true`); the output grain's PTS comes from the playout thread (one frame period each), not the harvested grain's PTS (which can be invalid early → mxlsink epoch). (`do-timestamp=true`, an `st2038ancmux` mux, a single combined pipeline, and `appsink sync=true` pacing were all tried and produced jittery / epoch / still-bursting grains; the two-pipeline split + manual PTS + playout-thread reclock is what works.)
  - Video pattern, timecode, and ident are live-adjustable without rebuilding the pipeline. Audio pattern and level are also live. Caption text and SCTE triggers are live. Channel count changes require a rebuild.

**Step 3: React + Vite Frontend**
- Initialize a React + Vite project.
- **Header branding:** At the very top of the page, display the CBC Radio-Canada logo (`gst-apps/logo/rgb_cbc-radio-canada-col-coul.png`) inline beside the "MXL Test Generator" h1 title. Copy the logo into `frontend/public/cbc-logo.png` so Vite serves it as a static asset; reference it in JSX as `<img src="/cbc-logo.png" />` with `height: 2.2rem`. The logo and title must share a flex row (`display: flex; align-items: center; gap: 1rem`).
- Structure the UI into two clearly labelled sections:
  - **Setup section** (always visible, disabled while running): MXL domain dropdown, resolution dropdown, frame rate dropdown, shared grouphint input, a three-row flow configuration table (Video / Audio Flow 1 / Audio Flow 2) each with an active checkbox, description, and label inputs, and a Start/Stop button. The Start button is disabled until a domain is selected and all active flows have non-empty description and label values.
  - **Operation section** (greyed-out and non-interactive until the pipeline is running): a Video panel (test pattern dropdown, timecode checkbox, ident text input with Apply button) and two independent Audio panels (test pattern dropdown, channel count numeric input, audio level fader with dBFS readout).
- In `App.jsx`, set `const API = ""` so all fetch calls use relative paths (e.g. `${API}/pipeline/status`). This means the browser always calls the same origin as the page — no port number hardcoded, works regardless of the docker-compose host-port mapping.
- FastAPI serves the React static files directly: `app.mount("/", StaticFiles(directory="/app/frontend/dist", html=True), name="static")` must be the **last** statement in `backend/main.py` so API routes take precedence. Add `aiofiles>=23.0.0` to `requirements.txt` (required by `StaticFiles`).
- Update `vite.config.js` to proxy all API paths (`/domains`, `/patterns`, `/options`, `/pipeline`, `/video`, `/audio`) to `http://localhost:9600` for local development (Vite dev server only — not used in the Docker image). Set the Vite dev port to the app's docker-compose host port + 100 (convention across all gst-apps: host 9600 → dev 9700).

**Entrypoint** — single process, single port:
```bash
cd /app
exec python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 9600
```

**Step 4: Pipeline Documentation**
Once the application is fully working, update **Section 2 — Test Generator** in `./gst-apps/gstreamer-pipeline.md` to reflect the actual pipeline built. That section must contain:
- A `gst-launch-1.0` CLI equivalent showing the full pipeline (video branch + Audio Flow 1 branch + Audio Flow 2 branch), including all capsfilters, overlays, and mxl sinks with representative property values.
- A Mermaid `flowchart` diagram with one subgraph per branch (Video, Audio Flow 1, Audio Flow 2) clearly showing each element and its connections.
- A prose explanation of what the pipeline does, with justification for key plugin choices (e.g. why `videoconvert` is needed before the `v210` capsfilter, why `audioconvert` is used, why explicit capsfilters are mandatory for `mxlsink`, why channel count changes require a pipeline rebuild, etc.).

Please write the necessary Dockerfiles, Python backend scripts, React components, and integration code following these guidelines at `./gst-apps/test-generator`, modifying content already present.
