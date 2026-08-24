<!--
SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
SPDX-License-Identifier: CC-BY-4.0
-->

# AI Agent Instructions: MXL ABI Tester

## Project Overview

Build a Dockerized bench instrument that exposes **every public C ABI call of the MXL SDK**
as a queueable step in a web UI. The operator composes parallel lanes of ABI calls, inserts
explicit delays between them, runs the sequence **slower than real time** (or paced to the
media clock), and reads every return value in a console. Sequences save to and load from
`.json` so a failure becomes a reproducible test scenario.

Each grain/sample step logs its media timestamp (OTS) beside the wall clock at which the
call actually happened, so **latency and propagation delay are measured, not guessed**.

The backend is a native **C++17** binary that calls the MXL ABI directly and also serves
the built React frontend. Keep code style consistent with `./gst-apps/mxl-info-gui`
(spec shape, UI theme) and `./gst-apps/input-selector` (native-backend Dockerfile shape).

## System Architecture

The application runs as a **single service on one port** inside Docker:

1. **C++ backend (port 9600):** a `cpp-httplib` server that serves both the REST API and
   the built React static files. The static mount is registered **last**, after all API
   routes, so API paths take precedence.
2. **Frontend:** React + Vite built into `dist/`, served by the C++ binary. No separate
   static file server, no Python.

Same-origin design: the browser uses one origin for both UI and API, so the docker-compose
host-port mapping (`9607:9600`) can change freely without rebuilding.

**Why the backend must be C/C++ and must be one long-lived process:** MXL handles
(`mxlInstance`, `mxlFlowWriter`, `mxlFlowReader`, `mxlFlowSynchronizationGroup`) are opaque
per-process pointers. A queued sequence only makes sense if every step runs in the process
that owns the handles. The lanes are therefore a fixed pool of `std::thread`s inside one
binary; they still meet through the same shared-memory domain files, exactly as separate
applications would.

## Environment & File Specifications

- **MXL public headers (source of truth):** `./dmf-mxl/lib/include/mxl/`
  — `mxl.h`, `flow.h`, `flowinfo.h`, `time.h`, `dataformat.h`, `rational.h`, `platform.h`.
  > ⚠️ `./dmf-mxl/build/<preset>/lib/include/mxl/` holds only the generated
  > `version.h`, which no public header includes. `./dmf-mxl/lib/include` alone is enough
  > to compile. There is **no** `mxl/dataflow.h` — all flow/grain/sample calls are in `flow.h`.
- **MXL shared library:** `./dmf-mxl/build/Linux-Clang-Release/lib/libmxl.so.1.1`
  → `/opt/mxl/lib/libmxl.so.1.1` in the image, with the conventional
  `libmxl.so.1` → `libmxl.so` symlink chain.
  > The build directory is named after the CMake preset that produced it, so a **native**
  > build outside Docker finds the library under `Darwin-Clang-Release/lib/libmxl.1.1.dylib`
  > on macOS. The image is always `linux/amd64`, so the Dockerfile paths above stay as they
  > are. `backend/CMakeLists.txt` picks the preset directory from the host and takes a
  > `-DMXL_PRESET=` override.
- **ABI reference documentation:** `./ea-ema-upp-mxl-sdk/Docs/C-MXL-ABI.md` — regenerated
  for `dmf-mxl` commit `84350f7c`, which is the pinned submodule HEAD. Call descriptions in
  the catalog are lifted from it. `Docs/Lexique.md` defines the terminology. The directory is
  a **separate clone and is git-ignored**, so it does not arrive with a `git pull`.
- **MXL domain scan root:** `MXL_DOMAIN_ROOT`, default `/Volumes/mxl` — the tree `/domains`
  walks. Distinct from the **instance domain**, which is a single domain directory passed as
  `argv[1]` and handed to `mxlCreateInstance`. In the container the scan root is the mounted
  Docker volume, `/mxl-domain`.
- **Scenario directory:** `/app/scenarios` (bind-mounted to `test-tools/ABI-tester/scenarios`).
- **Flow templates:** `/app/flows` (baked into the image from `flows/`).

Unlike the Rust apps (`input-selector`), a C++ build **links** `-lmxl` normally. There is
no `dlopen` of a baked absolute path, so the runtime stage needs **no symlink shim**.

## Core Concepts

Read this section before writing any code — three of these change the shape of the design.

### 1. Lanes

**Generic** lanes — any lane accepts any ABI call. Lanes are not "writer" and "reader":
that restriction would make it impossible to express writer-vs-writer conflict tests,
reader-only tests, or reader-before-writer races.

**A fixed pool of N, created once at startup**, N from `ABI_TESTER_LANES` (default 8, range
1..1024). This supersedes the original two-lane design, and is the one deviation that had to
land before the frontend: M14/M15 build their UI against whatever lane model exists, and
changing the lane count afterwards means rewriting JSX. The pool exists to load a machine
with many concurrent lanes and watch what that does to per-call latency.

Lane names are **bijective base 26** — `A`..`Z`, `AA`, `AB`, … — so `A` and `B` still mean
lanes 0 and 1 and every scenario file written against the two-lane design loads unchanged.
The pool is immutable after construction: lanes are never created or destroyed per scenario,
because the engine holds a raw `Lane*` across an unlocked `execute` and a lane must not die
under a thread standing in it. A scenario simply names whichever lanes it uses; loading one
empties every pool lane it does not name.

