<!--
SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
SPDX-License-Identifier: CC-BY-4.0
-->

# Test Tools

Bench instruments for the [MXL SDK](../dmf-mxl). Separate Docker stack from
[`gst-apps/`](../gst-apps/README.md), but it attaches to the **same MXL domain**, so the two
compose files are meant to run side by side.

## Apps at a glance

| App | Image | URL | What it does |
|-----|-------|-----|--------------|
| [ABI Tester](#abi-tester) | `abi-tester:latest` | http://localhost:9607 | Exposes all 42 MXL C ABI calls as queueable steps across parallel lanes, runs them slower than real time, and measures OTS-derived latency and end-to-end propagation delay |

---

## ABI Tester

A C++ backend links `libmxl` directly and serves both the REST API and a React frontend on one
port. Nothing is simulated: every row in the event log is one real call through the C ABI, with
its own `duration_us`.

**What it is for.** Answering questions about the SDK that the headers do not answer — what a
status code actually means, what a call does at a boundary, which of two recovery strategies
works. The bundled scenarios are the accumulated answers; see
[`ABI-tester/FINDINGS.md`](./ABI-tester/FINDINGS.md).

### Prerequisites

The compose file consumes the **external** volume `gst-apps_mxl-domain`, so the `gst-apps` stack
must have created it at least once:

```sh
docker compose -f gst-apps/docker-compose.yml up -d
```

### Run it

```sh
cd ~/mxl-hands-on
docker compose -f test-tools/ABI-tester/docker-compose.yml up --build -d
```

Then open **http://localhost:9607**. Build context is the repository root; the build reads about
12 MB of it (see `ABI-tester/Dockerfile.dockerignore`).

### Using it

1. **Domains** — the panel lists every domain found under `MXL_DOMAIN_ROOT`, with its buffer depth.
2. **Builder** — pick a call from the 42-entry catalog, fill the generated form, queue it on a
   lane. Lanes run in parallel threads and never share handles.
3. **Transport** — Run / Pause / Step per lane / Reset, with a delay scale that multiplies every
   `delay_before_ms` (it does **not** affect `pace`d steps, which aim at their grain's own OTS).
4. **Console and timing panel** — every call as it happens, and the derived latency table:
   write OTS, write wall clock, read wall clock, `age_ms`, `transit_ms`.

Scenarios saved from the UI land in `ABI-tester/scenarios/` in the working tree, because compose
bind-mounts `ABI-tester/scenarios` over `/app/scenarios`. A new scenario file is picked up without a
rebuild.

### Bundled scenarios

| File | What it demonstrates |
|---|---|
| `01-video-write-read` | Baseline discrete round trip, one grain per second |
| `02-audio-write-read` | Baseline continuous round trip. A blocking read that **wakes on the commit** (~500 ms into a 3 s deadline) next to one that expires (500 ms of 500 ms) — both return through `waited_ms`, and a timeout never surfaces as `MXL_ERR_TIMEOUT` |
| `03-late-reader` | Both edges of the ring on one reader: `MXL_ERR_OUT_OF_RANGE_TOO_LATE` behind it, `MXL_ERR_OUT_OF_RANGE_TOO_EARLY` ahead |
| `04-writer-conflict` | That there is **no** writer exclusion: `MXL_ERR_CONFLICT` is declared and never returned, and `mxlIsFlowActive` means "someone holds this flow open". Releasing the *last* writer deletes the flow |
| `05-slice-write` | One `OpenGrain`, four partial commits at 270 / 540 / 810 / 1080 slices; `headIndex` advances on the **first** partial commit |
| `06-flow-invalid` | A stale reader returns `MXL_STATUS_OK` with **stale data**; `MXL_ERR_FLOW_INVALID` appears only on the `TOO_EARLY` path. Recovery needs every reader reference dropped — **not** a new instance |
| `07-sync-group` | A synchronization group waits for its **slowest** member — it is a rendezvous, not a realigner. Video + audio, audio silent for 4 s and video then 2 frames late; the wait grows by exactly the laggard's delay. **Found an SDK bug:** a group with more than one member stops synchronizing after its first successful wait and returns `MXL_STATUS_OK` instantly |

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `MXL_DOMAIN_ROOT` | `/mxl-domain` | Root scanned for domains (any directory containing `domain_def.json`) |
| `SCENARIO_DIR` | `/app/scenarios` | Where scenarios are listed from and saved to |
| `FRONTEND_DIST` | `/app/frontend/dist` | Built React app; if absent the server runs API-only |
| `ABI_TESTER_LANES` | `8` | Lane pool size (`A`..`H`) |
| `ABI_TESTER_LOG_FILE` | — | Mirror the event log to a file |
| `ABI_TESTER_LOG_MAX_BYTES` | unlimited | Cap the in-memory log; older events are evicted and `/log` answers `410` with a cursor to re-sync |
| `MXL_LOG_LEVEL` | — | The SDK's own spdlog level, e.g. `debug`. Invaluable when the library, not the tool, is the suspect |

The domain the *instances* open is a command argument, not an environment variable:
`abi-tester [alias=]<path> ...`, defaulting to `default=/mxl-domain` in the entrypoint. Override
it with a compose `command:` to bind several aliased domains at once.

### Native development loop

Docker is only needed to ship it. Against the host tmpfs domain:

```sh
export MXL_DOMAIN=/Volumes/mxl/domain_1
cd test-tools/ABI-tester/backend && cmake --build build && ./build/abi-tester $MXL_DOMAIN
cd ../frontend && npm run dev          # Vite on 9707, proxying the API to 9600
```

`source ABI-tester/dev.sh` adds the curl helpers used throughout the findings.

### API

| Route | Purpose |
|---|---|
| `GET /health`, `GET /domains`, `GET /abi-calls` | SDK version, domain scan, the 42-call catalog with parameter schemas |
| `POST /call` | Run one call ad hoc. Needs `"domain"` spelled out on `mxlCreateInstance` |
| `GET/POST /scenario`, `GET/POST /scenarios/<name>` | The loaded scenario, and the files on disk |
| `POST /run`, `/pause`, `/step`, `/reset` | Transport. All four answer with a full `/state` |
| `GET /state`, `GET /log?since=<seq>` | Lanes, handles, and the event log (a bare array; `410` carries a re-sync cursor) |

### Known SDK issues these scenarios surfaced

- **A synchronization group with two or more members stops synchronizing after its first successful
  `WaitForDataAt`**, returning `MXL_STATUS_OK` in ~0.0003 ms thereafter
  (`FlowSynchronizationGroup.cpp:112` misuses `forward_list::splice_after`). Workaround: a fresh
  group per wait, or one member per group. Single-member groups are unaffected. `07-sync-group`
  demonstrates both the failure and the workaround side by side.
- `MXL_ERR_CONFLICT` is declared and never returned; there is no writer exclusion (`04`).
- Releasing the **last** writer deletes the flow directory (`04`, `06`).
- A stale reader returns `MXL_STATUS_OK` with stale data; `MXL_ERR_FLOW_INVALID` only ever appears
  on the `TOO_EARLY` path (`06`).

### A caveat worth knowing

The container runs as **root**, so flows it creates are root-owned in the domain. A container
killed with `docker stop` leaves its flows behind as *stale* — the state
`mxlGarbageCollectFlows` exists to clean up, and the tool can call it on itself.
