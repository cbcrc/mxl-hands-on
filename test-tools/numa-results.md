# Cross-NUMA cost of MXL grain transit (2 x Xeon Silver 4510)

This report asks what it costs when an MXL flow's writer and its readers run on different sockets of a 2-socket
Sapphire Rapids host. It was measured on 2026-10-19 and 2026-10-20 on two identical lab hosts, following
[numa-guide.md](numa-guide.md), with the ABI-tester.

It has four parts:
- a [background](#background) section that explains the terms the rest of the report uses;
- a [summary](#summary) of the findings;
- the [test procedure](#test-procedure);
- the [detailed report](#detailed-report), in the order the tests were run, with every number.

## Background

Readers who know CPU caches and NUMA can skip to the [summary](#summary).

### The machine and the setup

Each lab host has two CPU sockets. Each socket is a separate chip with 12 cores, its own 30 MB L3 cache and its
own memory. Linux calls each socket a **NUMA node**: node 0 is socket 0 and node 1 is socket 1, and this report
uses the two words interchangeably. A core can read the other socket's memory, but the request and the data travel
over **UPI** (Ultra Path Interconnect), Intel's link between the two sockets, and that takes longer than reading
its own memory.

```
            socket 0 = node 0                                socket 1 = node 1
  +----------------------------------+          +----------------------------------+
  | 12 cores (even CPUs)             |          | 12 cores (odd CPUs)              |
  | 30 MB L3                         |   UPI    | 30 MB L3                         |
  | memory (holds the grains)        |<-------->| memory                           |
  |                                  |          |                                  |
  | writer, local reader             |          | cross reader                     |
  +----------------------------------+          +----------------------------------+
```

In every test except layout B (§2-3):
- the **writer** runs on node 0, and the grains are in node 0's memory, because the writer creates the flow;
- a **local reader** runs on node 0;
- a **cross reader** runs on node 1 and reads every grain across UPI.

### Lines and caches

The CPU does not move single bytes between memory and its caches. It moves **lines** of 64 bytes, and the caches,
snoops, memory directory and counters below all work per line. A 2160p v210 grain is 22 118 400 B, which is
345 600 lines. At 29.97 fps that is about 10.4 M lines per second, or **311 M lines per 30 s**, the counter window.
That number recurs in every counter table: it is one 2160p30 grain stream.

Each core has small private caches (L1, L2). The 12 cores of a socket share its L3. Reading a line from a cache
is much faster than reading it from memory (DRAM), and reading local memory is faster than reading the other
socket's memory over UPI.

### Snoops and the memory directory

Several caches can hold a copy of the same line. Before a core writes a line, every other copy must be dropped, or
another core would go on reading the old value. Within a socket, the L3 knows which cores hold which lines.
Between sockets, the socket whose memory holds the line (its **home**) asks the other socket with a **snoop**: "do
you have a copy of this line? If so, drop it, and send it to me if you changed it." The answer either carries data
or says "I have nothing". Each snoop is a round trip over UPI.

So as not to snoop on every access, Sapphire Rapids keeps a few bits per line in memory, the **memory directory**,
that record whether the other socket may hold a copy. Two states matter here:
- **I:** not cached on the other socket. No snoop is needed.
- **A:** may be cached on the other socket. The home must snoop it before giving the line to a writer.

When node 1 reads a node 0 line, its state goes from I to A. Node 1 later drops its copy without telling anyone, so
the state stays A until the next snoop. Changing the state is a write to memory.

### Writing a line: RFO and non-temporal stores

- **RFO** (read for ownership) is what a core sends before writing a line: it fetches the line and gets the only
  copy. If the line is in state A, the RFO waits for the snoop of node 1. The writer fills each grain with
  `memset`, which writes this way.
- **Non-temporal stores** write straight to memory, around the caches. They are meant for data that will not be
  read again soon. glibc's `memcpy` uses them for copies above 16 MiB, which covers any 2160p grain. Such a write
  does not read the line from memory first, so the home cannot see the directory bits and snoops the other socket
  anyway.

### What the tests measure

- **Writer fill:** the time the writer takes to `memset` a whole grain.
- **Reader touch:** the time a reader takes to read every line of a grain (*lines*) or to `memcpy` it (*copy*).
- **Transit:** from the writer's commit stamp to the reader's wake-up. It measures the notification, not the data.
- **Ring:** the flow keeps its last 5 grains, and the writer overwrites the oldest.
- **head+1 / head-3:** the reader reads the grain just committed, or the one three frames older. At 2160p, three
  newer grains (66 MB) are more than the 30 MB L3, so a head-3 read comes from memory.
- **p50 / p99:** the median, and the value 99 % of grains stay under.

### Units

- **Flow rates in Gb/s**, as media flows are usually quoted. One 2160p29.97 v210 flow is 663 MB/s, i.e.
  **5.3 Gb/s**. Active 10-bit 4:2:2 video is 4.97 Gb/s; v210 packs 6 pixels into 16 bytes instead of 15.
- **Memory bandwidth in GB/s** (bytes), as memory is specified and as the tools report it.
- **Counts per 30 s**, the counter window: 311 M lines per 30 s is one 2160p30 grain stream.
- **Latencies from the uncore counters are in CHA cycles**, the clock of the L3 logic. Its frequency was not
  recorded, so these numbers are only compared with each other.

## Summary

Most results are at 2160p. At 1080p a grain (5.5 MB) and even the whole ring (27.6 MB) fit in one socket's 30 MB
L3, so reads could come from cache and hide the cost of crossing sockets. A 2160p grain is 22 MB and its ring
110 MB. Unless noted, the writer and the grains are on node 0, and "cross" means a reader on node 1.

### Bottom line

On this hardware, a 2160p30 flow whose reader runs on the other socket costs about **0.4-0.5 ms per grain** on
the reader's copy, and about **0.5 ms on the writer's fill**. Each is about 1.5 % of the 33.4 ms frame.
- Wake-up latency does not change.
- No configuration tested came near a frame period: up to 6 readers on one flow, 4 flows, and about 95 GB/s of
  memory stress. The worst p99 was under 5 ms.
- The CPU frequency governor mattered more than NUMA placement.

### Key findings

1. **Set the governor first.** Under `schedutil`, reads were bimodal and up to 2x slower. That produced a false
   "node 1 is faster" result. `performance` removed it and halved wake-up latency. (§1-3)
2. **Wake-up across sockets is free.** [Transit](#what-the-tests-measure), from the writer's commit to the
   reader's wake-up, is 0.03-0.06 ms at p50 under `performance`, the same for local and cross readers, at 1080p
   and 2160p. (§3, §4, §6)
3. **A cross reader reading alone pays +0.44 ms (+30 %) on a 2160p copy.** About 230 M of its 328 M
   [lines](#lines-and-caches) per 30 s come from node 0's memory.
   - Earlier results that showed it as almost free (+4 %) came from several readers feeding each other
     cache-to-cache.
   - To measure the cross-node cost, test one reader at a time. (§4-6)
4. **A cross reader slows the writer by about 0.5 ms (+34 %).**
   - Lines that node 1 has read stay marked "maybe cached remotely" in the
     [memory directory](#snoops-and-the-memory-directory).
   - Each later write to such a line must first [snoop](#snoops-and-the-memory-directory) node 1, almost always
     for nothing, then rewrite the directory.
   - That is about 300 M snoops and about 520 M extra DRAM writes per 30 s for one 2160p flow.
   - Every consumer of the flow, local ones included, gets each grain later. (§6, §7)
5. **The cost is per flow, not per reader.** With 1 to 6 cross readers on one flow:
   - the grain crosses [UPI](#the-machine-and-the-setup) once (5.3 Gb/s at 2160p30);
   - the writer's penalty stays at 0.5 ms;
   - two cross readers were faster than one, because they feed each other. (§8)
6. **A fresh grain is slower to read.** Reading at commit (head+1) is 14-16 % slower than reading three grains
   later, because the lines are still in the writer's cache. (§4, §5)
7. **UPI data scales with flows, and the links are far from full.** Four cross flows move 21 Gb/s. At four
   flows, local readers lost their advantage: touch 2.26 vs 2.31 ms, with a worse tail. They share node 0's L3
   and memory controller with four writers. (§9)
8. **Under heavy memory load, cross readers did not degrade more than local ones.**
   - With about 95 GB/s on node 0, touch rose +0.31 ms local and +0.26 ms cross.
   - The writer's snoop hides behind the slower DRAM read.
   - With four flows under load, the writer was the bottleneck: fill p99 4.4-4.8 ms. (§9, §10)
9. **Large copies send one cross-socket snoop per line, even when everything is local.**
   - A `memcpy` above glibc's 16 MiB threshold, which covers any 2160p grain, uses
     [non-temporal stores](#writing-a-line-rfo-and-non-temporal-stores).
   - Those cannot consult the directory, so they broadcast a snoop to the other socket.
   - Memory-streaming neighbours do the same, at a much larger scale. (§7, §8, §10)

### Recommendations for MXL deployments

- **Pin the CPU governor to `performance`**, or the platform equivalent, on MXL hosts. It is the largest single
  effect measured here.
- **Keep a flow's writer and its consumers on one socket when it is convenient.** At 2160p that saves about
  0.5 ms on the writer and 0.4 ms per remote reader. That is real, but under 2 % of a frame.
- **When a flow must cross, put all its remote consumers on the same remote socket.** The cost is per flow that
  crosses, not per consumer.
- **With several heavy flows on one socket, moving readers to the other socket can improve their tails.**
- **To measure a cross-node cost:** read the full payload, with one reader at a time, and count offcore or
  uncore events.
  - Transit-only tests show nothing.
  - Several readers hide the cost.
  - `perf c2c` cannot see streaming copies.

### Open questions

- **The writer slows from 1.48 to 1.65 ms when two or more local readers read its flow**, with no cross reader
  involved (§8). A guess: each write must first drop the copies of the line held in several readers' caches.
- **Under heavy memory load, the writer's fill is still 0.35 ms slower when its reader is cross** (§10). Yet the
  wait per write request ([RFO](#writing-a-line-rfo-and-non-temporal-stores)), measured at the L3, is now almost
  the same local and cross: 279 vs 283 cycles, against 242 vs 303 without load. The snoop to node 1 hides behind
  the slow memory read. Something else makes up the 0.35 ms, and it was not found.
- **Under memory stress, socket 1 receives more snoops in cross runs than in local runs**, for example 654 vs
  313 M per 30 s with one flow (§9, §10). Where they come from is not known.
- **On .134, a writer on node 1 was slower for every reader** (§3). This was not followed up.
- **UPI headroom is estimated, not measured.** The link count and flit size for this SKU were not checked.

### Caveats

- One hardware type (2 x Xeon Silver 4510), on two hosts.
- Most configurations ran once, for 90 s or 5 min. Where runs were repeated, they agreed to within 0.05 ms,
  except one at 0.14 ms (§7).
- The ABI-tester's copy into a reused buffer stands in for a real consumer. No mxlsrc or GStreamer pipeline was
  measured.
- Counters were multiplexed over 20-30 s windows and scaled.

## Test procedure

### Hardware and software

| | |
|---|---|
| Hosts | `10.164.50.134` (§1-3, shared with other users) and `10.164.50.136` (§4-10, idle) |
| CPU | 2 x Intel Xeon Silver 4510 (Sapphire Rapids), 12C/24T and 30 MB L3 per socket |
| Topology | 2 NUMA nodes, distance 10 local / 21 remote. Node 0 = even CPUs, node 1 = odd CPUs, and the hyperthread sibling of CPU n is n+24 |
| OS | Ubuntu, kernel 6.8.0-136, Docker 29.5.3, perf 6.8.12 |
| Image | `ghcr.io/cbcrc/abi-tester:latest` = `hands-on-1cdb007`, with glibc 2.39 inside |
| MXL domain | `/dev/shm/domain_1` (tmpfs), bind-mounted into every container |

### What is measured

The ABI-tester runs scenarios made of lanes, one thread per lane.
- **The writer lane** writes v210 grains in real time at 29.97 fps. It fills each grain with `memset`, and stamps
  the index and `mxlGetTime()` into the first 24 bytes.
- **A reader lane** does a blocking `mxlFlowReaderGetGrain` at its cursor, checks the stamp, and then touches the
  payload in one of three ways:
  - **stamp:** reads only the stamp;
  - **lines:** reads every line;
  - **copy:** `memcpy` of the whole grain into a reused per-lane buffer.

The metrics:

| Metric | Meaning |
|---|---|
| `transit_ms` | The reader's wake-up time minus the writer's stamp. Every container shares one host clock, so this is a real latency. |
| `touch_ms` | The time to read or copy the grain. |
| writer fill | `fill_us` of `mxlFlowWriterOpenGrain`, the `memset` of the whole grain. |
| head+1 | The reader blocks for the next grain and reads it right after commit. |
| head-3 | The reader reads the grain three frames old. At 2160p, three newer grains (66 MB) have been written since, more than the 30 MB L3. |

Grain sizes:
- **1080p v210:** 5 529 600 B. The ring of 5 grains is 27.6 MB and fits in one L3.
- **2160p:** 22 118 400 B. The ring is 110.6 MB.

The scenarios are 08 (lines), 10 (stamp) and 11 (copy) from `ABI-tester/scenarios`. Scenarios 12-17 (1080p
head-3, 2160p, and 2160p head-3) were derived from those and exist only on .136, untracked (§4).

### Placement

- **Compose.** `docker-compose.numa-writer-reader.yml`, plus the override `~/numa-runs/three-readers.yml` on
  each host from §2 on. The override sets every cpuset from the environment and adds `abi-reader-local`, a
  reader-only container on the writer's node (port 9609).
- **Memory nodes.** After `up`, `docker update --cpuset-mems` sets each container's memory node.
  `Cpus_allowed_list` and `Mems_allowed_list` were checked in `/proc/<pid>/status` before every run.
- **Grain pages.** The writer creates the flow, so the grain pages are first touched on node 0. `numa_maps` in
  every container showed the grain pages as `N0=` only.
- **Layouts.** Each container is pinned to whole physical cores. CPU n and its hyperthread sibling n+24 are one
  core, so "0-10 even + 24-34 even" means CPUs 0, 2, 4, 6, 8, 10 and 24, 26, 28, 30, 32, 34: 6 cores on node 0.
  No two containers share a core. Each container's memory node is the node it runs on, so in layout B the grains
  are in node 1's memory.

| Layout | Role | Node | Cores | CPUs |
|---|---|---|---|---|
| **First run** (§1) | writer, whose own reader lane is the local reader | 0 | 12 | all even CPUs |
| | cross reader | 1 | 12 | all odd CPUs |
| **A** (§2-8): the standard layout | writer | 0 | 6 | 0-10 even + 24-34 even |
| | local reader | 0 | 6 | 12-22 even + 36-46 even |
| | cross reader | 1 | 6 | 1-11 odd + 25-35 odd |
| **B** (§2-3): A with the nodes swapped, to test whether one socket is faster | writer | 1 | 6 | 1-11 odd + 25-35 odd |
| | local reader | 1 | 6 | 13-23 odd + 37-47 odd |
| | cross reader | 0 | 6 | 0-10 even + 24-34 even |
| **Flows** (§9-10): node 0 split three ways to make room for stress-ng | writer | 0 | 4 | 0-6 even + 24-30 even |
| | local reader | 0 | 4 | 8-14 even + 32-38 even |
| | stress-ng | 0 | 4 | 16-22 even + 40-46 even |
| | cross reader | 1 | 6 | 1-11 odd + 25-35 odd |

In §1-5 the writer container also runs reader lanes of its own, reported as "the writer's own reader". From §6 on
it runs the writer lanes only.

### Host settings

- **CPU governor:** `performance` on all 48 CPUs from §3 on, set by the user with `sudo cpupower frequency-set -g
  performance`. §1-2 ran under the default `schedutil`, which turned out to be the main source of noise.
- **Isolation:** no `isolcpus`. Nothing else was running on .136.

### Run harness (§5-10)

Each pass has two scripts in `~/numa-runs/` on the host:
- `<pass>-run.sh`, the driver, runs as the `lab` user;
- `<pass>-record.sh`, the recorder, runs with sudo in the user's own terminal.

They hand off through files:
1. The recorder creates `ready`. From §7 on, it first does a 1 s test count to catch rejected events.
2. The driver brings the containers up.
3. For each run, the driver creates `go` 30 s in. The recorder counts for 30 s (20 s in §5) and creates
   `recorded`.
4. After the last run, the driver creates `finished` and the recorder exits.

Each run:
1. The driver stops every container, clears the domain, and starts only the containers in the run. The other
   reader stays stopped.
2. It fetches the scenario from the server and trims it with jq to 90 s (`a7.times = 2699`, `b5.times = 2549`).
3. It splits it into a writer body (lane A) and a reader body (lane B), then does `POST /scenario` and `POST /run`.
4. It waits until every lane's `next` reaches the end of its steps. `/state.running` stays true after the lanes
   finish, so it can't be used.

Variants of this:
- **N readers or F flows (§8-10):** lane B, or lane A, is cloned onto more lanes. Each clone gets its own handle
  names, because handles are global to a container. Each writer lane gets its own flow id: the scenario's id with
  the last two digits replaced by 10+i. The bodies were checked against a scratch server before the first run.
- **stress-ng (§9-10):** `polinux/stress-ng` runs on its own physical cores with `--cpuset-mems 0`. It is
  started 15 s before the flows and removed after.

### Counters

Every counter needs root, because `perf_event_paranoid` is 4.

The tools:
- **`perf stat`** reads the CPU's hardware counters and prints one total per event over a time window, like an
  odometer. It does not slow the program down or record individual accesses.
  - **`-C <cpus>`** counts only what happens on those CPUs. In §5-6 that is one container's CPUs, so each number
    belongs to one container.
  - **`-a --per-socket`** counts on every CPU and prints one total per socket (S0, S1). §7-10 need it because
    their events are not tied to a core but to hardware shared by the whole socket: the L3 logic (CHA), the
    memory directory logic (M2M), the UPI links and the memory controller (IMC). Those totals cannot be split by
    container. The writer is on S0 and the cross reader on S1, so the split by socket mostly matches the split by
    role.
- **`perf c2c`** ("cache to cache") samples individual loads and stores and reports which lines move between
  cores or sockets. It is the usual tool for false sharing. It saw nothing here; see below.
- **Offcore events** (`ocr.*`, §5-6) count, per core, every line brought in from outside the core, by where it
  came from: the local L3, another core's cache, local memory, the other socket's memory or the other socket's
  cache.

| Section | Tool | Events |
|---|---|---|
| §5 | `perf c2c`, then `perf stat` per container's CPUs | `ocr.reads_to_core.*`: every line read into a core, demand + prefetch + RFO, by source |
| §6 | `perf stat` per container's CPUs | the same, adding `any_response` and `l3_hit` so the sources add up |
| §7-10 | `perf stat -a --per-socket` | CHA: `dir_lookup.*`, `snoop_resp.*`, `tor_inserts`/`tor_occupancy.ia_miss_rfo(_pref)`, `tor_inserts.snps_from_rem`, `requests.invitoe`. M2M: `directory_lookup.*`, `directory_update.*`. UPI: `txl_flits.all_data`/`non_data`. IMC: `cas_count.rd`/`wr` |

Things learned about the counters:
- **`perf c2c` sees nothing in a streaming `memcpy`.** The prefetchers fetch the lines before the loads run, so
  the loads count as L1 hits.
- **`unc_cha_snoops_sent.remote` reads 0** even when hundreds of millions of snoops cross sockets. Use
  `tor_inserts.snps_from_rem` on the receiving socket instead.
- **The writer's ownership requests are mostly `ia_miss_rfo_pref`** (L2 prefetch RFOs), not `ia_miss_rfo`.
- **RFO miss latency** is `tor_occupancy / tor_inserts`, in CHA cycles.
- **Events are multiplexed and scaled.** There are more events than hardware counters, so perf rotates them and
  scales each total up. Each event is live for 20-60 % of the window.

The counter vocabulary used in §5-10:

| Term | Meaning |
|---|---|
| CHA | Caching and Home Agent. There is one per L3 slice, about one per core. It tracks which cores hold each line and acts as the **home** for memory lines: it checks the directory, sends snoops and sends requests to memory. |
| TOR | Table of Requests: the CHA's queue of requests waiting for an answer. `tor_inserts` counts requests entering it and `tor_occupancy` adds up how many are waiting at each cycle, so occupancy / inserts is the average wait per request, in CHA cycles. |
| `ia_miss_rfo`, `ia_miss_rfo_pref` | RFOs from a core (IA) that missed the L3. `_pref` are the ones the L2 prefetcher sends ahead of the writes. |
| `requests.invitoe` | `InvItoE`: a request for the only copy of a line without reading its data, sent by non-temporal stores that overwrite the whole line. |
| `snoop_resp.rspi`, `.rspifwd` | Snoop answers: RspI is "I have nothing", RspIFwd is "I had it, here is the data". |
| `tor_inserts.snps_from_rem` | Snoops arriving at this socket from the other one. |
| M2M | Mesh to Memory: the bridge between the CHAs and the memory controller. It reads and writes the directory bits stored with each line. `directory_lookup.state_i` / `state_a` count the state found, `directory_update.i2a` / `a2i` the state changed. |
| IMC, `cas_count.rd` / `.wr` | The integrated memory controller. A CAS is one 64-byte read or write to memory, so CAS x 64 B / 30 s gives GB/s. |
| UPI flits | The unit of transfer on UPI. `txl_flits.all_data` counts the flits carrying line data that a socket sends, `non_data` the rest: requests, snoops and answers. Here, about 9 data flits carry one 64-byte line. |
| `ocr.reads_to_core.*` | Offcore: lines brought into a core, demand reads, prefetches and RFOs alike, by source. |

### Analysis

- **§1-6:** `ABI-tester/mxl-transit-stats.py` gives the transit, touch and end-to-end percentiles.
- **§7-10:** p50 and p99 are computed from the NDJSON, dropping the first 100 grains of each lane. The first
  fills take about 7.4 ms because of page faults. With several lanes, p50 is the mean of the lanes' p50, and p99
  is that of the worst lane.

## Detailed report

The sections are in the order the tests were run. Each says why it was run, and later sections correct earlier
ones where noted.

| § | Host, date | Question | Answer |
|---|---|---|---|
| 1 | .134, 10-19 | Does a cross reader pay to read a 1080p grain? | Apparently not, and node 1 even looked faster |
| 2 | .134, 10-19 | Same process, or a socket difference? | Neither: the readers sat in two states 2x apart |
| 3 | .134, 10-19 | Is it the CPU frequency governor? | Yes. Under `performance` every reader agrees |
| 4 | .136, 10-19 | Does 2160p from DRAM cost anything? | Looked like +4 %; corrected in §5-6 |
| 5 | .136, 10-19 | Where does each line come from? | Readers feed each other; the writer's `memset` is not non-temporal |
| 6 | .136, 10-19 | One reader at a time? | Cross copy +0.44 ms (+30 %), writer fill +0.5 ms |
| 7 | .136, 10-20 | Why does a cross reader slow the writer? | Memory-directory snoops of node 1 |
| 8 | .136, 10-20 | Does it grow with readers? | No: per flow, not per reader |
| 9 | .136, 10-20 | Several flows, light memory stress? | UPI linear; local readers lose their advantage at 4 flows |
| 10 | .136, 10-20 | Heavy memory stress? | Cross does not degrade more than local |

### 1. First run: 1080p, stamp, lines, copy (.134, 2026-10-19, 17:08 to 18:10 UTC)

This follows Test 2 of [numa-guide.md](numa-guide.md). One writer runs on NUMA node 0 and a separate reader on
node 1, sharing a single MXL domain on tmpfs. Each read flavour ran once for 5 minutes.

#### Setup

| | |
|---|---|
| Host | 2 x Intel Xeon Silver 4510 (12C/24T per socket, 30 MB L3 per socket), Ubuntu kernel 6.8.0-136, Docker 29.5.3 |
| Topology | 2 NUMA nodes, distance 10 local / 21 remote; node 0 = even CPUs, node 1 = odd CPUs |
| Image | `ghcr.io/cbcrc/abi-tester:latest` = `hands-on-1cdb007` (`sha256:7baa3570...`) |
| Compose | `docker-compose.numa-writer-reader.yml`, unchanged, `MXL_DOMAIN_DEVICE=/dev/shm/domain_1` |
| abi-writer | cpuset = the 24 node 0 CPUs, `--cpuset-mems 0`; verified `Mems_allowed_list: 0` before every run |
| abi-reader | cpuset = the 24 node 1 CPUs, `--cpuset-mems 1`; verified `Mems_allowed_list: 1` before every run |
| Host isolation | none: no `isolcpus`, governor `schedutil`, idle host (load 0.00) |
| Flow | 1080p29.97 v210, 5 529 600 B grains, 9 000 grains written (5 min) |

**Scenarios.** Each run uses scenario 10 (stamp), 08 (lines) or 11 (copy) from `ABI-tester/scenarios`. Both
instances load it with jq overrides: `a7.times = 8999`, `b5.times = 8849`. Lane B stops 150 grains before the
writer, so its last blocking reads don't wait out their 2 s timeouts.
- **Writer instance** runs the writer lane A and the reader lane B. Its lane B is the **local** reader (node 0).
- **Reader instance** runs the same body with lane A removed. Its lane B is the **cross-node** reader (node 1).

The scenarios also have a lane C, a once-per-second non-blocking read of the newest grain. It is not reported: it
measures where in the frame the probe happens to fall (about half a frame at p50), not latency.

The domain was cleared and the containers restarted before each run. The writer creates the flow, so it touches
the pages first. While the copy run's flow was still mapped, `/proc/1/numa_maps` in **both** containers showed
every grain page as `N0=` only. The node 1 reader really does read node 0 memory.

Both containers share one host clock, so `transit_ms` is a real latency, not a clock offset. No negative
transits were seen.

#### Results: lane B (blocking `mxlFlowReaderGetGrain`), all values in ms

8 850 reads per reader per run: 0 failed, 0 indices missed, all stamps valid and matching.

| Flavour | Reader | transit p50 | transit p99 | touch p50 | touch p99 | end-to-end p50 | end-to-end p99 |
|---|---|---:|---:|---:|---:|---:|---:|
| stamp | node 0 (local) | 0.073 | 0.089 | — | — | — | — |
| stamp | node 1 (cross) | 0.074 | 0.092 | — | — | — | — |
| lines | node 0 (local) | 0.074 | 0.093 | 0.842 | 1.085 | 0.918 | 1.167 |
| lines | node 1 (cross) | 0.074 | 0.094 | 0.634 | 1.016 | 0.706 | 1.095 |
| copy | node 0 (local) | 0.073 | 0.091 | 1.453 | 1.609 | 1.526 | 1.692 |
| copy | node 1 (cross) | 0.071 | 0.091 | 0.691 | 1.555 | 0.762 | 1.635 |

How to read the columns:
- `transit` = reader wake-up time minus the writer's `mxlGetTime()` stamp.
- `touch` = time to read the whole grain (lines) or memcpy it (copy).
- `end-to-end` = transit + touch.

#### Observations

1. **Wake-up latency does not depend on the node.** For all three flavours, transit p50 is 0.071-0.074 ms and
   p99 is 0.089-0.094 ms on both readers. The futex wake-up across sockets costs nothing measurable.
2. **The cross-node reader was not slower at reading the payload, and the local reader was the slower one.**
   This is the opposite of what the guide expects:
   - **lines:** touch p50 0.634 (node 1) vs 0.842 ms (node 0).
   - **copy:** 0.691 vs 1.453 ms.
   - **p99 is close for both:** lines 1.016 vs 1.085 ms, copy 1.555 vs 1.609 ms.
   - **The copy distributions are bimodal.** The node 0 reader sits almost always in a mode near 1.45 ms. The
     node 1 reader is mostly near 0.69 ms but reaches the same 1.45 ms mode above p90.
3. **Possible reasons why the cross reader looked faster, none of them verified at the time.** Later sections
   tested each one; the outcome follows each reason.
   - **The ring fits in L3.** The flow ring is 5 grains (`data.0`-`data.4`), about 27.6 MB, which fits in one
     socket's 30 MB L3. A freshly committed grain is then likely still in node 0's L3. In that case the node 1
     reader's read is served largely cache-to-cache over UPI, not from remote DRAM, and this test does not
     measure a remote-DRAM read.\
     → **§4-6: true.** At 1080p the cross reader was reading node 0's cache. At 2160p, with one reader reading
     from memory, cross costs +0.44 ms (+30 %).
   - **The local reader shares the writer's process and CPU set.** It also shares its L2 and L3 with the
     writer's working set.\
     → **§2: ruled out.** A separate local reader matches the writer's own reader to within 1 µs.
   - **Frequency scaling.** The governor is `schedutil` and the host was otherwise idle, so the core frequency
     a reader wakes on can vary between the two sockets.\
     → **§3: the cause.** Under `performance` every reader agrees, and the "faster" cross reader is gone.
4. **These are single 5-minute runs.** No pcm/UPI counters were collected, because pcm needs sudo on the host.\
   → §5-10 add `perf` counters, recorded with sudo by the user.

#### Follow-ups worth trying

- **Separate local reader.** Pin a third reader container to node 0 and run the reader-only body. That separates
  "local" from "same process as the writer".\
  → **§2: done.** Same process is ruled out.
- **Rerun with the `performance` governor**, or with `isolcpus` on the readers' CPUs, to remove frequency and
  scheduler noise.\
  → **§3: done** with `performance`, which removed the noise. `isolcpus` was not tried.
- **Repeat 3 times.** The guide asks for at least 3 runs.\
  → **Not done** systematically. The runs repeated in §7 matched §6 to within 0.05 ms, except one at 0.14 ms.
- **Run under node 0 memory stress**, as in the guide's stress-ng section, to see whether the cross-node reader
  then degrades more.\
  → **§9-10: done.** Cross did not degrade more than local.

### 2. Three readers, then swapped nodes (.134, 2026-10-19, 18:28 to 18:50 UTC)

This tests two possible explanations for the first run's result:
- **Same process.** The local reader shares the writer's process and its cores.
- **Socket asymmetry.** Node 1 is simply the faster socket.

**Override.** The PR compose was used unchanged, plus the override `~/numa-runs/three-readers.yml` on the lab
host. The override sets every cpuset from the environment and adds **abi-reader-local**: a separate container on
the writer's node (port 9609) that runs the same reader-only body.

**Layouts.** Each container gets its own 6 physical cores, including their hyperthread siblings (n and n+24).
No two containers share a core.

| Layout | abi-writer (+ its own reader) | abi-reader-local | abi-reader (cross) |
|---|---|---|---|
| A | node 0: 0-10 even, 24-34 even, mems 0 | node 0: 12-22 even, 36-46 even, mems 0 | node 1: 1-11 odd, 25-35 odd, mems 1 |
| B | node 1: 1-11 odd, 25-35 odd, mems 1 | node 1: 13-23 odd, 37-47 odd, mems 1 | node 0: 0-10 even, 24-34 even, mems 0 |

Each run is 5 minutes, with the same scenario overrides as above. All 12 reader results (3 readers in parallel x 4 runs) had 8 850 reads with 0 failed,
0 missed and every stamp valid. `Cpus_allowed_list` and `Mems_allowed_list` were verified before every run.

#### Lane B touch_ms, p50 / p99 (ms)

| Flavour | Layout | Writer's own reader | Separate local reader | Cross-node reader |
|---|---|---|---|---|
| copy | A (writer node 0) | 1.569 / 1.707 | 1.568 / 1.708 | 1.540 / 1.623 |
| copy | B (writer node 1) | 1.603 / 1.752 | 1.602 / 1.754 | 1.535 / 1.616 |
| lines | A (writer node 0) | 0.927 / 1.244 | 0.791 / 1.246 | 0.870 / 1.065 |
| lines | B (writer node 1) | 0.791 / 1.237 | 0.502 / 1.239 | 0.788 / 0.985 |

Transit was 0.070-0.080 ms at p50 and 0.089-0.106 ms at p99 for every reader in every run. End-to-end follows
touch plus about 0.08 ms. Both are in the raw output below.

#### What it shows

1. **Copy is the same for all readers, in both layouts.** All six copy readers sit at 1.54-1.60 ms p50. The
   cross-node reader is 2-4 % *faster* in both layouts. Since that follows the role and not the node, it is not
   a socket difference. Reading the payload across NUMA has no measurable cost here.
2. **Same-process is ruled out.** The writer's own reader and the separate local reader match to within 1 µs
   on copy, in both layouts.
3. **Socket asymmetry is ruled out.** Swapping the nodes does not move the numbers, so the hardware treats both
   sockets the same.
4. **The first run's "fast" copy mode (0.69 ms) did not come back** for any of the six copy readers. It does
   reappear on lines, and on a reader of either kind:
   - layout B's separate local reader reached 0.502 ms p50, with p10 0.488 and p90 0.797;
   - the writer's own reader in layout B had a p10 of 0.580.

   So all readers share a fast state (about 0.5 ms lines, 0.69 ms copy) and a slow state (about 0.8-0.9 ms
   lines, about 1.5 ms copy), roughly 2x apart. Which state a reader gets does not depend on its node, its role
   or its process.
5. **Leading hypothesis now: CPU frequency.** The cores run the `schedutil` governor, the host is idle, and the
   readers sleep 33 ms between grains. Even the fast state reads at only about 11 GB/s for lines and 8 GB/s for
   copy, which is low for a 4.1 GHz core. Two other explanations are still possible:
   - the core a thread happens to wake on;
   - whether the grain is still in the writer socket's L3 (the ring of 5 grains is about 27.6 MB, the L3 30 MB).

   → **§3: frequency confirmed.** Under `performance` the two states collapse into one, whatever core a reader
   wakes on. The L3 question is answered in §4-6.

#### Next steps to separate those

- **Governor run (needs sudo).** Rerun copy and lines with the `performance` governor:
  `sudo cpupower frequency-set -g performance`, then `-g schedutil` afterwards. If the 2x states collapse into
  the fast one, frequency is the cause.\
  → **§3: done.** Frequency is the cause.
- **Without sudo.** During a run, sample each reader thread's last CPU (`/proc/1/task/*/stat`, field 39, via
  `docker exec`) and that CPU's `scaling_cur_freq`. Then line them up with the touch_ms time series.\
  → **Not done.** §3 made it unnecessary.
- **2160p run.** Run with 2160p grains (a ring of about 110 MB, larger than L3) to see a real remote-DRAM read
  cost.\
  → **§4: done**, and corrected by §5-6: one reader alone pays +0.44 ms (+30 %).

### 3. `performance` governor (.134, 2026-10-19, 18:57 to 19:19 UTC)

Same three-reader layouts A and B, copy and lines, 5 minutes each. The only change is the CPU frequency
governor: the user set all 48 CPUs from `schedutil` to `performance`, and that was verified before and after.
All 12 reader results (3 readers in parallel x 4 runs) had 8 850 reads with 0 failed.

#### Lane B touch_ms, p50 / p99 (ms), `schedutil` -> `performance`

| Flavour | Layout | Writer's own reader | Separate local reader | Cross-node reader |
|---|---|---|---|---|
| copy | A (writer node 0) | 1.569 / 1.707 -> **0.681 / 0.802** | 1.568 / 1.708 -> **0.682 / 0.803** | 1.540 / 1.623 -> **0.680 / 0.800** |
| copy | B (writer node 1) | 1.603 / 1.752 -> **0.805 / 0.955** | 1.602 / 1.754 -> **0.806 / 0.955** | 1.535 / 1.616 -> **0.793 / 0.952** |
| lines | A (writer node 0) | 0.927 / 1.244 -> **0.516 / 0.668** | 0.791 / 1.246 -> **0.517 / 0.669** | 0.870 / 1.065 -> **0.519 / 0.669** |
| lines | B (writer node 1) | 0.791 / 1.237 -> **0.561 / 0.688** | 0.502 / 1.239 -> **0.563 / 0.687** | 0.788 / 0.985 -> **0.557 / 0.684** |

#### Lane B transit_ms, p50 / p99 (ms), `performance`

| Flavour | Layout | Writer's own reader | Separate local reader | Cross-node reader |
|---|---|---|---|---|
| copy | A | 0.033 / 0.042 | 0.032 / 0.041 | 0.034 / 0.045 |
| copy | B | 0.055 / 0.149 | 0.056 / 0.144 | 0.056 / 0.143 |
| lines | A | 0.034 / 0.111 | 0.033 / 0.114 | 0.035 / 0.117 |
| lines | B | 0.053 / 0.147 | 0.052 / 0.145 | 0.056 / 0.151 |

Under `schedutil`, every reader's transit had been 0.070-0.080 ms at p50 and 0.089-0.106 ms at p99.

#### What it shows

1. **The CPU frequency governor was the cause.**
   - Under `performance`, copy drops from about 1.55 ms to 0.68-0.81 ms, and lines from about 0.8-0.9 ms to
     0.52-0.56 ms.
   - The two states 2x apart disappear. Every reader now lands where the "fast state" was, and p99 tightens
     with it.
   - Wake-up latency (transit) roughly halves as well.
   - Under `schedutil`, a reader that sleeps 33 ms between grains woke on a core that had clocked down, and
     whether it got a fast or slow core was luck. That is where the first run's "node 1 is faster" came from.
2. **Reading across NUMA still has no measurable cost.** With frequency noise removed, the three readers in each
   run agree to within 1.5 % at p50 and within 0.005 ms at p99, on both flavours and in both layouts. For a 1080p
   grain that was just written, the cross-node read costs the same as the local one. The ring of 5 grains
   (about 27.6 MB) most likely still sits in the writer socket's L3, so this measures cache-to-cache over UPI,
   not remote DRAM.\
   → **§4-6: confirmed.** The cost appears at 2160p, with one reader reading from memory.
3. **Layout B is slower for all three readers, local and cross alike.** That is the run with the writer on
   node 1. Copy is 0.80 vs 0.68 ms, and transit p50 is 0.055 vs 0.033 ms with a fatter p99. Because it hits
   every reader, it follows where the **writer** runs, not where a reader reads from. It is one run each, so
   treat it as a lead and not a result. Plausible causes on node 1:
   - other activity on that node (the host also runs `intel-mtl-manager` and mediamtx);
   - different uncore or C-state behaviour on socket 1.

   → **Not followed up.** It is listed in the summary's open questions.

#### Recommendation

For latency tests on this host, set the governor to `performance` and report it with the results. Under
`schedutil`, run-to-run variation (about 2x on touch) is far larger than the effect being measured. The
remaining open questions:
- why the writer on node 1 is slower; a repeat run, plus a look at what else runs on node 1, would tell.\
  → **Not followed up.**
- the remote-DRAM cost, which needs a 2160p run whose ring is larger than L3.\
  → **§4-6.**

### 4. 2160p, and reading a grain from DRAM (head-3) (.136, 2026-10-19, 19:50 to 20:19 UTC)

**Why.** At 1080p, every result above may have been read from cache. A 5.5 MB grain fits in one socket's 30 MB
L3, and so does the whole ring of 5 grains (27.6 MB). That holds even for a reader that lags behind the head.

At 2160p a grain is 22 118 400 B. A reader at **head-3** reads a grain that three newer grains (66 MB) have been
written over. That is more than twice the L3, so the read has to come from DRAM.

**Host.** `lab@10.164.50.136` is the same hardware as .134: 2 x Xeon Silver 4510, the same NUMA layout, kernel
6.8.0-136, Docker 29.5.3. Its governor was set to `performance` by the user. The repo is at 7cbdbc8 and the image
is the same build (`hands-on-1cdb007`). Nothing else was running on it. Same three-reader **layout A** as above
(writer and separate local reader on node 0, cross reader on node 1, 6 cores each). Each run is 5 minutes, with
8 850 measured reads per reader and 0 failed.

**Scenarios.** These exist only on .136, untracked:

| File | Built from | Change |
|---|---|---|
| `14-transit-local-lines-2160p` | 08 | flow_def 3840x2160 (Y 3840, Cb/Cr 1920 wide), label and description |
| `15-transit-local-copy-2160p` | 11 | same |
| `16-transit-local-lines-2160p-lag3` | 14 | lane B rewritten for head-3, below |
| `17-transit-local-copy-2160p-lag3` | 15 | same |
| `12-transit-local-lines-lag3`, `13-transit-local-copy-lag3` | 08, 11 | head-3 at 1080p |

In the head-3 variants, lane B does two steps per grain:
- **`b4` (waiter).** The original blocking read at the cursor, with no stamp check and no touch. It waits for
  each new grain, which keeps the lag fixed.
- **`b4l` (measured read).** It reads `{"mode": "cursor", "offset": -4}`. Since b4 has already advanced the
  cursor to N+1, that is N-3, i.e. head-3. It has `verify_stamp` and `touch_payload`.

The stats script therefore shows 17 700 calls, 8 850 of them "without a valid stamp" (the waiters). The transit
and touch figures come only from b4l. `transit_ms` of about 100.15 ms is three frame periods, by construction.

**Placement.** `numa_maps` of all three containers, after every run, shows **5 grains** of 16 + 5 400 pages each
(about 110.6 MB) plus the flow header. That is 27 092 pages in total, **all `N0=`**.

#### touch_ms, p50 / p99 (ms), layout A, `performance`

| Grain | Read | Writer's own reader (node 0) | Separate local reader (node 0) | Cross-node reader (node 1) |
|---|---|---|---|---|
| 2160p copy | head+1 (just committed) | 1.848 / 1.892 | 1.848 / 1.894 | 1.846 / 1.892 |
| 2160p copy | **head-3 (DRAM)** | 1.622 / 1.652 | 1.626 / 1.655 | **1.690 / 1.705** |
| 2160p lines | head+1 (just committed) | 1.492 / 1.942 | 1.493 / 1.942 | 1.490 / 1.941 |
| 2160p lines | **head-3 (DRAM)** | 1.288 / 1.447 | 1.291 / 1.450 | 1.291 / 1.452 |
| 1080p copy | head-3 | 0.481 / 0.719 | 0.484 / 0.720 | 0.531 / 0.584 |
| 1080p copy | head+1, **on .134** (from the governor section) | 0.681 / 0.802 | 0.682 / 0.803 | 0.680 / 0.800 |

2160p head+1 transit: 0.033-0.036 ms at p50 and 0.042-0.046 ms at p99, the same as at 1080p under
`performance`. Wake-up does not depend on grain size. The 1080p head+1 runs on .136 were stopped in favour of
2160p, so the last row comes from .134 (same hardware, same layout) and is a cross-host comparison.

#### What it shows

> **Correction (offcore counters, §5).** Point 1 and point 4 assumed the cross-node reader read
> head-3 grains from DRAM. It mostly did not. All three readers read the same grain at the same moment, so each
> line came from DRAM once, into whichever node 0 reader asked first. The other readers, the cross-node one
> included, were mostly served from that reader's cache. Point 2 (fresh grains are pulled out of the writer's
> cache) is confirmed. With a single reader (§6), a cross-node 2160p read costs **+0.44 ms (+30 %)**,
> not +4 %, and it also slows the writer's fill by about 0.5 ms.

1. **Even from DRAM, reading across NUMA costs little.** At head-3 at 2160p, the cross-node copy is
   **+0.065 ms (+4 %) at p50 and +0.05 ms at p99** over the two local readers. Cross-node lines is the same as
   local.

   A single thread reading one stream cannot saturate either the local memory controller or UPI:
   - lines reads 22 MB in 1.29 ms, about 17 GB/s;
   - copy moves it in 1.62 ms, about 13.6 GB/s.

   The core's own limit on outstanding requests, helped by the prefetchers, hides the extra remote latency. With
   one 2160p30 consumer per socket, the interconnect is not the bottleneck.
2. **A grain read just after commit is *slower* than one read three frames later,** on every reader, local or
   remote. Copy is 1.85 vs 1.62 ms (+14 %) and lines 1.49 vs 1.29 ms (+16 %), with the p99 of lines 1.94 vs
   1.45 ms. The most likely reason is that a just-written grain is still settling. Either its lines are still
   modified in the writer's L2/L3 and every reader pays for the coherence traffic, or the writer's large
   `memset` (non-temporal stores above a size threshold) is still draining to DRAM. Without `perf c2c` there
   is no way to tell which.
3. **At 1080p the picture is the same, but noisier.**
   - At head-3, the cross reader's p50 is 0.05 ms higher, but its p99 is lower than the local readers', because
     the local readers still show a second mode, near 0.70 ms, at p90.
   - 1080p head-3 (0.48 ms on .136) is also faster than head+1 (0.68 ms on .134), which fits point 2. That
     comparison is across hosts, though.
4. **For MXL deployments.** For a single reader per flow, placing writer and reader on different sockets is not
   a measurable latency problem on this hardware: under 0.07 ms on a 33 ms frame, even for 2160p from DRAM. The
   governor (§3) matters about 10x more. What this test does **not** cover is many concurrent
   cross-node readers, or a memory-bandwidth-loaded socket. Those are the guide's stress-ng and `pcm` runs, and
   they need sudo.

### 5. Where each line comes from: offcore counters (.136, 2026-10-19, 21:02 to 21:10 UTC)

`perf c2c`, recorded first (`~/numa-runs/c2c/`), could not tell head+1 from head-3:
- The HITM counts were nearly identical: 760 vs 773 local, 180 vs 185 remote.
- All of the busy shared cache lines were in the kernel (scheduler tick, timekeeping, spinlocks).
- 82 % of samples came from idle CPUs.

c2c samples load instructions. In a streaming `memcpy`, the hardware prefetchers fetch almost every line before
the load runs, so the loads show up as L1 hits and the memory traffic is never attributed to them.

Instead, `perf stat` counted the `ocr.reads_to_core.*` offcore-response events. These cover every line read
into a core, **demand and prefetch**, plus reads for ownership (RFOs), split by where the line was served from.
They were counted per container's 12 CPUs for 20 s, 30 s into a 90 s run, in the same three-reader layout A,
under `performance`.

**Caveats.**
- The core can count two offcore events at a time, so the five rotated (about 40 % each) and were scaled.
- Only five sources were counted. In particular, `remote_cache.snoop_fwd` and plain L3 hits were not.

**Expected volume per reader in 20 s.**
- 2160p: 345 600 lines per grain x 600 grains = **207 M lines**.
- 1080p: 86 400 x 600 = **51.8 M lines**.

**Run timings.** The counted runs reproduced the earlier touch times:

| Run | touch p50 / p99 (ms) |
|---|---|
| 2160p head+1 | 1.84 / 1.90 |
| 2160p head-3 | 1.61 / 1.64 |
| 1080p head+1 | 0.70 / 0.75 |
| 1080p head-3 | 0.48-0.52 / 0.60-0.71 |

This also fills the 1080p head+1 gap on .136: 0.70 ms there vs 0.68 ms on .134.

#### Lines read into each container's cores in 20 s (millions)

"Writer" means the writer container's CPUs. They also run the writer's own reader (lane B), so that column is
**the writer's fill plus one reader**.

| Run | Container | modified line, same socket (`l3_hit.snoop_hitm`) | modified line, other socket (`remote_cache.snoop_hitm`) | clean line forwarded, same socket (`l3_hit.snoop_hit_with_fwd`) | local DRAM | remote DRAM |
|---|---|---:|---:|---:|---:|---:|
| 2160p head+1 | writer (+ own reader) | 9.5 | 0.3 | 98.6 | 296.5 | 0.8 |
| | reader-local (node 0) | 8.2 | 0.1 | 87.6 | 97.4 | 0.2 |
| | reader (node 1) | 0.3 | **23.8** | 0.5 | 1.1 | 4.4 |
| 2160p head-3 | writer (+ own reader) | 2.1 | 0.2 | 106.6 | 308.6 | 0.7 |
| | reader-local (node 0) | 1.7 | 0.1 | 96.8 | 108.6 | 0.2 |
| | reader (node 1) | 0.3 | **2.7** | 0.6 | 1.0 | **0.8** |
| 1080p head+1 | writer (+ own reader) | 15.3 | 0.3 | 12.8 | 61.5 | 0.8 |
| | reader-local (node 0) | 13.8 | 0.1 | 12.8 | 8.2 | 0.1 |
| | reader (node 1) | 0.3 | **35.8** | 0.4 | 0.3 | 2.2 |
| 1080p head-3 | writer (+ own reader) | 7.2 | 0.3 | 18.6 | 63.8 | 0.1 |
| | reader-local (node 0) | 6.6 | 0.1 | 20.3 | 13.1 | 0.8 |
| | reader (node 1) | 0.4 | **20.7** | 0.4 | 0.2 | **11.0** |

#### What it shows

1. **Readers do pull fresh grains out of the writer's cache, and head-3 mostly avoids it.**
   - **1080p head+1:** the cross-node reader took **35.8 M lines (69 % of the grain) as modified lines from
     node 0 caches**. The local reader took 13.8 M (27 %) the same way.
   - **1080p head-3:** these drop to 20.7 M and 6.6 M.
   - **2160p:** the cross reader's remote HITMs fall from 23.8 M to 2.7 M (-89 %), and the local reader's from
     8.2 M to 1.7 M (-79 %).

   This is the coherence traffic behind "a grain read at commit is slower than one read three frames later". It
   is correct data, served from another core's cache instead of from memory.
2. **The writer's fill is not non-temporal.** At 2160p the writer's CPUs pull about 300 M lines from local DRAM
   in 20 s: about 207 M RFOs for the lines `memset` overwrites, plus about 100 M for its own reader. So glibc
   2.39's `rep stosb` reads each line before writing it and leaves it in cache. Non-temporal stores are ruled
   out as the cause.
3. **The three readers feed each other, which invalidates the "cross-node from DRAM" result above.**
   - On node 0, local DRAM reads by the two local readers add up to about one grain per frame. The writer's
     column (308.6 - 207 RFO, about 100 M) plus reader-local (108.6 M) is about 207 M at 2160p head-3. About
     half of each local reader's lines (97-107 M) are clean lines forwarded from the other one.
   - Each line is fetched from DRAM **once** and then served cache-to-cache.
   - The cross-node reader's remote DRAM reads at 2160p head-3 are only **0.8 M of 207 M**. Almost all of its
     lines come from a source not counted here. Given the above, that is most likely `remote_cache.snoop_fwd`,
     a clean line forwarded over UPI from a node 0 reader's cache.
   - So the 4 % cross-node cost measured at 2160p head-3 is the cost of **remote-cache forwarding**, not of
     reading remote DRAM.
4. **1080p head-3 still sits partly in cache** (20.7 M remote HITM, 11.0 M remote DRAM for the cross reader),
   as expected from a 27.6 MB ring that fits in L3.

#### To measure remote DRAM for real

Run **one reader at a time**:
- remove lanes B and C from the writer's body (`del(.lanes.B, .lanes.C)`);
- run 2160p head-3 twice, once with only the local reader container and once with only the cross reader;
- count with a second event set that adds `remote_cache.snoop_fwd` and `l3_hit.snoop_hit_no_fwd`, so the
  categories add up to the grain.

Expect the cross reader's remote DRAM count to be about 207 M per 20 s. Its touch time against the local single
reader is then the true cost of a cross-node read.

### 6. One reader at a time: the real cross-node cost (.136, 2026-10-19, 21:18 to 21:26 UTC)

This removes the confound found above, where the readers fed each other.
- **One writer, lane A only.** Its lanes B and C are removed, so the writer container only writes.
- **One reader, lane B only,** either the local reader container (node 0) or the cross reader (node 1). The
  other reader container stays stopped.

Same layout A cpusets and `performance` governor. Each run is 90 s, and the counters ran for 30 s, starting 30 s
in. Each reader made 2 550 reads with 0 failed and every stamp matching. The driver and recorder are
`~/numa-runs/single-run.sh` and `single-record.sh` (sudo).

The counters now include `any_response` (the total) and `l3_hit` (all L3 hits), so the sources add up. The
grain stream is 345 600 lines x 899 grains, about **311 M lines per 30 s**.

#### Timings (ms, p50 / p99)

| 2160p copy | Local reader (node 0) | Cross reader (node 1) | Cross - local |
|---|---|---|---|
| **Reader touch, head-3** | 1.465 / 1.555 | 1.901 / 2.017 | **+0.44 / +0.46 (+30 %)** |
| **Reader touch, head+1** | 1.699 / 1.768 | 2.109 / 2.211 | **+0.41 / +0.44 (+24 %)** |
| Reader transit, head+1 | 0.030 / 0.038 | 0.032 / 0.042 | +0.002 / +0.004 |
| **Writer fill (`memset` 22 MB), with the reader at head-3** | 1.454 / 1.703 | 1.920 / 1.948 | **+0.47 (+32 %)** |
| **Writer fill, with the reader at head+1** | 1.484 / 1.641 | 1.985 / 2.009 | **+0.50 (+34 %)** |

The writer is on node 0 in every run. Only the reader moves.

#### Where the reader's lines came from (millions per 30 s, reader's cores)

| Run | Total (`any_response`) | L3 hit, own socket | of which modified in another core | Remote cache, modified line | Local DRAM | Remote DRAM |
|---|---:|---:|---:|---:|---:|---:|
| local, head-3 | 323.1 | 79.5 | 0.7 | 0.0 | 245.5 | 0.1 |
| cross, head-3 | 328.2 | 16.5 | 0.4 | 80.3 | 0.6 | **230.1** |
| local, head+1 | 324.7 | 110.6 | 24.8 | 0.1 | 213.2 | 0.1 |
| cross, head+1 | 322.5 | 11.5 | 0.4 | 114.4 | 0.5 | **196.2** |

`remote_cache.snoop_fwd` matched `remote_cache.snoop_hitm` to within 1 % (80.8 vs 80.3, 115.3 vs 114.4), so
nearly every line forwarded from the other socket was a modified one. The CSVs have every event. The stopped
reader's CPUs counted under 9 M lines, the idle baseline.

#### What it shows

1. **Reading across NUMA does cost something, about 30 % per 2160p grain.**
   - The cross reader really reads remote memory now: 230 M of 328 M lines from remote DRAM at head-3. The
     rest are modified lines still in node 0's L3, pulled over UPI.
   - The local reader gets the same grain from local DRAM (75 %) and its own L3 (25 %).
   - The copy takes 0.44 ms longer (11.6 vs 15.1 GB/s).

   The three-reader runs hid this: the cross reader was fed from the node 0 readers' caches, and showed only
   +4 %.
2. **A cross-node reader also slows the writer by about 0.5 ms (+32-34 %).** The writer never moved, yet its
   22 MB fill is slower whenever the reader is on node 1:
   - Before overwriting a line, the writer reads it to take ownership. When node 1 has read that line, that
     ownership request has to go to node 1 too.
   - At head-3, 155 M of the writer's 325 M ownership reads were answered from node 1's cache (the reader read
     that slot only two frames before).
   - At head+1 only 34 M were, yet the fill is still 0.5 ms slower. That points to the ownership request having
     to check with the remote socket even when no data comes back. This was an inference here; §7 confirms it
   with directory and snoop counters.

   In a deployment, every consumer of that flow, local ones included, then gets its grain later.
3. **The fresh-grain cost holds with one reader too.** head+1 is slower than head-3 for both readers: 1.70 vs
   1.47 ms local, 2.11 vs 1.90 ms cross. The local head+1 reader takes 24.8 M modified lines from the writer's
   cache.
4. **Readers sharing a grain changes things both ways.**
   - A lone local reader at head-3 (1.465 ms) is faster than the same reader among three (1.62 ms), because the
     three contend.
   - A lone cross reader (1.90 ms) is slower than one among three (1.69 ms), because it can no longer be fed
     from the node 0 readers' caches.
5. **Wake-up is unaffected.** Transit is 0.030 vs 0.032 ms. The cross-node cost is all in moving the payload.

#### For MXL deployments, corrected

- On this hardware a 2160p30 reader on the other socket adds about 0.4-0.45 ms per grain to its copy. That is
  about 1.3 % of the frame period, so it is fine for one consumer.
- It also adds about 0.5 ms to the writer's fill.
- Both scale with bytes moved over UPI. Many cross-socket readers, or several 2160p flows, will add up, and the
  guide's memory-stress and `pcm` UPI runs (sudo) are the way to find where it stops being negligible.
- **Keep writers and their heavy readers on the same socket when you can.** The governor still matters more:
  `schedutil` cost about 2x on every read.

### 7. Why a cross reader slows the writer: the memory directory (.136, 2026-10-20, 13:11 to 13:28 UTC)

This tests the guess above. The guess was that after a node 1 reader has touched a line, the writer's next ownership
request for it has to check with node 1 even when no data comes back. The setup is the same as the single-reader
runs: one writer (lane A), one reader (lane B), layout A, `performance` governor, 90 s runs, and counters over 30 s
starting 30 s in. This time the counters are uncore (CHA and M2M), counted per socket with `perf stat -a
--per-socket`. Socket 0 is home for the grain and the writer in every run.

There were two passes:
- **Pass 1:** the four single-reader runs.
- **Pass 2:** the two head+1 runs again, with the events pass 1 missed.

Drivers are `dir-run.sh` and `dir2-run.sh`, and the sudo recorders are `dir-record.sh` and `dir2-record.sh`. The
timings repeat §6 to within 0.05 ms. The exception is cross head-3 touch, at 2.04 ms vs 1.90 ms.

#### Timings (ms, p50 / p99)

| 2160p copy | Local reader | Cross reader | Cross - local |
|---|---|---|---|
| Writer fill, reader at head-3 (pass 1) | 1.433 / 1.591 | 1.945 / 2.004 | +0.51 |
| Writer fill, reader at head+1 (pass 1) | 1.463 / 1.562 | 1.994 / 2.024 | +0.53 |
| Writer fill, reader at head+1 (pass 2) | 1.471 / 1.745 | 1.981 / 2.010 | +0.51 |
| Reader touch, head+1 (pass 2) | 1.698 / 1.777 | 2.089 / 2.234 | +0.39 |

#### Memory directory and snoops (millions per 30 s)

The grain stream is still about 311 M lines per 30 s.

| Event | Socket | Local h-3 | Cross h-3 | Local h+1 | Cross h+1 |
|---|---|---:|---:|---:|---:|
| `m2m_directory_lookup.state_i` | S0 | 470.0 | 224.9 | 452.1 | 221.4 |
| `m2m_directory_lookup.state_a` (possibly cached remotely) | S0 | 0.3 | **299.4** | 0.3 | **305.9** |
| `m2m_directory_update.i2a` (marked remote by a node 1 read) | S0 | 0.0 | 223.6 | 0.0 | 219.6 |
| `m2m_directory_update.a2i` (taken back for the writer) | S0 | 0.2 | **299.0** | 0.2 | **304.3** |
| `cha_snoop_resp.rspifwd` (data came back) | S0 | 0.1 | 129.8 | 0.1 | 21.3 |
| `cha_snoop_resp.rspi` (no data) | S0 | 311.9 | 175.5 | 311.1 | 288.6 |

Pass 2, head+1 only:

| Event | Socket | Local h+1 | Cross h+1 |
|---|---|---:|---:|
| `cha_tor_inserts.ia_miss_rfo_pref` (writer's prefetch RFOs) | S0 | 233.9 | 289.8 |
| **RFO-prefetch miss latency, occupancy / inserts (CHA cycles)** | S0 | **242** | **304** |
| Demand RFO miss latency (CHA cycles), 13-17 M inserts | S0 | 243 | 300 |
| `cha_tor_inserts.snps_from_rem` (snoops arriving from the other socket) | S0 | 4.9 | 312.2 |
| `cha_tor_inserts.snps_from_rem` | S1 | 316.3 | 309.2 |
| `cha_requests.invitoe` (full-line writes, no read) | S0 | 306.3 | 0.0 |
| `cha_requests.invitoe` | S1 | 0.0 | 305.8 |
| `m2m_directory_lookup.state_a` | S0 | 1.3 | 310.2 |

`cha_snoops_sent.remote` was 0 in every run. On this chip it does not count snoops sent to the other socket.
`snps_from_rem` on the receiving socket does.

#### What it shows

1. **The guess was right: the writer waits for an empty snoop of node 1.**
   - SPR keeps a directory state per line in DRAM. When node 1 reads a node 0 line, that state goes from I to A
     ("may be cached remotely").
   - Node 1 later drops the clean line without telling anyone, so the state stays A.
   - When the writer comes back to that slot, about 300 M of its ownership requests per 30 s find state A. Each
     one must snoop node 1 before it can write, then reset the state to I.
   - At head+1, only 21 M of those snoops bring data back. The rest are round trips for nothing.
   - The writer's prefetch RFOs, the bulk of its ownership requests, take 26 % longer: 304 vs 242 cycles. The
     fill is limited by that latency, and it is 35 % longer.
   - In the local runs the directory stays at I and no snoop leaves socket 0 for the writer.
2. **Every frame also costs two directory writes per line.** About 220 M I->A updates and 300 M A->I updates per
   30 s happen only in the cross runs.
3. **The reader's copy sends one snoop per line to the other socket, local or not.** This was not expected.
   - At 2160p, `memcpy` is above glibc's 16 MiB non-temporal threshold. The reader's destination writes are
     `InvItoE`, about 306 M per 30 s, counted on whichever socket the reader runs.
   - An `InvItoE` writes the whole line without reading DRAM, so the home cannot see the directory bits stored
     there. It snoops the other socket instead.
   - That explains why idle socket 1 received 316 M snoops in the all-local run.
   - In the cross run, the reader's destination is on node 1, so those snoops go from S1 to S0 (312 M). The
     writer's state-A snoops go from S0 to S1 (309 M).

   These counts fit this reading. §8 confirms the destination is on node 1: node 1's DRAM writes
   grow by one grain stream per cross reader.

#### For MXL deployments

- A reader on the other socket slows its writer through the directory, not through bandwidth. Every other reader
  of that flow, local ones included, then gets each grain about 0.5 ms later at 2160p.
- A reader copying more than 16 MiB per grain sends about one snoop per line over UPI wherever it runs. Snoops are
  small messages, but they count against the link when there are many flows.

### 8. N readers on one flow (.136, 2026-10-20, 13:39 to 13:53 UTC)

This asks whether the cross-node cost grows with the number of consumers. There is one writer (lane A, 2160p,
scenario 15) and N readers in one reader container, all local (node 0) or all cross (node 1), for N = 1, 2, 4, 6.
- Each reader is lane B cloned onto lanes B..G, with its own instance and reader handles.
- The layout, governor and 90 s / 30 s windows are the same as before.
- Counters are per socket: UPI flits sent (`unc_upi_txl_flits.*`), DRAM CAS reads and writes (`unc_m_cas_count.*`),
  the writer's RFO-prefetch latency, snoops from the other socket, and directory state-A lookups.

Every lane made 2 550 reads, with 0 failed and every stamp matching. The driver is `multi-run.sh` and the sudo
recorder is `multi-record.sh`.

#### Timings (ms, p50; reader touch is the mean of the lanes' p50)

| N | Writer fill, local | Writer fill, cross | Reader touch, local | Reader touch, cross | Worst lane touch p99, local / cross |
|---|---|---|---|---|---|
| 1 | 1.482 | 1.994 | 1.651 | 2.123 | 1.707 / 2.213 |
| 2 | 1.664 | 1.997 | 1.747 | 1.955 | 1.812 / 2.031 |
| 4 | 1.653 | 1.999 | 2.470 | 2.589 | 2.572 / 2.755 |
| 6 | 1.653 | 2.009 | 2.475 | 2.754 | 2.832 / 2.870 |

Transit p50 goes from 0.030 to 0.039 ms with N, the same for local and cross.

#### Counters (millions per 30 s)

The grain stream is about 311 M lines per 30 s.

| Event | Socket | local x1 | cross x1 | cross x2 | cross x4 | cross x6 | local x6 |
|---|---|---:|---:|---:|---:|---:|---:|
| UPI data flits sent | S0 | 3 | 2 807 | 2 815 | 3 086 | 2 818 | 7 |
| UPI non-data flits sent | S0 | 1 203 | 4 006 | 4 407 | 6 197 | 7 298 | 5 838 |
| Snoops from the other socket | S1 | 316 | 311 | 311 | 314 | 312 | **1 861** |
| Snoops from the other socket | S0 | 0 | 311 | 624 | 1 254 | **1 867** | 1 |
| DRAM CAS writes | S1 | 17 | 328 | 639 | 1 265 | 1 886 | 18 |
| DRAM CAS writes | S0 | 577 | 835 | 839 | 834 | 857 | 2 197 |
| DRAM CAS reads | S0 | 481 | 638 | 641 | 679 | 646 | 643 |
| Directory lookups, state A | S0 | 0.1 | 307 | 312 | 339 | 312 | 0.2 |
| Writer RFO-prefetch miss latency (CHA cycles) | S0 | 241 | 303 | 306 | 305 | 306 | 243 |

#### What it shows

1. **The writer's cross-node cost is paid once per flow, not once per reader.**
   - Cross fill stays at about 2.00 ms, and the writer's RFO latency at about 305 cycles, for any N.
   - Directory state A is set or not, so more remote readers add no more snoops for the writer.
   - The local writer also slows down at N >= 2, from 1.48 to 1.65 ms, with no change in its RFO-miss latency.
     So the cross - local gap shrinks to about +0.35 ms. That local slowdown is not explained; the guess is
     invalidating copies held by several local readers.
2. **The grain crosses UPI once, however many node 1 readers there are.**
   - About 2.8 G data flits per 30 s for any N. That is 9 flits per 64-byte line, so 311 M lines, 5.3 Gb/s at
     2160p30.
   - Node 1 readers that miss the same line at the same time share one fetch, and node 0's DRAM reads stay flat.
3. **Readers contend with each other more than with the socket boundary.**
   - Two cross readers are faster than one (1.96 vs 2.12 ms) because they feed each other on node 1.
   - From N = 4, contention among readers dominates both cases: 2.47 local vs 2.59 cross. By N = 6 the worst
     lane's p99 is within 0.04 ms of local.
4. **Cross-socket snoops scale with readers, wherever they run: 311 M x N per 30 s.**
   - This is the non-temporal destination store from §7.
   - Six local readers send idle node 1 about 1.9 G snoops per 30 s.
   - In cross runs, node 1's DRAM writes grow by about 315 M per reader. That confirms each reader's destination
     buffer is on node 1.
5. **Directory updates are real DRAM writes.**
   - In cross runs, node 0 writes about 835 M lines per 30 s. The grain itself is 311 M, and in cross runs the
     reader's buffers are on node 1.
   - The extra ~520 M lines, about 1.1 GB/s, match the I->A plus A->I updates counted in §7.

#### For MXL deployments

- Putting several consumers of a flow on the other socket costs no more UPI data and no more writer time than
  putting one there. The cost is per flow crossing the boundary.
- Large copies (more than 16 MiB per grain) generate one cross-socket snoop per line per reader, local or not.
- This is far from UPI limits: one 2160p30 flow is 5.3 Gb/s of data. Flows are what scale UPI data, which is the
  next test.

### 9. Several flows, with and without light memory stress (.136, 2026-10-20, 14:00 to 14:23 UTC)

There are F 2160p flows (F = 1, 2, 4), each with one reader. The readers are all local (node 0) or all cross
(node 1). Each case runs once with no stress and once with `stress-ng --vm 8 --vm-bytes 4G --vm-keep` on node 0's
memory.
- The writer runs lane A of scenario 15 cloned onto lanes A..D, each with its own flow id and handles.
- The reader runs lane B cloned the same way, lane i reading flow i.
- stress-ng is started 15 s before each stressed run's flows and removed after.

Node 0 is split by physical core, so the writer, the local reader and stress-ng never share a core (the sibling of
CPU n is n+24). The guide's layout puts stress-ng on the siblings of the writer's cores.

| Role | CPUs |
|---|---|
| Writer | 0,2,4,6 + 24,26,28,30 |
| Local reader | 8,10,12,14 + 32,34,36,38 |
| stress-ng | 16,18,20,22 + 40,42,44,46 |
| Cross reader | 1-11 odd + 25-35 odd (unchanged) |

The writer has 8 CPUs here instead of 12, so compare within this section. Every lane made 2 550 reads, with 0
failed and every stamp matching. The driver is `flows-run.sh` and the sudo recorder is `flows-record.sh`, with the
same counters as §8.

#### Timings (ms, p50 mean of lanes; p99 of the worst lane)

| | Writer fill, local | Writer fill, cross | Reader touch, local | Reader touch, cross |
|---|---|---|---|---|
| F=1 | 1.469 / 1.589 | 1.980 / 2.008 | 1.668 / 1.753 | 2.163 / 2.295 |
| F=2 | 1.654 / 1.717 | 2.069 / 2.115 | 1.796 / 1.860 | 2.194 / 2.296 |
| F=4 | 1.864 / 3.069 | 2.322 / 3.626 | 2.257 / **3.267** | 2.306 / **2.425** |
| F=1, stress | 1.595 / 1.973 | 2.041 / 2.408 | 1.753 / 2.018 | 2.265 / 2.594 |
| F=2, stress | 1.716 / 2.146 | 2.073 / 2.627 | 1.846 / 2.155 | 2.320 / 2.626 |
| F=4, stress | 2.521 / 4.150 | 2.808 / **4.897** | 1.995 / 3.489 | 2.464 / **4.553** |

Transit p50 goes from 0.030 to 0.054 ms with F and stress, about the same for local and cross.

#### Counters (millions per 30 s unless noted)

| Event | Socket | F=1 L | F=1 X | F=2 L | F=2 X | F=4 L | F=4 X | F=1 L, stress | F=1 X, stress | F=4 L, stress | F=4 X, stress |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| UPI data flits sent | S0 | 37 | 2 842 | 4 | 5 611 | 4 | 11 205 | 3 | 2 806 | 10 | 11 217 |
| UPI non-data flits sent | S0 | 1 285 | 4 055 | 2 255 | 6 453 | 4 342 | 9 408 | 1 204 | 5 202 | 4 337 | 12 820 |
| Directory lookups, state A | S0 | 1 | 309 | 0 | 611 | 0 | 1 224 | 0 | 304 | 0 | 1 231 |
| Snoops from the other socket | S1 | 319 | 313 | 628 | 623 | 1 249 | 1 248 | 315 | **654** | 1 248 | **1 823** |
| Writer RFO-prefetch miss latency (cycles) | S0 | 242 | 303 | 253 | 318 | 282 | 360 | 255 | **265** | 280 | 315 |
| DRAM read + write (GB/s) | S0 | 2.3 | 3.2 | 4.9 | 6.3 | 10.3 | 12.0 | 21.3 | 22.1 | 29.0 | 32.0 |

L is local and X is cross. The F=2 stressed columns are in `dir.csv`, and they sit between F=1 and F=4.

#### What it shows

1. **UPI data scales exactly with flows: about 2.8 G flits per 30 s per 2160p30 flow** (5.3 Gb/s). Four cross
   flows move 21 Gb/s, still far from link limits.
2. **The writer's cross cost stays about the same per flow: +0.51, +0.42 and +0.46 ms at F = 1, 2, 4.**
   - The RFO latency gap grows slowly: 61, 65, then 78 cycles.
   - The writer's p99 of about 3 ms at F = 4 appears for local and cross alike. That is the writer's own
     contention, 4 writer lanes on 4 physical cores, not the socket boundary.
3. **At F = 4 the local readers lose their advantage.**
   - Touch p50 is 2.26 ms local vs 2.31 ms cross, and the local tail is worse: p99 3.27 vs 2.43 ms.
   - Local readers share node 0's L3 and memory controller with four writers. Cross readers get node 1's L3 to
     themselves.
4. **Light memory stress hit local and cross about equally at F <= 2.**
   - Both lose about 0.1 ms at p50 and 0.3 ms at p99.
   - Only F = 4 under stress gives cross a clearly worse tail: fill p99 4.90 vs 4.15 ms, touch p99 4.55 vs 3.49 ms.
   - The worst p99 in the whole set is under 5 ms of a 33 ms frame.
5. **This stress was light.** `--vm 8` on four physical cores added only about 20 GB/s to node 0, against the
   guide's 63 GB/s. So the guide's prediction, that cross degrades more under memory load, is not really tested
   here. §10 tests it.
6. **Two things under stress are not explained.**
   - **The cross writer's RFO latency drops from 303 to 265 cycles, while local rises from 242 to 255.** One
     possible reason: the snoop to node 1 runs in parallel with the DRAM read, so once DRAM is slower the snoop
     hides behind it. §10 supports this.
   - **Socket 1 receives extra snoops only in the stressed cross runs**, for example 654 vs 313 M at F = 1.

#### For MXL deployments

- Each flow whose readers sit on the other socket costs its writer about 0.5 ms per grain at 2160p and 5.3 Gb/s
  of UPI data. Neither grows with the number of readers.
- With several heavy flows on one socket, moving readers to the other socket can help. They get their own L3, and
  at F = 4 they had a better tail than the local readers.
- None of this approached a frame period. The open question is behaviour under heavy memory load (next pass).

### 10. Heavy memory stress (.136, 2026-10-20, 14:58 to 15:06 UTC)

This is the same layout and flows as §9, at F = 1 and F = 4, local and cross. stress-ng runs as
`--stream 8 --timeout 900s` on the stress cores (16-22 even and 40-46 even, mems 0) instead of `--vm`.
- A 15 s calibration on those cores reported 8 x about 8.6 GB/s, about 69 GB/s. That is stress-ng 0.07.05's own
  figure.
- The CAS counters measured **91-97 GB/s** of DRAM traffic on node 0 during the runs, against 2-12 GB/s without
  stress.
- The baselines are §9's no-stress runs, same layout, about an hour earlier.

Every lane made 2 550 reads, with 0 failed and every stamp matching. The driver is `stress-run.sh` and the sudo
recorder is `stress-record.sh`, with the same counters.

#### Timings (ms, p50 mean of lanes; p99 of the worst lane)

| | Writer fill, local | Writer fill, cross | Reader touch, local | Reader touch, cross |
|---|---|---|---|---|
| F=1, no stress | 1.469 / 1.589 | 1.980 / 2.008 | 1.668 / 1.753 | 2.163 / 2.295 |
| **F=1, stream** | 1.947 / 1.981 | 2.296 / 2.328 | 1.973 / 2.004 | 2.420 / 2.457 |
| F=4, no stress | 1.864 / 3.069 | 2.322 / 3.626 | 2.257 / 3.267 | 2.306 / 2.425 |
| **F=4, stream** | 3.210 / 4.352 | 3.771 / 4.756 | 2.271 / **3.952** | 2.688 / **2.952** |

Transit p50 rises to 0.045-0.078 ms under this stress, for local and cross alike.

#### Counters (millions per 30 s unless noted)

| Event | Socket | F=1 L | F=1 X | F=4 L | F=4 X | F=1 L, stream | F=1 X, stream | F=4 L, stream | F=4 X, stream |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| DRAM read + write (GB/s) | S0 | 2.3 | 3.2 | 10.3 | 12.0 | 90.8 | 91.2 | 95.7 | 97.5 |
| Writer RFO-prefetch miss latency (cycles) | S0 | 242 | 303 | 282 | 360 | 279 | **283** | 297 | **318** |
| Snoops from the other socket | S1 | 319 | 313 | 1 249 | 1 248 | **3 481** | 5 257 | 4 442 | 7 207 |
| UPI non-data flits sent | S0 | 1 285 | 4 055 | 4 342 | 9 408 | 13 509 | 20 918 | 16 711 | 29 436 |
| UPI data flits sent | S0 | 37 | 2 842 | 4 | 11 205 | 44 | 2 847 | 53 | 11 261 |
| Directory lookups, state A | S0 | 1 | 309 | 0 | 1 224 | 2 | 310 | 2 | 1 234 |

#### What it shows

1. **Under heavy memory load, cross does not degrade more than local at F = 1.** That is the opposite of the
   guide's prediction.
   - Reader touch rises +0.31 ms local and +0.26 ms cross.
   - The writer's cross - local gap shrinks from 0.51 to 0.35 ms.
2. **The writer's snoop hides behind the slower DRAM read.**
   - The cross - local gap in RFO-prefetch latency at the CHA collapses from 61 to 4 cycles at F = 1, and from 78
     to 21 at F = 4.
   - That supports the explanation offered in §9: the snoop to node 1 runs in parallel with
     the DRAM read, so once local DRAM is slow the snoop costs little.
   - The fill gap only shrinks to 0.35 ms, though. The CHA-side RFO latency does not account for all of the
     writer's cross cost, and the rest is not explained.
3. **At F = 4 under load, the writer suffers most, local or cross.**
   - Fill p50 is 3.21 / 3.77 ms, and p99 is 4.35 / 4.76 ms.
   - Cross readers are slower at p50 (2.69 vs 2.27 ms), but the local readers again have the worse tail (p99 3.95
     vs 2.95 ms).
   - The worst p99 in the whole study is still under 5 ms of a 33 ms frame.
4. **A memory-heavy neighbour floods UPI with snoops, even when everything is local.**
   - With stress-ng on node 0 and no cross reader, socket 1 received 3.5 G snoops per 30 s, 11x one reader, and
     node 0's UPI non-data flits went from 1.3 to 13.5 G.
   - The likely cause is STREAM's non-temporal stores, the same mechanism as the reader's large `memcpy`. That is
     inferred, not measured.
   - The extra snoops in cross runs on top of that (5 257 vs 3 481 M) remain unexplained, as in the light pass.

#### For MXL deployments

- A socket under heavy memory load slows every consumer about equally. Moving consumers off it does not make
  their reads worse, and it hides most of the writer's directory-snoop cost.
- Memory-streaming neighbours generate cross-socket snoop traffic far larger than MXL's own.

## Appendix A: raw `mxl-transit-stats.py` output (§1-4)

<details>
<summary>Raw <code>mxl-transit-stats.py</code> output</summary>

Lane C is left out (see §1).

```
08-transit-local-lines/reader.ndjson  lane B  mxlFlowReaderGetGrain
  calls       8850 ok, 0 failed
  indices     53719209995..53719218844, 8850 read, 0 indexes not read
  transit_ms  min    0.034  p10    0.068  p50    0.074  p90    0.081  p99    0.094  max    0.447   mean    0.074
  touch_ms    min    0.372  p10    0.513  p50    0.634  p90    0.906  p99    1.016  max    1.490   mean    0.696
  end_to_end  min    0.495  p10    0.583  p50    0.706  p90    0.983  p99    1.095  max    1.564   mean    0.770
  age_ms      min    0.581  p10    0.916  p50    0.968  p90    1.035  p99    1.079  max    1.979   mean    0.971

08-transit-local-lines/writer.ndjson  lane B  mxlFlowReaderGetGrain
  calls       8850 ok, 0 failed
  indices     53719209995..53719218844, 8850 read, 0 indexes not read
  transit_ms  min    0.037  p10    0.071  p50    0.074  p90    0.081  p99    0.093  max    0.446   mean    0.075
  touch_ms    min    0.424  p10    0.758  p50    0.842  p90    0.915  p99    1.085  max    1.746   mean    0.825
  end_to_end  min    0.481  p10    0.833  p50    0.918  p90    0.990  p99    1.167  max    1.822   mean    0.899
  age_ms      min    0.579  p10    0.915  p50    0.969  p90    1.036  p99    1.078  max    1.995   mean    0.972

10-transit-local-stamp/reader.ndjson  lane B  mxlFlowReaderGetGrain
  calls       8850 ok, 0 failed
  indices     53719172951..53719181800, 8850 read, 0 indexes not read
  transit_ms  min    0.030  p10    0.070  p50    0.074  p90    0.081  p99    0.092  max    0.880   mean    0.075
  age_ms      min    0.289  p10    0.859  p50    0.869  p90    0.877  p99    0.888  max    1.891   mean    0.853

10-transit-local-stamp/writer.ndjson  lane B  mxlFlowReaderGetGrain
  calls       8850 ok, 0 failed
  indices     53719172951..53719181800, 8850 read, 0 indexes not read
  transit_ms  min    0.027  p10    0.069  p50    0.073  p90    0.079  p99    0.089  max    0.886   mean    0.073
  age_ms      min    0.289  p10    0.858  p50    0.867  p90    0.875  p99    0.886  max    1.910   mean    0.852

11-transit-local-copy/reader.ndjson  lane B  mxlFlowReaderGetGrain
  calls       8850 ok, 0 failed
  indices     53719247041..53719255890, 8850 read, 0 indexes not read
  transit_ms  min    0.030  p10    0.067  p50    0.071  p90    0.079  p99    0.091  max    0.530   mean    0.072
  touch_ms    min    0.570  p10    0.675  p50    0.691  p90    1.451  p99    1.555  max    4.978   mean    0.836
  end_to_end  min    0.637  p10    0.745  p50    0.762  p90    1.529  p99    1.635  max    5.071   mean    0.908
  age_ms      min    0.584  p10    0.903  p50    0.938  p90    0.963  p99    1.025  max    1.630   mean    0.935

11-transit-local-copy/writer.ndjson  lane B  mxlFlowReaderGetGrain
  calls       8850 ok, 0 failed
  indices     53719247041..53719255890, 8850 read, 0 indexes not read
  transit_ms  min    0.034  p10    0.070  p50    0.073  p90    0.080  p99    0.091  max    0.533   mean    0.074
  touch_ms    min    0.579  p10    1.380  p50    1.453  p90    1.470  p99    1.609  max    5.592   mean    1.396
  end_to_end  min    0.664  p10    1.453  p50    1.526  p90    1.546  p99    1.692  max    5.678   mean    1.470
  age_ms      min    0.581  p10    0.905  p50    0.941  p90    0.964  p99    1.025  max    1.646   mean    0.937

```

</details>


<details>
<summary>Raw <code>mxl-transit-stats.py --lane B</code> output, follow-up layouts A and B</summary>

```
A/08-transit-local-lines/reader-local.ndjson  lane B  mxlFlowReaderGetGrain
  calls       8850 ok, 0 failed
  indices     53719326655..53719335504, 8850 read, 0 indexes not read
  transit_ms  min    0.039  p10    0.071  p50    0.075  p90    0.083  p99    0.104  max    0.471   mean    0.077
  touch_ms    min    0.234  p10    0.761  p50    0.791  p90    1.009  p99    1.246  max    1.571   mean    0.866
  end_to_end  min    0.291  p10    0.836  p50    0.866  p90    1.089  p99    1.330  max    1.666   mean    0.942
  age_ms      min    0.651  p10    1.007  p50    1.071  p90    1.090  p99    1.108  max    1.753   mean    1.058

A/08-transit-local-lines/reader.ndjson  lane B  mxlFlowReaderGetGrain
  calls       8850 ok, 0 failed
  indices     53719326655..53719335504, 8850 read, 0 indexes not read
  transit_ms  min    0.036  p10    0.074  p50    0.078  p90    0.087  p99    0.106  max    0.474   mean    0.079
  touch_ms    min    0.414  p10    0.833  p50    0.870  p90    0.916  p99    1.065  max    1.596   mean    0.873
  end_to_end  min    0.485  p10    0.911  p50    0.949  p90    0.999  p99    1.151  max    1.670   mean    0.952
  age_ms      min    0.652  p10    1.009  p50    1.074  p90    1.093  p99    1.110  max    1.755   mean    1.061

A/08-transit-local-lines/writer.ndjson  lane B  mxlFlowReaderGetGrain
  calls       8850 ok, 0 failed
  indices     53719326655..53719335504, 8850 read, 0 indexes not read
  transit_ms  min    0.035  p10    0.071  p50    0.076  p90    0.084  p99    0.104  max    0.469   mean    0.077
  touch_ms    min    0.438  p10    0.766  p50    0.927  p90    1.022  p99    1.244  max    1.846   mean    0.895
  end_to_end  min    0.500  p10    0.842  p50    1.004  p90    1.103  p99    1.327  max    1.917   mean    0.972
  age_ms      min    0.648  p10    1.008  p50    1.071  p90    1.091  p99    1.108  max    1.762   mean    1.058

A/11-transit-local-copy/reader-local.ndjson  lane B  mxlFlowReaderGetGrain
  calls       8850 ok, 0 failed
  indices     53719317068..53719325917, 8850 read, 0 indexes not read
  transit_ms  min    0.036  p10    0.074  p50    0.078  p90    0.086  p99    0.097  max    0.337   mean    0.079
  touch_ms    min    0.435  p10    1.483  p50    1.568  p90    1.646  p99    1.708  max    5.282   mean    1.566
  end_to_end  min    0.486  p10    1.562  p50    1.648  p90    1.726  p99    1.793  max    5.371   mean    1.645
  age_ms      min    0.613  p10    0.940  p50    0.989  p90    1.014  p99    1.032  max    1.258   mean    0.982

A/11-transit-local-copy/reader.ndjson  lane B  mxlFlowReaderGetGrain
  calls       8850 ok, 0 failed
  indices     53719317068..53719325917, 8850 read, 0 indexes not read
  transit_ms  min    0.040  p10    0.076  p50    0.080  p90    0.087  p99    0.101  max    0.339   mean    0.081
  touch_ms    min    0.628  p10    1.502  p50    1.540  p90    1.593  p99    1.623  max    5.642   mean    1.535
  end_to_end  min    0.714  p10    1.581  p50    1.622  p90    1.675  p99    1.709  max    5.728   mean    1.617
  age_ms      min    0.620  p10    0.943  p50    0.992  p90    1.016  p99    1.034  max    1.256   mean    0.985

A/11-transit-local-copy/writer.ndjson  lane B  mxlFlowReaderGetGrain
  calls       8850 ok, 0 failed
  indices     53719317068..53719325917, 8850 read, 0 indexes not read
  transit_ms  min    0.038  p10    0.073  p50    0.079  p90    0.087  p99    0.099  max    0.343   mean    0.080
  touch_ms    min    0.432  p10    1.492  p50    1.569  p90    1.646  p99    1.707  max    5.293   mean    1.567
  end_to_end  min    0.497  p10    1.577  p50    1.648  p90    1.726  p99    1.794  max    5.372   mean    1.647
  age_ms      min    0.617  p10    0.940  p50    0.990  p90    1.016  p99    1.033  max    1.263   mean    0.983

B/08-transit-local-lines/reader-local.ndjson  lane B  mxlFlowReaderGetGrain
  calls       8850 ok, 0 failed
  indices     53719346161..53719355010, 8850 read, 0 indexes not read
  transit_ms  min    0.028  p10    0.066  p50    0.070  p90    0.080  p99    0.089  max    0.514   mean    0.071
  touch_ms    min    0.457  p10    0.488  p50    0.502  p90    0.797  p99    1.239  max    1.826   mean    0.579
  end_to_end  min    0.514  p10    0.557  p50    0.571  p90    0.874  p99    1.320  max    1.892   mean    0.651
  age_ms      min    0.631  p10    0.974  p50    1.033  p90    1.070  p99    1.097  max    1.774   mean    1.027

B/08-transit-local-lines/reader.ndjson  lane B  mxlFlowReaderGetGrain
  calls       8850 ok, 0 failed
  indices     53719346161..53719355010, 8850 read, 0 indexes not read
  transit_ms  min    0.036  p10    0.071  p50    0.075  p90    0.083  p99    0.095  max    0.519   mean    0.077
  touch_ms    min    0.474  p10    0.756  p50    0.788  p90    0.869  p99    0.985  max    1.533   mean    0.798
  end_to_end  min    0.549  p10    0.830  p50    0.864  p90    0.949  p99    1.069  max    1.630   mean    0.874
  age_ms      min    0.629  p10    0.979  p50    1.039  p90    1.074  p99    1.099  max    1.779   mean    1.033

B/08-transit-local-lines/writer.ndjson  lane B  mxlFlowReaderGetGrain
  calls       8850 ok, 0 failed
  indices     53719346161..53719355010, 8850 read, 0 indexes not read
  transit_ms  min    0.033  p10    0.070  p50    0.075  p90    0.082  p99    0.092  max    0.521   mean    0.076
  touch_ms    min    0.482  p10    0.580  p50    0.791  p90    1.019  p99    1.237  max    1.825   mean    0.790
  end_to_end  min    0.561  p10    0.652  p50    0.871  p90    1.096  p99    1.322  max    1.892   mean    0.866
  age_ms      min    0.633  p10    0.979  p50    1.037  p90    1.074  p99    1.099  max    1.797   mean    1.032

B/11-transit-local-copy/reader-local.ndjson  lane B  mxlFlowReaderGetGrain
  calls       8850 ok, 0 failed
  indices     53719336575..53719345424, 8850 read, 0 indexes not read
  transit_ms  min    0.056  p10    0.076  p50    0.080  p90    0.087  p99    0.100  max    0.622   mean    0.081
  touch_ms    min    0.622  p10    1.520  p50    1.602  p90    1.681  p99    1.754  max    5.560   mean    1.588
  end_to_end  min    0.694  p10    1.601  p50    1.683  p90    1.763  p99    1.837  max    5.641   mean    1.669
  age_ms      min    0.627  p10    0.917  p50    0.976  p90    1.019  p99    1.039  max    1.772   mean    0.972

B/11-transit-local-copy/reader.ndjson  lane B  mxlFlowReaderGetGrain
  calls       8850 ok, 0 failed
  indices     53719336575..53719345424, 8850 read, 0 indexes not read
  transit_ms  min    0.058  p10    0.075  p50    0.079  p90    0.086  p99    0.099  max    0.621   mean    0.080
  touch_ms    min    0.673  p10    1.473  p50    1.535  p90    1.585  p99    1.616  max    4.576   mean    1.499
  end_to_end  min    0.763  p10    1.552  p50    1.617  p90    1.668  p99    1.701  max    4.660   mean    1.579
  age_ms      min    0.632  p10    0.916  p50    0.975  p90    1.018  p99    1.038  max    1.774   mean    0.971

B/11-transit-local-copy/writer.ndjson  lane B  mxlFlowReaderGetGrain
  calls       8850 ok, 0 failed
  indices     53719336575..53719345424, 8850 read, 0 indexes not read
  transit_ms  min    0.059  p10    0.074  p50    0.079  p90    0.086  p99    0.099  max    0.625   mean    0.080
  touch_ms    min    0.620  p10    1.525  p50    1.603  p90    1.681  p99    1.752  max    5.943   mean    1.599
  end_to_end  min    0.694  p10    1.603  p50    1.684  p90    1.763  p99    1.836  max    6.026   mean    1.679
  age_ms      min    0.629  p10    0.917  p50    0.975  p90    1.017  p99    1.038  max    1.783   mean    0.971

```

</details>

<details>
<summary>Raw <code>mxl-transit-stats.py --lane B</code> output, <code>performance</code> governor, layouts A and B</summary>

```
A/08-transit-local-lines/reader-local.ndjson  lane B  mxlFlowReaderGetGrain
  calls       8850 ok, 0 failed
  indices     53719378299..53719387148, 8850 read, 0 indexes not read
  transit_ms  min    0.024  p10    0.030  p50    0.033  p90    0.065  p99    0.114  max    3.295   mean    0.043
  touch_ms    min    0.171  p10    0.379  p50    0.517  p90    0.608  p99    0.669  max    0.970   mean    0.516
  end_to_end  min    0.213  p10    0.453  p50    0.548  p90    0.652  p99    0.740  max    3.750   mean    0.559
  age_ms      min    0.286  p10    0.579  p50    0.786  p90    0.881  p99    0.901  max    4.178   mean    0.754

A/08-transit-local-lines/reader.ndjson  lane B  mxlFlowReaderGetGrain
  calls       8850 ok, 0 failed
  indices     53719378299..53719387148, 8850 read, 0 indexes not read
  transit_ms  min    0.026  p10    0.032  p50    0.035  p90    0.068  p99    0.117  max   19.253   mean    0.049
  touch_ms    min    0.310  p10    0.402  p50    0.519  p90    0.605  p99    0.669  max    0.845   mean    0.521
  end_to_end  min    0.360  p10    0.482  p50    0.554  p90    0.652  p99    0.741  max   19.794   mean    0.570
  age_ms      min    0.288  p10    0.583  p50    0.787  p90    0.883  p99    0.903  max   19.843   mean    0.760

A/08-transit-local-lines/writer.ndjson  lane B  mxlFlowReaderGetGrain
  calls       8850 ok, 0 failed
  indices     53719378299..53719387148, 8850 read, 0 indexes not read
  transit_ms  min    0.025  p10    0.030  p50    0.034  p90    0.065  p99    0.111  max    3.482   mean    0.045
  touch_ms    min    0.172  p10    0.378  p50    0.516  p90    0.608  p99    0.668  max    0.834   mean    0.516
  end_to_end  min    0.233  p10    0.453  p50    0.548  p90    0.653  p99    0.742  max    3.868   mean    0.561
  age_ms      min    0.284  p10    0.580  p50    0.786  p90    0.882  p99    0.902  max    5.510   mean    0.756

A/11-transit-local-copy/reader-local.ndjson  lane B  mxlFlowReaderGetGrain
  calls       8850 ok, 0 failed
  indices     53719368721..53719377570, 8850 read, 0 indexes not read
  transit_ms  min    0.024  p10    0.030  p50    0.032  p90    0.035  p99    0.041  max    0.303   mean    0.032
  touch_ms    min    0.496  p10    0.663  p50    0.682  p90    0.732  p99    0.803  max    2.740   mean    0.693
  end_to_end  min    0.552  p10    0.694  p50    0.714  p90    0.765  p99    0.837  max    2.777   mean    0.725
  age_ms      min    0.350  p10    0.697  p50    0.751  p90    0.776  p99    0.798  max    1.009   mean    0.743

A/11-transit-local-copy/reader.ndjson  lane B  mxlFlowReaderGetGrain
  calls       8850 ok, 0 failed
  indices     53719368721..53719377570, 8850 read, 0 indexes not read
  transit_ms  min    0.026  p10    0.032  p50    0.034  p90    0.037  p99    0.045  max    0.296   mean    0.034
  touch_ms    min    0.540  p10    0.660  p50    0.680  p90    0.730  p99    0.800  max    3.078   mean    0.691
  end_to_end  min    0.584  p10    0.694  p50    0.714  p90    0.765  p99    0.837  max    3.124   mean    0.725
  age_ms      min    0.359  p10    0.699  p50    0.754  p90    0.778  p99    0.799  max    1.011   mean    0.746

A/11-transit-local-copy/writer.ndjson  lane B  mxlFlowReaderGetGrain
  calls       8850 ok, 0 failed
  indices     53719368720..53719377569, 8850 read, 0 indexes not read
  transit_ms  min    0.025  p10    0.031  p50    0.033  p90    0.036  p99    0.042  max    0.304   mean    0.033
  touch_ms    min    0.495  p10    0.661  p50    0.681  p90    0.732  p99    0.802  max    3.148   mean    0.692
  end_to_end  min    0.552  p10    0.694  p50    0.714  p90    0.765  p99    0.837  max    3.182   mean    0.725
  age_ms      min    0.291  p10    0.697  p50    0.753  p90    0.777  p99    0.799  max    1.012   mean    0.744

B/08-transit-local-lines/reader-local.ndjson  lane B  mxlFlowReaderGetGrain
  calls       8850 ok, 0 failed
  indices     53719397798..53719406647, 8850 read, 0 indexes not read
  transit_ms  min    0.026  p10    0.035  p50    0.052  p90    0.100  p99    0.145  max    3.479   mean    0.068
  touch_ms    min    0.182  p10    0.296  p50    0.563  p90    0.631  p99    0.687  max    3.060   mean    0.517
  end_to_end  min    0.271  p10    0.376  p50    0.608  p90    0.688  p99    0.802  max    3.952   mean    0.585
  age_ms      min    0.499  p10    0.572  p50    0.602  p90    0.717  p99    0.892  max    6.197   mean    0.641

B/08-transit-local-lines/reader.ndjson  lane B  mxlFlowReaderGetGrain
  calls       8850 ok, 0 failed
  indices     53719397799..53719406648, 8850 read, 0 indexes not read
  transit_ms  min    0.027  p10    0.037  p50    0.056  p90    0.103  p99    0.151  max    3.516   mean    0.072
  touch_ms    min    0.305  p10    0.355  p50    0.557  p90    0.633  p99    0.684  max    0.904   mean    0.525
  end_to_end  min    0.352  p10    0.438  p50    0.607  p90    0.686  p99    0.781  max    4.156   mean    0.596
  age_ms      min    0.511  p10    0.575  p50    0.605  p90    0.720  p99    0.904  max    6.191   mean    0.645

B/08-transit-local-lines/writer.ndjson  lane B  mxlFlowReaderGetGrain
  calls       8850 ok, 0 failed
  indices     53719397798..53719406647, 8850 read, 0 indexes not read
  transit_ms  min    0.026  p10    0.036  p50    0.053  p90    0.099  p99    0.147  max    3.571   mean    0.072
  touch_ms    min    0.179  p10    0.296  p50    0.561  p90    0.630  p99    0.688  max    0.838   mean    0.516
  end_to_end  min    0.281  p10    0.376  p50    0.608  p90    0.688  p99    0.809  max    3.951   mean    0.588
  age_ms      min    0.501  p10    0.574  p50    0.603  p90    0.717  p99    0.982  max    6.199   mean    0.645

B/11-transit-local-copy/reader-local.ndjson  lane B  mxlFlowReaderGetGrain
  calls       8850 ok, 0 failed
  indices     53719388215..53719397064, 8850 read, 0 indexes not read
  transit_ms  min    0.027  p10    0.037  p50    0.056  p90    0.100  p99    0.144  max    3.746   mean    0.073
  touch_ms    min    0.398  p10    0.643  p50    0.806  p90    0.890  p99    0.955  max    3.282   mean    0.789
  end_to_end  min    0.430  p10    0.728  p50    0.859  p90    0.972  p99    1.082  max    4.529   mean    0.862
  age_ms      min    0.493  p10    0.573  p50    0.604  p90    0.714  p99    0.860  max    4.969   mean    0.642

B/11-transit-local-copy/reader.ndjson  lane B  mxlFlowReaderGetGrain
  calls       8850 ok, 0 failed
  indices     53719388215..53719397064, 8850 read, 0 indexes not read
  transit_ms  min    0.027  p10    0.037  p50    0.056  p90    0.100  p99    0.143  max    5.853   mean    0.072
  touch_ms    min    0.501  p10    0.669  p50    0.793  p90    0.886  p99    0.952  max    4.137   mean    0.784
  end_to_end  min    0.542  p10    0.740  p50    0.850  p90    0.954  p99    1.063  max    6.733   mean    0.856
  age_ms      min    0.506  p10    0.573  p50    0.605  p90    0.714  p99    0.869  max    7.169   mean    0.642

B/11-transit-local-copy/writer.ndjson  lane B  mxlFlowReaderGetGrain
  calls       8850 ok, 0 failed
  indices     53719388215..53719397064, 8850 read, 0 indexes not read
  transit_ms  min    0.025  p10    0.037  p50    0.055  p90    0.099  p99    0.149  max   44.304   mean    0.079
  touch_ms    min    0.402  p10    0.644  p50    0.805  p90    0.891  p99    0.955  max    3.290   mean    0.790
  end_to_end  min    0.439  p10    0.729  p50    0.859  p90    0.972  p99    1.084  max   45.136   mean    0.869
  age_ms      min    0.495  p10    0.573  p50    0.604  p90    0.714  p99    0.879  max   44.885   mean    0.649

```

</details>

<details>
<summary>Raw <code>mxl-transit-stats.py --lane B</code> output, host .136, 2160p and 1080p head-3, layout A</summary>

```
A/14-transit-local-lines-2160p/reader-local.ndjson  lane B  mxlFlowReaderGetGrain
  calls       8850 ok, 0 failed
  indices     53719506047..53719514896, 8850 read, 0 indexes not read
  transit_ms  min    0.026  p10    0.032  p50    0.033  p90    0.036  p99    0.042  max    0.237   mean    0.034
  touch_ms    min    1.237  p10    1.481  p50    1.493  p90    1.531  p99    1.942  max    3.727   mean    1.513
  end_to_end  min    1.284  p10    1.514  p50    1.527  p90    1.565  p99    1.978  max    3.762   mean    1.546
  age_ms      min    1.694  p10    1.999  p50    2.015  p90    2.024  p99    2.039  max    2.278   mean    2.014

A/14-transit-local-lines-2160p/reader.ndjson  lane B  mxlFlowReaderGetGrain
  calls       8850 ok, 0 failed
  indices     53719506047..53719514896, 8850 read, 0 indexes not read
  transit_ms  min    0.028  p10    0.034  p50    0.035  p90    0.038  p99    0.046  max    0.240   mean    0.036
  touch_ms    min    1.252  p10    1.478  p50    1.490  p90    1.529  p99    1.941  max    2.993   mean    1.510
  end_to_end  min    1.319  p10    1.513  p50    1.526  p90    1.565  p99    1.978  max    3.027   mean    1.546
  age_ms      min    1.695  p10    2.002  p50    2.017  p90    2.026  p99    2.040  max    2.279   mean    2.016

A/14-transit-local-lines-2160p/writer.ndjson  lane B  mxlFlowReaderGetGrain
  calls       8850 ok, 0 failed
  indices     53719506046..53719514895, 8850 read, 0 indexes not read
  transit_ms  min    0.026  p10    0.032  p50    0.034  p90    0.037  p99    0.043  max    0.238   mean    0.035
  touch_ms    min    1.240  p10    1.479  p50    1.492  p90    1.531  p99    1.942  max    3.729   mean    1.512
  end_to_end  min    1.288  p10    1.514  p50    1.527  p90    1.565  p99    1.978  max    3.762   mean    1.546
  age_ms      min    1.693  p10    2.000  p50    2.016  p90    2.024  p99    2.039  max    2.284   mean    2.014

A/15-transit-local-copy-2160p/reader-local.ndjson  lane B  mxlFlowReaderGetGrain
  calls       8850 ok, 0 failed
  indices     53719486881..53719495730, 8850 read, 0 indexes not read
  transit_ms  min    0.026  p10    0.032  p50    0.034  p90    0.038  p99    0.042  max    0.162   mean    0.035
  touch_ms    min    1.717  p10    1.829  p50    1.848  p90    1.867  p99    1.894  max   12.068   mean    1.852
  end_to_end  min    1.751  p10    1.863  p50    1.883  p90    1.902  p99    1.928  max   12.112   mean    1.887
  age_ms      min    1.742  p10    2.028  p50    2.044  p90    2.054  p99    2.066  max    2.593   mean    2.042

A/15-transit-local-copy-2160p/reader.ndjson  lane B  mxlFlowReaderGetGrain
  calls       8850 ok, 0 failed
  indices     53719486881..53719495730, 8850 read, 0 indexes not read
  transit_ms  min    0.027  p10    0.034  p50    0.036  p90    0.039  p99    0.044  max    0.164   mean    0.037
  touch_ms    min    1.738  p10    1.827  p50    1.846  p90    1.865  p99    1.892  max   12.016   mean    1.850
  end_to_end  min    1.772  p10    1.863  p50    1.883  p90    1.902  p99    1.928  max   12.065   mean    1.887
  age_ms      min    1.747  p10    2.030  p50    2.046  p90    2.055  p99    2.067  max    2.594   mean    2.044

A/15-transit-local-copy-2160p/writer.ndjson  lane B  mxlFlowReaderGetGrain
  calls       8850 ok, 0 failed
  indices     53719486881..53719495730, 8850 read, 0 indexes not read
  transit_ms  min    0.027  p10    0.033  p50    0.035  p90    0.038  p99    0.043  max    0.161   mean    0.036
  touch_ms    min    1.717  p10    1.828  p50    1.848  p90    1.866  p99    1.892  max   12.073   mean    1.851
  end_to_end  min    1.751  p10    1.863  p50    1.883  p90    1.902  p99    1.928  max   12.112   mean    1.887
  age_ms      min    1.738  p10    2.029  p50    2.045  p90    2.055  p99    2.066  max    2.601   mean    2.043

A/16-transit-local-lines-2160p-lag3/reader-local.ndjson  lane B  mxlFlowReaderGetGrain
  calls       17700 ok, 0 failed
  indices     53719496461..53719505313, 17700 read, 0 indexes not read
  stamps      8850 without a valid stamp, 0 stamped with another index
  transit_ms  min   99.757  p10  100.085  p50  100.149  p90  100.211  p99  100.324  max  100.885   mean  100.148
  touch_ms    min    1.199  p10    1.268  p50    1.291  p90    1.361  p99    1.450  max    3.431   mean    1.305
  end_to_end  min  100.997  p10  101.377  p50  101.447  p90  101.540  p99  101.660  max  104.316   mean  101.453
  age_ms      min    1.636  p10    2.465  p50   52.300  p90  102.629  p99  102.700  max  102.953   mean   52.544

A/16-transit-local-lines-2160p-lag3/reader.ndjson  lane B  mxlFlowReaderGetGrain
  calls       17700 ok, 0 failed
  indices     53719496461..53719505313, 17700 read, 0 indexes not read
  stamps      8850 without a valid stamp, 0 stamped with another index
  transit_ms  min   99.759  p10  100.086  p50  100.150  p90  100.212  p99  100.325  max  100.886   mean  100.149
  touch_ms    min    1.229  p10    1.267  p50    1.291  p90    1.360  p99    1.452  max    3.024   mean    1.305
  end_to_end  min  101.029  p10  101.378  p50  101.448  p90  101.541  p99  101.658  max  103.909   mean  101.454
  age_ms      min    1.640  p10    2.467  p50   52.305  p90  102.630  p99  102.701  max  102.955   mean   52.545

A/16-transit-local-lines-2160p-lag3/writer.ndjson  lane B  mxlFlowReaderGetGrain
  calls       17700 ok, 0 failed
  indices     53719496461..53719505313, 17700 read, 0 indexes not read
  stamps      8850 without a valid stamp, 0 stamped with another index
  transit_ms  min   99.756  p10  100.087  p50  100.152  p90  100.214  p99  100.326  max  100.885   mean  100.151
  touch_ms    min    1.103  p10    1.264  p50    1.288  p90    1.358  p99    1.447  max    3.429   mean    1.302
  end_to_end  min  100.996  p10  101.377  p50  101.447  p90  101.540  p99  101.657  max  104.314   mean  101.453
  age_ms      min    1.631  p10    2.466  p50   52.298  p90  102.632  p99  102.702  max  102.954   mean   52.546

A/17-transit-local-copy-2160p-lag3/reader-local.ndjson  lane B  mxlFlowReaderGetGrain
  calls       17700 ok, 0 failed
  indices     53719477296..53719486148, 17700 read, 0 indexes not read
  stamps      8850 without a valid stamp, 0 stamped with another index
  transit_ms  min   99.632  p10  100.121  p50  100.147  p90  100.173  p99  100.279  max  100.780   mean  100.147
  touch_ms    min    1.497  p10    1.609  p50    1.626  p90    1.639  p99    1.655  max   11.746   mean    1.629
  end_to_end  min  101.239  p10  101.742  p50  101.774  p90  101.802  p99  101.911  max  111.918   mean  101.776
  age_ms      min    1.754  p10    2.463  p50   52.455  p90  102.599  p99  102.613  max  103.146   mean   52.526

A/17-transit-local-copy-2160p-lag3/reader.ndjson  lane B  mxlFlowReaderGetGrain
  calls       17700 ok, 0 failed
  indices     53719477297..53719486149, 17700 read, 0 indexes not read
  stamps      8850 without a valid stamp, 0 stamped with another index
  transit_ms  min   99.638  p10  100.122  p50  100.149  p90  100.174  p99  100.279  max  100.779   mean  100.148
  touch_ms    min    1.539  p10    1.623  p50    1.690  p90    1.698  p99    1.705  max   11.321   mean    1.681
  end_to_end  min  101.239  p10  101.764  p50  101.833  p90  101.861  p99  101.966  max  111.510   mean  101.829
  age_ms      min    1.762  p10    2.465  p50   52.458  p90  102.601  p99  102.614  max  103.147   mean   52.528

A/17-transit-local-copy-2160p-lag3/writer.ndjson  lane B  mxlFlowReaderGetGrain
  calls       17700 ok, 0 failed
  indices     53719477296..53719486148, 17700 read, 0 indexes not read
  stamps      8850 without a valid stamp, 0 stamped with another index
  transit_ms  min   99.636  p10  100.125  p50  100.152  p90  100.178  p99  100.283  max  100.778   mean  100.151
  touch_ms    min    1.529  p10    1.603  p50    1.622  p90    1.636  p99    1.652  max   11.746   mean    1.624
  end_to_end  min  101.239  p10  101.742  p50  101.774  p90  101.802  p99  101.906  max  111.917   mean  101.775
  age_ms      min    1.751  p10    2.464  p50   52.460  p90  102.604  p99  102.617  max  103.185   mean   52.529

A/13-transit-local-copy-lag3/reader-local.ndjson  lane B  mxlFlowReaderGetGrain
  calls       17700 ok, 0 failed
  indices     53719465260..53719474112, 17700 read, 0 indexes not read
  stamps      8850 without a valid stamp, 0 stamped with another index
  transit_ms  min   99.691  p10  100.119  p50  100.143  p90  100.168  p99  100.211  max  100.653   mean  100.143
  touch_ms    min    0.405  p10    0.469  p50    0.484  p90    0.696  p99    0.720  max    2.891   mean    0.510
  end_to_end  min  100.175  p10  100.597  p50  100.628  p90  100.831  p99  100.887  max  103.051   mean  100.653
  age_ms      min    0.318  p10    0.854  p50   50.905  p90  100.986  p99  100.999  max  101.477   mean   50.918

A/13-transit-local-copy-lag3/reader.ndjson  lane B  mxlFlowReaderGetGrain
  calls       17700 ok, 0 failed
  indices     53719465260..53719474112, 17700 read, 0 indexes not read
  stamps      8850 without a valid stamp, 0 stamped with another index
  transit_ms  min   99.692  p10  100.120  p50  100.144  p90  100.170  p99  100.213  max  100.656   mean  100.145
  touch_ms    min    0.491  p10    0.522  p50    0.531  p90    0.559  p99    0.584  max    3.193   mean    0.535
  end_to_end  min  100.213  p10  100.650  p50  100.678  p90  100.710  p99  100.766  max  103.355   mean  100.680
  age_ms      min    0.326  p10    0.856  p50   50.907  p90  100.988  p99  101.000  max  101.479   mean   50.919

A/13-transit-local-copy-lag3/writer.ndjson  lane B  mxlFlowReaderGetGrain
  calls       17700 ok, 0 failed
  indices     53719465260..53719474112, 17700 read, 0 indexes not read
  stamps      8850 without a valid stamp, 0 stamped with another index
  transit_ms  min   99.696  p10  100.122  p50  100.147  p90  100.172  p99  100.214  max  100.707   mean  100.147
  touch_ms    min    0.404  p10    0.463  p50    0.481  p90    0.695  p99    0.719  max    3.352   mean    0.506
  end_to_end  min  100.175  p10  100.597  p50  100.628  p90  100.831  p99  100.886  max  103.512   mean  100.653
  age_ms      min    0.322  p10    0.854  p50   50.909  p90  100.990  p99  101.003  max  101.538   mean   50.919

```

</details>

## Appendix B: where the data is

Raw NDJSON, scenario bodies, `/state` snapshots and `numa_maps` captures are on the lab hosts:
- .134, first run: `~/numa-runs/<scenario>/`
- .134, three-reader runs under `schedutil`: `~/numa-runs/exp/<layout>/<scenario>/`
- .134, three-reader runs under `performance`: `~/numa-runs/exp-perf/<layout>/<scenario>/`
- .134, interrupted 2160p run: `~/numa-runs/exp-2160p/`
- .136, 1080p head-3: `~/numa-runs/exp-136/A/13-transit-local-copy-lag3/`
- .136, 2160p runs: `~/numa-runs/exp-136-2160p/A/<scenario>/`
- .136, `perf c2c`: `~/numa-runs/c2c/<scenario>/` (`c2c-stats.txt`, `c2c-report.txt`, `perf-c2c.data`)
- .136, offcore counters: `~/numa-runs/ocr/<scenario>/ocr-{writer,reader-local,reader}.csv`, with the NDJSON of each counted run
- .136, single-reader runs: `~/numa-runs/single/{local,cross}-<scenario>/` (counters, NDJSON, `/state`, `numa_maps`)
- .136, directory and snoop counters: `~/numa-runs/dir/` and `~/numa-runs/dir2/`, `{local,cross}-<scenario>/dir.csv`
- .136, N readers on one flow: `~/numa-runs/multi/{local,cross}-x<N>/` (`dir.csv`, NDJSON, scenario bodies)
- .136, several flows and stress: `~/numa-runs/flows/{none,vm}-f<F>-{local,cross}/` (same files)
- .136, heavy stress: `~/numa-runs/stress/stream-f<F>-{local,cross}/` (same files)

The override and driver scripts are in `~/numa-runs/` on each host. The `*-record.sh` scripts need sudo.
