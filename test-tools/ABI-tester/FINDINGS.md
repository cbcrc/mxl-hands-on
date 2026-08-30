# MXL under load — what the ABI-tester measured

Findings from the M11.5 load sweep, 2026-08-30. Everything here is measured on this
machine with the tooling in this directory; the reproduction recipe is at the end.

These are notes for **someone building a real media function on MXL**, not a description
of the tester. The single most useful item is §1.

---

## Environment

| | |
|---|---|
| CPU | AMD Ryzen 9 7900X3D — 12 cores / 24 threads, 128 MiB L3 across 2 CCDs |
| RAM | 62 GB; MXL domain on a 10 GB `tmpfs` at `/Volumes/mxl` |
| SDK | `dmf-mxl`, `Linux-Clang-Release` |
| Flow | 1080p29.97 v210 — grain **5,529,600 B**, `grain_count` **5** (ring = 26.4 MiB/flow) |
| Load | N threads, each writing its own flow, paced to every grain's OTS with ±2 ms jitter |
| Run | 600 grains/lane (~20 s). **First 5 grains of every lane excluded** from all statistics — see §5 |

---

## 1. 80% of the cost of writing a grain is the payload — so render *into* the grain

Per-grain cost budget, single lane, measured:

| | |
|---|---:|
| `mxlFlowWriterOpenGrain` (call itself) | **18–34 µs** |
| writing the 5.27 MiB payload | **180 µs** |
| `mxlFlowWriterCommitGrain` | **11 µs** |
| | |
| MXL's own overhead | ~45 µs |
| **payload as a share of total** | **80%** |

MXL's API cost is small and, as §2 shows, does not grow with load. What costs is the one
pass over the frame. That pass is **unavoidable in principle but very easy to pay twice**.

`mxlFlowWriterOpenGrain` hands back a pointer directly into the shared-memory ring slot:

```c
mxlStatus mxlFlowWriterOpenGrain(mxlFlowWriter writer, uint64_t index,
                                 mxlGrainInfo* mxlGrainInfo, uint8_t** payload);
//                                                           ^^^^^^^^^^^^^^^^^
//  \param[out] payload  The requested grain payload.
```

> **Recommendation — open the grain first, render directly into `payload`, then commit.**
>
> The tempting structure is: decode/scale/composite into your own buffer, then open a grain
> and `memcpy` into it. That pays the full-frame pass **twice** — once producing, once
> copying — and the copy is pure overhead.
>
> Invert it. Call `mxlFlowWriterOpenGrain` *before* you produce the frame, hand `payload` to
> your renderer / decoder / colour converter as its output buffer, and `mxlFlowWriterCommitGrain`
> when it returns. MXL's cost then really is ~45 µs per grain instead of ~225 µs.
>
> At 1080p29.97 v210 that is a **4–5× reduction in non-application cost per frame**, and the
> saving scales with resolution: at UHD the copy alone is ~720 µs a frame.
>
> `mxlFlowWriterCancelGrain` exists for the failure path — if the render fails after you have
> opened the grain, cancel it rather than committing a half-written frame.

The same argument applies on the read side: `mxlFlowReaderGetGrain*` gives you a pointer into
the ring. Decode/process from it in place where the downstream API allows a caller-supplied
input pointer, rather than copying out first.

---

## 2. The MXL API path does not degrade with load. Memory bandwidth does.

Removing only the payload write (`fill: {mode: "none"}`) and changing nothing else:

| lanes | variant | `OpenGrain` call p50 | payload p50 | `late_ms` p50 |
|---:|---|---:|---:|---:|
| 96 | normal | 2044.8 µs | 1582.4 µs | 0.76 ms |
| 96 | **payload removed** | **18.3 µs** | 1.7 µs | **0.06 ms** |
| 256 | normal | 41.3 µs | 490.0 µs | **391.82 ms** |
| 256 | **payload removed** | **18.3 µs** | 1.5 µs | **0.06 ms** |

**`OpenGrain` costs 18.3 µs at 96 lanes and 18.3 µs at 256 lanes** — identical, and identical
to the single-lane figure. With the payload write gone, 256 concurrent paced writers hold the
media clock to **60 µs**. Every bit of the degradation in the "normal" rows is the memset.