### 2. The index cursor — why the tool can run slower than real time

MXL indices are derived from real TAI time (`mxlGetCurrentIndex`), but **the ring buffer
is not**: it only advances when a writer commits. So a writer that writes index `N`, waits
five seconds, then writes `N+1` is perfectly legal, and a reader can follow it. What breaks
determinism is deriving each index from the wall clock at execution time.

Therefore each lane holds a **cursor**. A `setCursor` pseudo-step seeds it once (from a
literal, or from a single `mxlGetCurrentIndex()` call), and grain/sample steps take their
index from the cursor, optionally advancing it. The scenario controls the index sequence;
the wall clock does not.

### 3. OTS — there is no timestamp field in a grain

`mxlGrainInfo` (`dmf-mxl/lib/include/mxl/flow.h:126-148`) carries `version`, `size`,
`index`, `flags`, `grainSize`, `totalSlices`, `validSlices` — and **no timestamp**.
The index *is* the timestamp, quantized to the edit rate. The OTS must be derived:

```c
uint64_t ots_ns = mxlIndexToTimestamp(&editRate, grainInfo.index);
```

`mxlGetTime()` returns **TAI nanoseconds since the SMPTE ST 2059 (PTP) epoch**, the same
base, so `mxlGetTime() - ots_ns` is a meaningful age. `mxlFlowRuntimeInfo`
(`flowinfo.h:164-180`) additionally exposes `headIndex`, `lastWriteTime`, `lastReadTime`.

### 4. Semantics that are easy to get wrong

| Fact | Consequence for this tool |
|---|---|
| **Sample calls are backwards-looking.** `mxlFlowWriterOpenSamples(writer, index, count, …)` and `mxlFlowReaderGetSamples(reader, index, count, …)` address the `count` samples **ending at** `index`, i.e. `[index − count, index)`. Grain calls are not — `index` is the grain itself. | Label the index field differently per flow kind in the UI, and log the resolved range explicitly. |
| **`headIndex` means two different things.** Discrete: last committed grain index, *inclusive*; valid reads are `[headIndex − grainCount + 1, headIndex]`. Continuous: the *exclusive* end of the last committed range. | The `head` index mode and the timing panel must branch on flow kind. |
| **`mxlFlowWriterCommitGrain` validates the index** — returns `MXL_ERR_INVALID_ARG` unless `grain->index` equals the writer's currently-open index. | The engine must cache the `mxlGrainInfo` returned by `OpenGrain` and hand that same struct back. |
| **Repeated partial commits on one open grain are legal and intended.** The writer only closes the grain when `validSlices == totalSlices`. | A scenario may queue one `OpenGrain` followed by several `CommitGrain` steps with a rising `validSlices`. Expose `validSlices` and `flags` as editable fields. |
| **`mxlTimestampToIndex` rounds to nearest, not floor** (`IndexConversion.hpp` adds a half-interval before dividing). | Footnote it in the timing panel so a half-interval offset is not read as a bug. |
| **Continuous `count` must be ≤ `bufferLength / 2`**, which is what `mxlFlowWriterGetMaxWriteLengthSamples` / `…GetMaxReadLengthSamples` return. | Otherwise `MXL_ERR_INVALID_ARG`. Validate client-side and log the bound. |
| **`mxlFlowReaderGetSamples` never returns `MXL_ERR_TIMEOUT`** — a timeout surfaces as `MXL_ERR_OUT_OF_RANGE_TOO_EARLY`. | Do not treat the two as distinct in the audio scenarios. |
| **Time helpers return `MXL_UNDEFINED_INDEX` (`UINT64_MAX`)** on a null or zero edit rate, rather than a status code. | Render that value by name, never as `18446744073709551615`. |
| **Two options channels.** `mxlCreateFlowWriter`'s `options` takes plain keys `maxCommitBatchSizeHint` / `maxSyncBatchSizeHint` (both ≥ 1, sync a multiple of commit). The *domain* `options.json` takes the URN `urn:x-mxl:option:history_duration/v1.0`. `mxlCreateFlowReader`'s `options` is silently ignored in the current implementation. | Say so in the catalog descriptions. |
| **Flow definitions are validated**: `id`, `media_type`, non-empty `label`, a valid `urn:x-nmos:tag:grouphint/v1.0` tag, and `grain_rate` (video/data) or `sample_rate` (audio) are required. | A malformed template fails as `MXL_ERR_UNKNOWN`, which is unhelpful — validate in the UI before submitting. |
| **The continuous-writer wakeup bug is fixed in the pinned SDK.** `commit()` assigns `flow->info.runtime.headIndex` *before* clearing `_currentIndex`, and `signalCompletedBatch()` reads `headIndex` (`PosixContinuousFlowWriter.cpp:113-114, 141`). Verified: a blocking read with a 3000 ms deadline returned in **506.0 ms** against a writer committing at +0.5 s, and **505.3 ms** for a 100-sample commit landing mid-batch, so wakeup is not quantised to sync-batch boundaries either. | Blocking audio reads are usable, and lanes may pace on them — no short-hop polling. **Still untested:** repeated small commits *inside one sync batch*, where `signalCompletedBatch()`'s `currentSyncSampleBatch == _lastSyncSampleBatch` branch only signals once `headIndex % syncBatchSize` passes `_earlySyncThreshold`. That is the shape a real audio writer produces — make it a bundled scenario. |

