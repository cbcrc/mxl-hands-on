# AI Agent Instructions: MXL Input Selector (native Rust backend)

## Project Overview
A Dockerized **N-input → 1-output MXL video selector**. It takes up to `MAX_INPUTS` MXL video
flows as inputs and routes exactly one at a time to a single MXL output flow. The active input
can be switched live from the UI with **frame-accurate, glitch-free** results. Video only — no
audio. There is no NMOS control.

> **History:** the original implementation used a Python/FastAPI + GStreamer `input-selector`
> backend. It switched cleanly *most* of the time but not always (caps/timing races at the
> `active-pad` switch; black-fill slots could not be switched into live). That backend has been
> **replaced by a native Rust backend** (`backend-rs/`) built on the MXL Rust SDK
> (`dmf-mxl/rust/mxl`). The frontend (React + Vite) was kept, with minimal changes.

## Why the Rust approach is clean
MXL grain indices are **absolute (epoch/TAI)** — all same-rate flows share one index timeline.
The router writes output grain `N` by copying input grain `N` from whichever source is active.
Each output grain is committed atomically from exactly one source, so switching (flip an atomic
between iterations) always lands on a frame boundary → clean by construction. No caps
negotiation, no clock-domain mismatch. Only the active source is read per frame → cost is O(1)
regardless of input count, which is what makes `MAX_INPUTS` cheap to scale.

## Architecture
A single Rust binary (`input-selector-router`, axum + tokio) serves the REST API **and** the
built React frontend on **port 9600**. On `POST /pipeline/start` it spawns a dedicated OS thread
(the grain router); the MXL reader/writer objects live entirely in that thread.

### Backend layout (`backend-rs/src/`)
- `main.rs` — tracing init, build `AppState`, axum router, bind `0.0.0.0:$PORT` (default 9600).
- `api.rs` — `AppState`, typed serde DTOs, all handlers, `ServeDir` static serving (SPA
  fallback to `index.html`). Endpoint paths/shapes match the old FastAPI backend.
- `router.rs` — the grain router: `start_router()` spawns the thread and blocks until setup
  succeeds (so config errors surface synchronously), then the copy loop runs. `RouterHandle`
  holds an `AtomicUsize` active-slot, an `AtomicBool` stop, the join handle, and a last-error
  `Mutex`. `LAG_GRAINS = 2` (write this many grains behind the live edge so source grains are
  committed before reading).
- `scan.rs` — domain scan (`domain_def.json`) + flow listing via `mxl-info -d` (ported 1:1 from
  the old `main.py`, including the `Domain Definition:` header skip and group/role parsing).
- `format.rs` — `read_flow_format` + `validate_inputs` (returns `(common, errors, per_slot)`).
- `flowdef.rs` — deterministic output UUIDv5 (`MXL_SELECTOR_NS`, **immutable**) + derive the
  output `flow_def` from the first input's def + on-disk metadata patch.
- `black.rs` — `v210_black_grain(size)`: flat black (Y=0x040, Cb=Cr=0x200) tiled across the
  payload.

### Build (Docker, `mxl-not-built`)
The crate path-deps on `mxl` with the **`mxl-not-built`** feature, so `mxl-sys` binds against
the prebuilt MXL headers (no cmake/ninja) and `libmxl.so` is `dlopen`ed at runtime. The
Dockerfile has a `rust:1-bookworm` builder stage (installs `clang`/`libclang-dev` for bindgen)
that copies `dmf-mxl` → `/build/dmf-mxl`, so `get_mxl_so_path()` bakes
`/build/dmf-mxl/build/Linux-Clang-Release/lib/libmxl.so` — the runtime stage symlinks exactly
that path to `/opt/mxl/lib/libmxl.so`. **The prebuilt MXL C library and rust target must exist
under `dmf-mxl/` before building the image** (the maintainer builds them first).

Runtime stage (`ubuntu:24.04`) carries only `libmxl.so`, `mxl-info`, the binary, and the
frontend `dist/` — no GStreamer, no Python.

### Configuration (env)
- `MXL_DOMAIN` (default `/mxl-domain`) — domain root to scan.
- `MAX_INPUTS` (default 3) — number of selectable input slots (UI + router).
- `MXL_INFO_BIN` (default `/opt/mxl/tools/mxl-info/mxl-info`).
- `FRONTEND_DIST` (default `/app/frontend/dist`).
- `PORT` (default 9600).

Compose (`gst-apps/docker-compose.yml`) maps host `9604:9600` and passes
`MAX_INPUTS=${INPUT_SELECTOR_MAX_INPUTS:-3}`.

## API

| Method | Path | Notes |
|--------|------|-------|
| `POST` | `/get-domains` | rescan `MXL_DOMAIN`, return domain list |
| `GET`  | `/domains` | cached domain list (`id`, `label`, `description`, `path`) |
| `GET`  | `/scan-domain?domain_path=` | `mxl-info -d` parsed flow list (active flows only) |
| `POST` | `/pipeline/start` | validate formats; **400** `{detail, errors, per_slot}` on mismatch; else start + return status |
| `POST` | `/pipeline/stop` | stop the router |
| `GET`  | `/pipeline/status` | `{running, domain_path, input_flow_uuids, slot_kinds, active_input, output_flow_uuid, format, grouphint, description, label, error}` |
| `POST` | `/pipeline/active-input` | `{slot}` → flip the active atomic (409 if not running) |
| `GET`  | `/config` | `{max_inputs}` — the frontend renders this many slots |

> **Swagger/OpenAPI is deferred.** All request/response types are typed serde DTOs, so adding
> `utoipa` + `utoipa-swagger-ui` at `/docs` later is a small additive change (no handler
> refactor). Do this only when asked.

## Frontend (`frontend/src/App.jsx`)
React + Vite, dark theme, CBC logo header. Same two sections as before (**Setup** /
**Operation**). Changes from the GStreamer version:
- Slot count is dynamic: fetched from `GET /config` (`max_inputs`); slots render from
  `slotIndexes = Array.from({length: maxInputs})`. `inputs` is sized to `maxInputs`.
- **Black-fill slots are switchable live** — the Active-Input button is no longer disabled or
  greyed for black slots (the old "Black-fill slots cannot be switched to live" gating is gone).
- Start sends `input_flow_uuids` (length `maxInputs`, each UUID or `null`), `grouphint`,
  `description`, `label`. Format-mismatch 400 → red banner with `errors` + `per_slot`.

## Known pitfalls / design notes
| Issue | Note |
|-------|------|
| Source grain not ready when read | The router writes `LAG_GRAINS` behind the live epoch index so the source grain is already committed; `get_complete_grain` also has a timeout and the output grain is `cancel()`ed (not committed) on a read miss rather than committing garbage. |
| Heterogeneous inputs | The output flow has one fixed grain size; all selected inputs must share `frame_width/height`, `grain_rate`, `interlace_mode`. Enforced at `/pipeline/start` (HTTP 400). |
| All-black inputs | Rejected — the output format is derived from the first MXL input, so at least one real MXL input is required. |
| `grouphint` lost on flow creation | The on-disk `flow_def.json` is re-patched right after `create_flow_writer` (no polling — the file exists synchronously) to (re)assert `grouphint`/tags/description/label for NMOS discovery. |
| `MXL_SELECTOR_NS` | The UUIDv5 namespace in `flowdef.rs` must never change — doing so orphans previously written output flow directories. |
| Port already in use during local dev | The binary honours `PORT`; run on a free port when something else holds 9600. |