So when a loaded MXL system misses its deadlines, look at memory bandwidth first. The API is
not where the time goes.

**Measured sustained ceiling: ~38 GB/s of grain writes**, i.e. **~230 concurrent 1080p29.97
v210 flows** on this machine. At 256 lanes the offered load is 40.4 GB/s, the system falls
~10% behind real time, and `late_ms` climbs to 392 ms.

⚠️ **CPU utilisation is a misleading indicator here.** The system monitor showed ~30% at 96
lanes and ~40% at 256 — 2.5× the work for 1.33× the CPU. A thread stalled on memory still
counts as "busy", so that 40% is not 60% of headroom. `late_ms` and achieved GB/s are the
honest signals.

---

## 3. Phase, not average load, sets your p99

Every writer seeded its clock at the same moment, so all N targeted the same grain boundary
and fired together. Adding a per-lane `pace.offset_ms` spread across the 33.3667 ms grain
period — **same lane count, same total bandwidth, same work, only the phase changed**:

| 96 lanes | `OpenGrain` call p50 | payload p50 | `late_ms` p50 | phase σ |
|---|---:|---:|---:|---:|
| all in phase | 2044.8 µs | 1582.4 µs | 0.76 ms | 4.12 ms |
| **staggered** | **19.9 µs** | **182.4 µs** | **0.06 ms** | 9.63 ms |
| | **103× better** | **8.7× better** | | |

Staggered, 96 concurrent writers perform like **one** writer (182.4 µs against a single-lane
180 µs). In phase, the offered average is 15.9 GB/s — under half the ceiling — yet 96 × 5.27 MiB
= **506 MiB arrives compressed into a few milliseconds** instead of spread over 33.4.

Phase σ is the diagnostic: 9.63 ms is exactly `33.3667/√12`, a uniform spread. At ≤32 lanes
in-phase it measures 1.10–1.17 ms, which is precisely `stddev(uniform(−2,+2)) = 4/√12 = 1.155`
— i.e. the *only* spread present was the jitter we injected.

> **Recommendation — deliberately stagger the phase of independent writers.**
>
> In a genlocked plant this is the real deployment condition, not a test artefact: everything
> is locked to one clock, so everything bursts together. Give each writer a fixed offset within
> the grain period. Mixed frame rates decorrelate for free; a single rate does not.
>
> Note the counterintuitive corollary: at 256 lanes the system is *saturated*, pacing has
> collapsed, and the writers have naturally desynchronised — which gives it **better** per-call
> latency (490 µs) than the unsaturated but synchronised 96 (1582 µs). A spread-out heavy load
> beats a bursty light one.

*Untested:* whether staggering also helps at 256. It should improve latency, but cannot create
bandwidth — the average demand there already exceeds the ceiling.

---

## 4. Instances, flows, and where the limits actually are

**Use one `mxlInstance` per process per domain, with many readers and writers on it.**
Not one instance per flow. Three reasons, in order of weight:

1. **Flow synchronization groups are instance-scoped.** `createFlowSynchronizationGroup()` is a
   method on `Instance`, so flows in *different* instances cannot be time-aligned to each other.
   The per-flow model doesn't just cost more — it removes the capability.
2. **Reader sharing is instance-scoped.** `getFlowReader` refcounts and returns an existing
   `FlowReader` for the same flow id. Two components sharing one instance share one mapping;
   through separate instances they each map it independently.
3. **You'd spend the scarce resource to save the abundant one** — see the table below.

### Limits found, none of them documented

| limit | value | why |
|---|---|---|
| **inotify instances** | **128 per _user_** | every `mxlCreateInstance` builds a `DomainWatcher` → one `inotify_init1`. Shared with the desktop session, so ~64 were actually available. Watches, by contrast, are per-*flow* inside that one fd, capped at 65,536 — a 512× ratio pointing the other way. |
| **file descriptors** | **~7 per flow writer** | default soft `ulimit -n` of 1024 caps a process at **~145 flows**. `ulimit -n 65536` fixes it; no root needed. |
| **memory bandwidth** | **~38 GB/s** | ≈ 230 concurrent 1080p29.97 v210 flows on this host. |
| **writer creation** | **~8 ms per flow, serialised** | `Instance::createFlowWriter` holds `Instance::_mutex` across the whole creation. 256 writers took **2.13 s** wall to create. |