## The ABI Call Catalog

`libmxl.so.1.1` exports **exactly 42 functions**: 5 from `mxl.h`,
30 from `flow.h`, 7 from `time.h`. All 42 must be queueable. The backend holds this as one
table and serves it at `GET /abi-calls`.

> The completeness check is a **diff, not a count** — a count of 42 also passes with one name
> typo'd and one call missing. Compare `curl -sS localhost:9600/abi-calls | jq -r '.[].name'
> | sort` against the library's exported symbols: `nm -D --defined-only libmxl.so.1.1 | grep
> '^mxl'` on Linux, or `nm -gU libmxl.1.1.dylib | grep ' _mxl'` on macOS, where Mach-O
> prefixes symbols with `_` and `nm -D` does not exist. Both spellings return 42.

**The frontend renders its call palette and every argument form from that endpoint**, so the
two cannot drift.

### `instance` — `mxl.h` (5)

| Call | Signature |
|---|---|
| `mxlGetVersion` | `mxlStatus mxlGetVersion(mxlVersionType*)` |
| `mxlCreateInstance` | `mxlInstance mxlCreateInstance(char const* domain, char const* options)` — returns `NULL` on failure, not a status |
| `mxlGarbageCollectFlows` | `mxlStatus mxlGarbageCollectFlows(mxlInstance)` |
| `mxlIsTmpFs` | `mxlStatus mxlIsTmpFs(char const* path, bool* out)` |
| `mxlDestroyInstance` | `mxlStatus mxlDestroyInstance(mxlInstance)` |

### `flow-lifecycle` — `flow.h` (6)

| Call | Signature |
|---|---|
| `mxlCreateFlowWriter` | `mxlStatus mxlCreateFlowWriter(mxlInstance, char const* flowDef, char const* options, mxlFlowWriter*, mxlFlowConfigInfo*, bool* created)` |
| `mxlReleaseFlowWriter` | `mxlStatus mxlReleaseFlowWriter(mxlInstance, mxlFlowWriter)` |
| `mxlCreateFlowReader` | `mxlStatus mxlCreateFlowReader(mxlInstance, char const* flowId, char const* options, mxlFlowReader*)` |
| `mxlReleaseFlowReader` | `mxlStatus mxlReleaseFlowReader(mxlInstance, mxlFlowReader)` |
| `mxlIsFlowActive` | `mxlStatus mxlIsFlowActive(mxlInstance, char const* flowId, bool*)` |
| `mxlGetFlowDef` | `mxlStatus mxlGetFlowDef(mxlInstance, char const* flowId, char* buffer, size_t* bufferSize)` — two-pass: size, then fetch |

### `flow-info` — `flow.h` (3)

`mxlFlowReaderGetInfo(mxlFlowReader, mxlFlowInfo*)`,
`mxlFlowReaderGetConfigInfo(mxlFlowReader, mxlFlowConfigInfo*)`,
`mxlFlowReaderGetRuntimeInfo(mxlFlowReader, mxlFlowRuntimeInfo*)`.

### `grain-read` — `flow.h` (4)

| Call | Signature |
|---|---|
| `mxlFlowReaderGetGrain` | `(mxlFlowReader, uint64_t index, uint64_t timeoutNs, mxlGrainInfo*, uint8_t** payload)` |
| `mxlFlowReaderGetGrainSlice` | `(mxlFlowReader, uint64_t index, uint16_t minValidSlices, uint64_t timeoutNs, mxlGrainInfo*, uint8_t**)` |
| `mxlFlowReaderGetGrainNonBlocking` | `(mxlFlowReader, uint64_t index, mxlGrainInfo*, uint8_t**)` |
| `mxlFlowReaderGetGrainSliceNonBlocking` | `(mxlFlowReader, uint64_t index, uint16_t minValidSlices, mxlGrainInfo*, uint8_t**)` |

`minValidSlices` accepts the symbolic `MXL_GRAIN_VALID_SLICES_ANY` (0) and
`MXL_GRAIN_VALID_SLICES_ALL` (`UINT16_MAX`) — offer both by name in the form.

### `grain-write` — `flow.h` (4)

| Call | Signature |
|---|---|
| `mxlFlowWriterGetGrainInfo` | `(mxlFlowWriter, uint64_t index, mxlGrainInfo*)` — inspection-only peek |
| `mxlFlowWriterOpenGrain` | `(mxlFlowWriter, uint64_t index, mxlGrainInfo*, uint8_t** payload)` |
| `mxlFlowWriterCancelGrain` | `(mxlFlowWriter)` — resets the tracked index; does **not** roll back bytes already written |
| `mxlFlowWriterCommitGrain` | `(mxlFlowWriter, mxlGrainInfo const* grain)` |

### `sample-read` — `flow.h` (3)

`mxlFlowReaderGetMaxReadLengthSamples(mxlFlowReader, size_t*)`,
`mxlFlowReaderGetSamples(mxlFlowReader, uint64_t index, size_t count, uint64_t timeoutNs, mxlWrappedMultiBufferSlice*)`,
`mxlFlowReaderGetSamplesNonBlocking(mxlFlowReader, uint64_t index, size_t count, mxlWrappedMultiBufferSlice*)`.

### `sample-write` — `flow.h` (4)

`mxlFlowWriterGetMaxWriteLengthSamples(mxlFlowWriter, size_t*)`,
`mxlFlowWriterOpenSamples(mxlFlowWriter, uint64_t index, size_t count, mxlMutableWrappedMultiBufferSlice*)`,
`mxlFlowWriterCancelSamples(mxlFlowWriter)`,
`mxlFlowWriterCommitSamples(mxlFlowWriter)`.

### `syncgroup` — `flow.h` (6)

`mxlCreateFlowSynchronizationGroup(mxlInstance, mxlFlowSynchronizationGroup*)`,
`mxlReleaseFlowSynchronizationGroup(mxlInstance, mxlFlowSynchronizationGroup)`,
`mxlFlowSynchronizationGroupAddReader(group, reader)`,
`mxlFlowSynchronizationGroupAddPartialGrainReader(group, reader, uint16_t minValidSlices)`,
`mxlFlowSynchronizationGroupRemoveReader(group, reader)`,
`mxlFlowSynchronizationGroupWaitForDataAt(group, uint64_t timestamp, uint64_t timeoutNs)` —
note it takes a **timestamp**, not an index, because flows tick at different edit rates.

### `time` — `time.h` (7)

`mxlGetCurrentIndex(mxlRational const*)`, `mxlGetNsUntilIndex(uint64_t, mxlRational const*)`,
`mxlTimestampToIndex(mxlRational const*, uint64_t)`, `mxlIndexToTimestamp(mxlRational const*, uint64_t)`,
`mxlSleepForNs(uint64_t)`, `mxlSleepUntil(uint64_t)`, `mxlGetTime(void)`.

These return raw `uint64_t`, not `mxlStatus`. Log the value, and render
`MXL_UNDEFINED_INDEX` by name.

### Non-ABI pseudo-calls

`setCursor` — lane bookkeeping only. `repeat` — `{"call":"repeat","args":{"to":"a5",
"times":54000}}`, a backward-only jump resolved to a step position at load time, so a
30-minute flow is a few steps rather than 108,000.

Both are **deliberately absent from `/abi-calls`**, which keeps that endpoint diffable
against `nm`, and both **must be visually marked in the UI as not ABI calls** so a scenario
is never mistaken for a pure ABI trace.

### Not queueable

The four `dataformat.h` helpers (`mxlIsValidDataFormat`, `mxlIsSupportedDataFormat`,
`mxlIsDiscreteDataFormat`, `mxlIsContinuousDataFormat`) are header-only inlines, not
exported symbols. The backend uses them internally to branch discrete vs continuous; they
are not steps.

## Backend Functionalities

### 1. `scan_domains`

C++ port of `_scan_domains()` / `_read_buffer_depth()` in
`gst-apps/mxl-info-gui/backend/main.py`. Walk `MXL_DOMAIN_ROOT` with
`std::filesystem::recursive_directory_iterator` looking for `domain_def.json`:

```json
{ "id": "71ef9b5c-98c1-4f98-9def-1d61ee9a4fdb",
  "label": "dev-domain",
  "description": "mxl-domain-dev-gst-apps" }