That last one is a **startup** cost, not a steady-state one — `OpenGrain`/`CommitGrain` never
take that mutex, which is why §2's 18.3 µs is flat at 256 lanes. Sharing an instance is still
the right call; just budget for serialised setup on a large fan-out, and don't create flows
on a latency-sensitive path.

### Both limits fail badly

- `mxlCreateInstance` returns a bare `nullptr` with no errno and no status code. The
  `inotify_init1` failure is logged internally (`DomainWatcher.cpp:55`) and then discarded by
  the `catch` in `mxl.cpp`.
- Descriptor exhaustion surfaces to the caller as `Input/output error` on `flow_def.json` and
  then as a generic `MXL_ERR_UNKNOWN`. The true cause — `Too many open files` — appears only in
  a **cleanup warning** at `FlowManager.cpp:187`, at `warning` level, on a different line.

If you are debugging either, **read the library's stderr**; the API return value will send you
somewhere else.

---

## 5. Pacing: sleep at the edges, be event-driven in the middle

- **Sources** (SDI ingest, generators) must be paced — their rate *is* the clock. These are the
  ones to phase-stagger per §3.
- **Transforms** (read A → process → write B) should **not** self-pace against the grain rate.
  Drive them from a blocking read on the input. Output is then paced by input, and each stage's
  phase is naturally offset by the previous stage's processing time — which decorrelates the
  herd for free.

Do **not** read this as "don't sleep". Deadline sleeping is what makes an idle stage free: an
earlier measurement in this project found a 5 ms idle poll costing **48.7% of a core at 512
lanes**, which a condition variable took to **0.0%**. An unpaced stage with nothing to do is a
busy-wait.

### The first `grain_count` grains of any flow measure page faults, not MXL

`fill_us` for a fresh flow, one lane:

```
1821  1730  2331  1745  2282  |  162  212  178  178 ...
└──── exactly grain_count (5) ────┘   └── steady state ──┘
```

First-touch faults on the ring's mmap; once it wraps, every slot is faulted in. ~10× the
steady-state cost. Any benchmark shorter than a ring wrap is measuring the kernel's page
allocator. **Discard the first `grain_count` grains** — all figures above do.

---

## 6. Interop: a real GStreamer consumer reads what this tool writes

While 8 lanes wrote concurrently, `mxlsrc` consumed lane A's flow:

```
rendered: 294, dropped: 0, current: 29.92, average: 30.08
Execution ended after 0:00:10.010061585
```

300 grains at 30000/1001 is exactly 10.0100 s; the pipeline ran 10.010061585 s — **62 µs over
ten seconds (6 ppm), zero dropped buffers, no warnings.** `mxl-info -l` lists every flow by its
grouphint name, and `mxl-info -f` reports `Active: true`, `Grain count: 5`,
`Latency (grains, ms): 1`.

```bash
gst-launch-1.0 -v mxlsrc domain=/Volumes/mxl/domain_1 \
    video-flow-id=<flow-id> num-buffers=300 \
  ! video/x-raw,framerate=30000/1001 \
  ! fpsdisplaysink video-sink=fakesink sync=false text-overlay=false
```

The downstream framerate capsfilter is required — `mxlsrc` needs it to negotiate.

Note the flow definition must carry a **`urn:x-nmos:tag:grouphint/v1.0` tag**: `validateGroupHint`
in `FlowParser.cpp:114` is mandatory, and omitting it fails `mxlCreateFlowWriter` with a bare
`MXL_ERR_UNKNOWN`.

---

## 7. The GStreamer plugin pays the payload tax in both directions

`gst-mxl-rs` copies the full frame on each side. It is a shim between MXL and GStreamer
memory, not a native zero-copy binding — but the two copies do **not** have the same status.

**Read side — `mxlsrc/create_discrete.rs:101`:**

```rust
DiscreteFormat::Video => gst::Buffer::from_slice(grain_data.payload.to_vec()),
//                                                             ^^^^^^^^^
```

`.to_vec()` heap-allocates 5.27 MiB and copies the grain out of the ring, every frame.

**Write side — `mxlsink/render_discrete.rs:75-77`:**

```rust
let destination = access.payload_mut();
let copy_len = std::cmp::min(destination.len(), payload.len());
destination[..copy_len].copy_from_slice(&payload[..copy_len]);
```

~180 µs per frame in each direction at 1080p29.97 v210 (§1), so an `mxlsrc → … → mxlsink`
chain pays it twice on top of whatever the elements in between do.

### The read-side copy is currently forced; the write-side one is not

**`mxlsrc` cannot safely avoid it today, because MXL has no reader-side grain pin.**
`mxlFlowReaderGetGrain` returns a pointer into the ring with no matching release call, and the
writer overwrites that slot after `grain_count` grains regardless of any reader — **167 ms at
29.97 with `grain_count` 5**. A `GstBuffer` handed downstream can easily outlive that inside a
`queue`, an encoder, or a muxer, and would tear. The copy buys lifetime decoupling, which is a
real purchase, not an oversight. Removing it needs either an MXL API addition (hold/release a
slot) or a deeper ring plus a policy bet about downstream latency.

> **Cheap improvement that keeps the safety:** the copy is forced, the *allocation* is not.
> `gst::Buffer::from_slice(…to_vec())` mallocs and frees 5.27 MiB per frame — 158 MB/s of
> allocator churn at 1080p29.97. A `GstBufferPool` of reusable buffers keeps the copy and
> removes the churn.

**`mxlsink` can avoid its copy with a standard GStreamer mechanism.** Implement
`propose_allocation()` offering a `GstBufferPool` whose memory is backed by grain payloads;
upstream then renders directly into the grain and the sink only commits. That is §1's
"render into the grain" expressed in GStreamer's own idiom, and it is the change with the
better return of the two — it removes a full-frame pass with no safety trade.

*Not benchmarked here* — this section is a source reading (`gst-mxl-rs` at
`dmf-mxl/rust/gst-mxl-rs`), not a measurement.

---

## Reproducing

```bash
cd test-tools/ABI-tester
ulimit -n 65536
./sweep.sh "1 8 32 96 256" 599        # ~4 min; writes /tmp/abi-load-<N>.ndjson
```

Then, in one query over every run — `filename=true` recovers the lane count, and
`t_wall_ns::BIGINT` is exact where `jq`'s doubles quantise at ~256 ns:

```sql
WITH ev AS (
  SELECT regexp_extract(filename,'abi-load-(\d+)',1)::INT AS lanes,
         lane, call, duration_us, t_wall_ns::BIGINT AS t,
         fill.fill_us AS fill_us, pace.late_ms AS late_ms,
         duration_us - coalesce(fill.fill_us,0) AS abi_us,
         row_number() OVER (PARTITION BY filename, lane, call ORDER BY seq) AS n
  FROM read_json_auto('/tmp/abi-load-*.ndjson', filename=true, union_by_name=true)
  WHERE ok AND step_id IN ('s5','s6')
)
SELECT lanes, replace(call,'mxlFlowWriter','') AS call, count(*) AS n,
       round(quantile_cont(abi_us,0.50),1)      AS abi_p50_us,
       round(quantile_cont(fill_us,0.50),1)     AS payload_p50_us,
       round(quantile_cont(duration_us,0.99),1) AS p99_us,
       round(quantile_cont(late_ms,0.50),3)     AS late_p50_ms,
       round(stddev((t % 33366667)/1e6),2)      AS phase_sd_ms
FROM ev WHERE n > 5          -- drop the page-fault region; see §5
GROUP BY 1,2 ORDER BY 2,1;
```

The two diagnostics in §2 and §3 are one-line edits to `gen-load.sh`'s `s5` step:

```jq
fill:{mode:"none"},                                          # §2 — isolate the payload write
pace:{jitter_ms:2, offset_ms:($i * 33.3667 / $lanes)},       # §3 — spread the phase
```

**Before any run:** make sure nothing else is listening on port 9600. cpp-httplib sets
`SO_REUSEPORT` (`httplib.h:1860`), so a stale server does **not** fail to bind — the kernel
round-robins connections between listeners and requests land nondeterministically, with no
error anywhere. `sweep.sh` guards against this; ad-hoc runs should `pkill -f build/abi-tester`
first.