```

Then read `options.json` in the **same directory** for
`urn:x-mxl:option:history_duration/v1.0` (nanoseconds → ms; default **200 ms** when the
file is absent, the key missing, or the file unreadable).

Per domain store: `id`, `label`, `description`, `path`, `buffer_depth_ms` (float),
`buffer_depth_is_default` (bool). Runs once at startup, re-triggerable via API.

### 2. Handle registry

`std::map<std::string, Entry>` keyed by an operator-chosen name (`"A"`, `"W1"`, `"R1"`).
`Entry` holds:

- the kind — `instance` / `writer` / `reader` / `syncgroup` / `grain`
- the raw handle
- for writers and readers: the cached `mxlFlowConfigInfo` and the flow id — `common.grainRate`
  is needed for **every** index↔timestamp conversion, and `common.format` decides discrete
  vs continuous
- for `grain` entries: the `mxlGrainInfo` returned by `mxlFlowWriterOpenGrain`

Mutex-protected; both lane threads share it. Creation steps name their output via
`"out": { … }`; consuming steps reference by name.

### 3. Step schema

```json
{
  "id": "s5",
  "call": "mxlFlowWriterOpenGrain",
  "delay_before_ms": 1000,
  "args": { "writer": "W1", "index": { "mode": "cursor", "offset": 0 } },
  "out": { "grain": "g" },
  "fill": { "mode": "ramp", "stamp": true },
  "advance_cursor": 1,
  "note": "first grain"
}
```

Index arguments accept four modes — this is the heart of deterministic execution:

| mode | meaning |
|---|---|
| `literal` | the typed value |
| `cursor` | the lane cursor + `offset` |
| `current` | `mxlGetCurrentIndex(&editRate)` at execution time (real-time behaviour, for comparison) |
| `head` | `mxlFlowReaderGetRuntimeInfo(…).headIndex + offset` — inclusive for discrete, exclusive for continuous |

For continuous flows `advance_cursor` is naturally the batch `count`, since `index` is the
exclusive end of the range.

Commit steps reference the cached grain info:

```json
{ "id": "s6", "call": "mxlFlowWriterCommitGrain",
  "args": { "writer": "W1", "grain": "g", "validSlices": "all", "flags": [] } }
```

`validSlices` accepts `"all"`, a literal count, or `"+N"` (increment), so a scenario can
queue several commits against one open grain to exercise slice-by-slice writing. `flags`
is a list — currently only `MXL_GRAIN_FLAG_INVALID`, which is how a scenario advances the
ring over deliberately bad data.

### 4. Payload fill and stamping

`fill.mode` is one of `none`, `const` (with `byte`), or `ramp`.

`fill.stamp: true` additionally writes a 24-byte record at payload offset 0: a magic word,
the grain index, and `mxlGetTime()` at the moment of the fill. Reader steps may set
`"verify_stamp": true`; the reader parses that record back and logs

```
payload_transit_ns = read_wall_ns − stamped_write_ns
```

This is the only true end-to-end measurement available. OTS alone tells you the media time
a grain *represents*, not when it physically became visible to a consumer. Log both.

For continuous flows, write the stamp into the first fragment of channel 0. Access pattern
for wrapped multi-buffer slices (channel `c`, fragment `f`):

```c
uint8_t* p = (uint8_t*)slices.base.fragments[f].pointer + c * slices.stride;
size_t   n = slices.base.fragments[f].size;   // skip when 0; fragment[1] is the post-wrap remainder
```

### 5. Event log

Append-only `std::vector<Event>` behind a mutex, monotonic `seq`. Every executed step emits
exactly one event:

```json
{
  "seq": 42, "lane": "A", "step_id": "s5",
  "call": "mxlFlowWriterOpenGrain",
  "t_wall_ns": 1778847026700056836,
  "duration_us": 37,
  "status": "MXL_STATUS_OK", "status_code": 0,
  "args_resolved": { "index": 106730821602 },
  "out": {
    "grainInfo": { "index": 106730821602, "flags": 0, "grainSize": 5529600,
                   "totalSlices": 1080, "validSlices": 0 },
    "ots_ns": 1778847026666666666,
    "age_ns": 33390170
  }
}
```

`ots_ns = mxlIndexToTimestamp(editRate, index)`; `age_ns = t_wall_ns − ots_ns`. Reader
events additionally carry `payload_transit_ns` when stamp verification is on.

Render **every** `mxlStatus` by enum name, never as a bare integer:

```
MXL_STATUS_OK(0)  MXL_ERR_UNKNOWN(1)  MXL_ERR_FLOW_NOT_FOUND(2)
MXL_ERR_OUT_OF_RANGE_TOO_LATE(3)  MXL_ERR_OUT_OF_RANGE_TOO_EARLY(4)
MXL_ERR_INVALID_FLOW_READER(5)  MXL_ERR_INVALID_FLOW_WRITER(6)
MXL_ERR_TIMEOUT(7)  MXL_ERR_INVALID_ARG(8)  MXL_ERR_CONFLICT(9)
MXL_ERR_PERMISSION_DENIED(10)  MXL_ERR_FLOW_INVALID(11)
MXL_ERR_STRLEN(1024) and the following fabrics range
```

### 6. Engine

Two `std::thread` lane executors. Each walks its own step list: sleep
`delay_before_ms × delay_scale`, resolve arguments, invoke, append the event, advance the
cursor. Modes:

- `run` — every lane free-running
- `pause` — finish the current step, stop before the next
- `step` — execute exactly one step in a named lane
- `reset` — release every handle in reverse creation order, clear the log and both cursors

A global `delay_scale` multiplier slows a whole scenario without editing every step.

### 7. Scenario persistence

A scenario is `{ "name", "description", "lanes": { "A": [step…], "B": [step…] } }`.
Load/save `.json` under `/app/scenarios`. Reject path separators in the name.

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness plus `sdk_version`, `domain`, `tmpfs` |
| `POST` | `/domains/scan` | Rescan `MXL_DOMAIN_ROOT`; return the updated domain list |
| `GET` | `/domains` | Cached domain list |
| `GET` | `/abi-calls` | The call catalog — drives the UI palette and every argument form |
| `GET` | `/flow-templates` | Bundled `flows/*.json` names + contents |
| `POST` | `/call` | **Ad-hoc: run one ABI call now**, outside any lane. `{"call":…,"args":{…}}`. Logs against lane `"-"` |
| `GET` / `POST` | `/scenario` | Get / replace the in-memory scenario (all lanes) |
| `GET` | `/scenarios` | List saved scenario files |
| `GET` / `POST` | `/scenarios/{name}` | Load / save a scenario `.json` |
| `POST` | `/run` | Start every lane; optional `delay_scale` |
| `POST` | `/pause` | Stop before the next step |
| `POST` | `/step` | `{ "lane": "A" }` — execute one step in one lane |
| `POST` | `/reset` | Release all handles, clear the log |
| `GET` | `/log?since=<seq>` | Events after `seq` |
| `GET` | `/state` | Lane positions, running flags, `lane_pool`, live handle table |

> ⚠️ `POST /call` and `POST /step` are **different endpoints with confusable names**. `/call`
> runs one ABI call immediately; `/step` advances one lane by one queued step. `/step` meant
> the former before the engine landed, so older recipes read backwards.

Log delivery is **polling, not SSE** — the tool runs slower than real time, so a 250 ms
poll is ample and avoids chunked-transfer complexity in httplib.

## Required UI Functionalities

1. **Domain panel** — "Scan Domains" button calling `POST /domains/scan`; a table of
   Label / ID / Path / Buffer Depth (dimmed `(default)` annotation when falling back to
   200 ms); a selector whose chosen path becomes the default `mxlCreateInstance` argument.
2. **Flow template panel** — pick `video` or `audio`, view and edit the `flow_def.json`
   inline, with a "regenerate id" toggle so each run can create a fresh flow. Validate the
   required fields (`id`, `media_type`, non-empty `label`, grouphint tag, `grain_rate` or
   `sample_rate`) before allowing a run.
3. **Lane builders.** Each has an "Add call" dropdown grouped by catalog category, a step
   list with up / down / delete, a per-step argument form generated from the catalog, and a
   `delay before (ms)` field. Index fields are labelled per flow kind — "grain index" for
   discrete, "end index (range is index − count … index)" for continuous — so the
   backwards-looking sample addressing is visible in the form rather than a trap.

   > **Open decision for M14: how many builders are on screen at once.** The original
   > `gridTemplateColumns: "1fr 1fr"` assumed exactly two lanes; the pool is now up to 1024,
   > and drawing a builder per pool slot is not a design. The endpoint is already shaped for
   > this — `/state` returns **only lanes that have steps**, plus a scalar `lane_pool` — so
   > the UI can render the occupied lanes and offer the rest through a picker. Settle this
   > before writing JSX, not during.
4. **Transport bar** — Run / Pause / Reset, a per-lane Step control (not one button per pool
   slot — see the open decision above), a delay-scale multiplier, and the live handle table
   from `/state`.
5. **Console** — monospace scrolling window, one line per event, colour-coded by status
   (green `MXL_STATUS_OK`, amber out-of-range and timeout, red everything else), click a
   line to expand the full JSON. Lane filter. Polls `/log?since=<seq>` every **250 ms**.
6. **Timing panel** — a derived table keyed by index: write OTS, write wall, read wall,
   `age_ns`, `payload_transit_ns`. This is the answer to "measure latency and propagation
   delay". Footnote that `mxlTimestampToIndex` rounds to nearest, so a half-interval
   discrepancy is expected behaviour.
7. **Scenario panel** — name field, Save, load dropdown, plus browser download / upload of
   the `.json`.

> ⚠️ Guard every list response before calling `.map()` — `Array.isArray(d?.x) ? d.x : []` —
> to avoid a blank-page crash when the backend returns an error object.

## Step-by-Step Implementation Guide

### Step 1: Docker Setup

Build context is the **repository root**; all `COPY` paths are relative to it
(e.g. `COPY test-tools/ABI-tester/backend/ /build/backend/`). Three stages, following
`gst-apps/input-selector/Dockerfile`.

**Stage 1 — frontend:**
```dockerfile
FROM node:18-bullseye-slim AS frontend-builder
WORKDIR /build
COPY test-tools/ABI-tester/frontend/package.json \
     test-tools/ABI-tester/frontend/package-lock.json* ./
RUN npm install --legacy-peer-deps
COPY test-tools/ABI-tester/frontend/ ./
COPY gst-apps/logo/rgb_cbc-radio-canada-col-coul.png ./public/cbc-logo.png
RUN npm run build
```

**Stage 2 — C++ backend.** Ubuntu 24.04 ships both third-party dependencies, so nothing
is vendored:
```dockerfile
FROM ubuntu:24.04 AS backend-builder
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        g++ cmake make nlohmann-json3-dev libcpp-httplib-dev \
    && rm -rf /var/lib/apt/lists/*

COPY dmf-mxl/lib/include /opt/mxl/include
COPY dmf-mxl/build/Linux-Clang-Release/lib/libmxl.so.1.1 /opt/mxl/lib/libmxl.so.1.1
RUN cd /opt/mxl/lib \
 && ln -sf libmxl.so.1.1 libmxl.so.1 \
 && ln -sf libmxl.so.1   libmxl.so

COPY test-tools/ABI-tester/backend /build/backend
WORKDIR /build/backend
RUN cmake -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build -j
```
Compile flags: `-std=c++17 -I/opt/mxl/include -L/opt/mxl/lib -lmxl -pthread`.

**Stage 3 — runtime:**
```dockerfile
FROM ubuntu:24.04
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        libcpp-httplib0.14t64 curl \
    && rm -rf /var/lib/apt/lists/*

COPY dmf-mxl/build/Linux-Clang-Release/lib/libmxl.so.1.1 /opt/mxl/lib/libmxl.so.1.1
RUN cd /opt/mxl/lib \
 && ln -sf libmxl.so.1.1 libmxl.so.1 \
 && ln -sf libmxl.so.1   libmxl.so \
 && ldconfig /opt/mxl/lib

COPY dmf-mxl/build/Linux-Clang-Release/tools/mxl-info/mxl-info /opt/mxl/tools/mxl-info/mxl-info
RUN chmod +x /opt/mxl/tools/mxl-info/mxl-info

COPY --from=backend-builder /build/backend/build/abi-tester /app/abi-tester
COPY --from=frontend-builder /build/dist /app/frontend/dist
COPY test-tools/ABI-tester/flows     /app/flows
COPY test-tools/ABI-tester/scenarios /app/scenarios
COPY test-tools/ABI-tester/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV LD_LIBRARY_PATH=/opt/mxl/lib
ENV MXL_DOMAIN_ROOT=/mxl-domain
ENV FRONTEND_DIST=/app/frontend/dist
ENV SCENARIO_DIR=/app/scenarios
ENV FLOW_DIR=/app/flows
EXPOSE 9600
ENTRYPOINT ["/entrypoint.sh"]
```

`mxl-info` is copied for cross-checking convenience only; the backend does not depend on it.

**`test-tools/docker-compose.yml`:**
```yaml
name: test-tools

volumes:
  mxl-domain:
    external: true
    name: gst-apps_mxl-domain

services:
  abi-tester:
    platform: linux/amd64
    build:
      context: ..
      dockerfile: test-tools/ABI-tester/Dockerfile
    image: abi-tester:latest
    container_name: abi-tester
    hostname: abi-tester
    domainname: local
    ports:
      - "9607:9600"   # C++ backend serves both API and React frontend
    volumes:
      - type: volume
        source: mxl-domain
        target: /mxl-domain
      - type: bind
        source: ./ABI-tester/scenarios
        target: /app/scenarios
    environment:
      - MXL_DOMAIN_ROOT=/mxl-domain
```

> ⚠️ The external volume requires the `gst-apps` stack to have created it. Run
> `docker compose -f gst-apps/docker-compose.yml up -d` at least once first.

### Step 2: C++ Backend

Layout under `test-tools/ABI-tester/backend/`:

| File | Responsibility |
|---|---|
| `CMakeLists.txt` | C++17, find `nlohmann_json` and `httplib`, link `mxl` and `pthread` |
| `src/main.cpp` | httplib server, route registration, static mount **last** |
| `src/domains.{hpp,cpp}` | `scan_domains` / buffer-depth reading |
| `src/registry.{hpp,cpp}` | named handle table |
| `src/calls.{hpp,cpp}` | the 42-call catalog **and** its adapters, one table |
| `src/engine.{hpp,cpp}` | the lane pool, transport, event log |
| `src/scenario.{hpp,cpp}` | scenario `.json` load/save |

> **One table, not two.** The original layout split `catalog.{hpp,cpp}` from
> `invoke.{hpp,cpp}` — two parallel lists of 42 that must stay in lockstep, with nothing to
> catch a half-defined call. Instead a single `CallSpec` carries the name, header,
> description, `std::vector<ParamSpec>` **and** the
> `std::function<json(Registry&, json const&)>` adapter.

Constants:
```cpp
const char* MXL_DOMAIN_ROOT = getenv("MXL_DOMAIN_ROOT") ?: "/Volumes/mxl";
const char* FRONTEND_DIST   = getenv("FRONTEND_DIST")   ?: "/app/frontend/dist";
const char* SCENARIO_DIR    = getenv("SCENARIO_DIR")    ?: "/app/scenarios";
const char* FLOW_DIR        = getenv("FLOW_DIR")        ?: "/app/flows";
constexpr double  DEFAULT_BUFFER_DEPTH_MS = 200.0;
constexpr auto    HISTORY_DURATION_KEY = "urn:x-mxl:option:history_duration/v1.0";
```
Plus `ABI_TESTER_LANES` (pool size, default 8) and `ABI_TESTER_LOG_FILE` (NDJSON event log;
unset means no file). In the container `MXL_DOMAIN_ROOT` is set to `/mxl-domain`.

**The server states its environment on startup**, in the terminal it was launched from:
`Domain`, `Domain root`, `Scenario dir`, `Lanes`. A default that is only implied by silence
is a default nobody checks — every env var above that changes behaviour gets a line.

The static mount goes after every route, mirroring the `StaticFiles` ordering rule in
`mxl-info-gui`:
```cpp
srv.set_mount_point("/", FRONTEND_DIST);
```
with an SPA fallback that serves `index.html` for unmatched non-API paths.

Enable permissive CORS so the Vite dev server on 9707 can reach the API.

### Step 3: React + Vite Frontend

- `package.json` targeting Vite 5 and React 18; dev script
  `vite --host 0.0.0.0 --port 9707` (docker-compose host port 9607 + 100, the convention
  across every app in this repo).
- `vite.config.js` proxies each API path individually to `http://localhost:9600` — the
  same style as `mxl-info-gui`, no `/api` prefix.
- `const API = ""` — all fetches are same-origin relative paths, so no port is hardcoded.
- Dark theme, inline style objects at the top of `App.jsx` under a `// ── Shared styles ──`
  banner. Tokens: page `#0f0f0f`, section card `#1c1c1c` (radius 8px, padding 1.5rem),
  text `#e0e0e0`, muted `#aaa` / `#888` / `#666`, inputs `#2a2a2a` on `1px solid #444`,
  primary button `#0d7c3e`, accent `#4caf50`, mono blocks `#111` with `#c8e6c9` text,
  table borders `#333` / `#222`, group header rows `#252525` on `#5b9bd5`.
- Header row: `/cbc-logo.png` at `height: 2.2rem` beside an `<h1>` "MXL ABI Tester" in a
  flex row (`gap: 1rem`), with a dimmed one-line subtitle.
- Page root `maxWidth: "1400px"` — wider than the 960px of `mxl-info-gui`, because of the
  two side-by-side lane builders.

### Step 4: Entrypoint

```bash
#!/bin/bash
# SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
# SPDX-License-Identifier: Apache-2.0
# Entrypoint for the MXL ABI Tester container.
# The native C++ backend serves both the REST API and the built React frontend on port 9600.

set -e

exec /app/abi-tester
```

### Step 5: Bundled flow templates and scenarios

**`flows/video-1080p2997-v210.json`** — derived from
`dmf-mxl/examples/flow-configs/flow-video-v210.json`: `media_type: "video/v210"`,
`grain_rate: { numerator: 30000, denominator: 1001 }`, 1920×1080 progressive BT709, three
components. **`flows/audio-48k-2ch-f32.json`** — derived from
`dmf-mxl/examples/flow-configs/flow-audio.json`: `media_type: "audio/float32"`,
`sample_rate: { numerator: 48000 }`, `channel_count: 2`, `bit_depth: 32`.

Set both `label` and the `urn:x-nmos:tag:grouphint/v1.0` tag to name this tool
(e.g. `"ABI-Tester:Video"`), since higher-level applications rely on those for discovery.

Six bundled scenarios:

| File | What it demonstrates |
|---|---|
| `01-video-write-read.json` | Baseline discrete round trip, one grain per second |
| `02-audio-write-read.json` | Baseline continuous round trip. Blocking accessor — wakeup works on the pinned SDK; use it to exercise repeated small commits inside one sync batch |
| `03-late-reader.json` | `MXL_ERR_OUT_OF_RANGE_TOO_LATE` |
| `04-writer-conflict.json` | Two writers on one flow → `MXL_ERR_CONFLICT` |
| `05-slice-write.json` | One `OpenGrain`, several partial `CommitGrain` |
| `06-flow-invalid.json` | Writer restart → `MXL_ERR_FLOW_INVALID` recovery |

> As of 2026-08-24 only `01` exists. The Verification section below is written against all
> six, so M16 is not complete until the other five are written.

### Step 6: Documentation

Add the app to the repo's app inventory (`gst-apps/README.md` "Apps at a glance" and URL
tables, or a new `test-tools/README.md`) with its port and one-line purpose.

SPDX headers on every new file: `2026 CBC/Radio-Canada`, Apache-2.0 for code and
configuration, CC-BY-4.0 for documentation.

## Verification

1. `docker compose -f gst-apps/docker-compose.yml up -d` (creates the volume and the
   domain), then `docker compose -f test-tools/docker-compose.yml up --build -d`.
2. Open `http://localhost:9607` — the domain scan lists the dev domain with its buffer depth.
3. Load **`01-video-write-read.json`** and press Run. Lane A creates an instance and a
   video writer, seeds its cursor from `mxlGetCurrentIndex()`, and writes 10 grains one
   second apart; lane B creates an instance and a reader after a 500 ms delay and reads the
   same 10 indices. Every event shows `MXL_STATUS_OK`; the timing panel shows
   `payload_transit_ns` in the low milliseconds while `age_ns` grows by ~1 s per grain —
   proving the run is deterministic and decoupled from real time.
4. Cross-check with `mxl-info-gui` (`http://localhost:9699`): the flow appears in the
   domain with `Active: true`, and its `Head index` matches the last index written.
5. Load **`02-audio-write-read.json`**. Confirm `args_resolved` shows the range as
   `[index − count, index)`, and that `count` never exceeds
   `mxlFlowWriterGetMaxWriteLengthSamples`. The blocking read must **wake on the commit**,
   not run to its deadline — compare `waited_ms` against the writer's offset. When a read
   does time out it surfaces as `MXL_ERR_OUT_OF_RANGE_TOO_EARLY`, never `MXL_ERR_TIMEOUT`,
   so `waited_ms` is the only way to tell an expired deadline from an unreachable index.
6. Load **`03-late-reader.json`** — the reader requests an index older than
   `head − grainCount`. Expect `MXL_ERR_OUT_OF_RANGE_TOO_LATE`, rendered by name in amber.
7. Load **`04-writer-conflict.json`** — both lanes open a writer on the same flow. Expect
   `MXL_ERR_CONFLICT` on the second.
8. Load **`05-slice-write.json`** — lane A does one `OpenGrain` then four `CommitGrain`
   steps at `validSlices` 270 / 540 / 810 / `"all"`, one second apart; lane B polls with
   `mxlFlowReaderGetGrainSliceNonBlocking` at `minValidSlices = 270`. Expect the reader to
   see the grain at 270 slices and `MXL_ERR_OUT_OF_RANGE_TOO_EARLY` before that. This also
   verifies the engine hands the cached `mxlGrainInfo` back correctly — a mistake there
   surfaces immediately as `MXL_ERR_INVALID_ARG` on the commit.
9. Load **`06-flow-invalid.json`** — lane A releases its writer and recreates the flow
   while lane B holds a reader. Expect `MXL_ERR_FLOW_INVALID` on the next read. Use the
   scenario to settle whether releasing and recreating the *reader* is sufficient recovery
   (what `dmf-mxl/tools/mxl-gst/sink.cpp` does) or whether the *instance* must be recreated
   — a question this tool exists to answer definitively.
10. Press Reset; the handle table empties and `mxl-info-gui` reports the flow as
    inactive/stale.
11. Save a scenario from the UI, confirm the `.json` appears in
    `test-tools/ABI-tester/scenarios/`, reload it, and confirm the run reproduces.

Please write the Dockerfile, C++ backend, React frontend, entrypoint, flow templates and
bundled scenarios at `./test-tools/ABI-tester/` following these guidelines.
