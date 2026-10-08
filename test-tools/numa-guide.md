# NUMA isolation and cross-NUMA read tests for abi-tester

Two tests on a Linux Docker host, modelled on the Kubernetes approach in this repo:

1. **Process isolation:** pin one `abi-tester` container (CPUs and memory) to a NUMA node and
   show a noisy neighbour on the other node does not affect it.
2. **Cross-NUMA read:** run a writer on node 0 and a reader on node 1 sharing one MXL domain,
   and measure the cost of reading across the socket interconnect.

Background: [docs/03-cpu-qos.md](../docs/03-cpu-qos.md) (CPU Manager static policy and Topology
Manager `single-numa-node`), [docs/11-noisy-neighbor.md](../docs/11-noisy-neighbor.md) (noisy
neighbour method), [docs/12-rdt-qos.md](../docs/12-rdt-qos.md) (Intel RDT cache/bandwidth
control), and [Intel PCM](https://github.com/intel/pcm) for the memory and UPI counters used below.

## How the k8s mechanism maps to Docker

| Kubernetes | Docker |
|---|---|
| `cpuManagerPolicy: static`, Guaranteed Pod with integer CPUs | `cpuset:` on the service (`--cpuset-cpus`) |
| `topologyManagerPolicy: single-numa-node` | CPUs from one node + `--cpuset-mems` (`docker update`, below) |
| Kubelet keeps other pods off the exclusive CPUs | Not done by Docker: use `isolcpus` or disjoint cpusets for everything else |
| Admission rejects unaligned Pods | No check: verify placement yourself |

Compose has no `cpuset_mems` key (the schema rejects it), so memory binding is done with
`docker update --cpuset-mems <node>` followed by `docker restart`. Do not write the cgroup's
`cpuset.mems` by hand: Docker recreates the cgroup scope on restart and the write is lost. The
setting survives `restart` but not `down`/`up` or `--force-recreate`; rerun it after those.

## Prerequisites

- Linux host (not Docker Desktop on macOS), cgroup v2, 2+ NUMA nodes, `numactl`, `numastat`, `perf`.
- `pcm`: `sudo apt install pcm && sudo modprobe msr`, or build from the Intel PCM repo.

Lab host topology (`numactl --hardware`): 2 nodes, 24 CPUs each, distance 10 local / 21 remote.
CPU numbers are interleaved, so ranges like `0-23` span both nodes; always use explicit lists:

```bash
N0=0,2,4,6,8,10,12,14,16,18,20,22,24,26,28,30,32,34,36,38,40,42,44,46
N1=1,3,5,7,9,11,13,15,17,19,21,23,25,27,29,31,33,35,37,39,41,43,45,47
```

Other hosts: check `numactl --hardware` and `lscpu -e=CPU,NODE`, and the NIC's node with
`cat /sys/class/net/<nic>/device/numa_node`.

**Reserve the CPUs.** Pinning does not stop other work from using them. Either boot with
`isolcpus=$N0 nohz_full=$N0` (closest to a k8s exclusive cpuset, verify with
`cat /sys/devices/system/cpu/isolated`), or give every other container a cpuset on the other
node and move host services off with `systemctl set-property --runtime system.slice AllowedCPUs=$N1`
(same for `user.slice`). Record which you used.

## Verify placement (used by both tests)

```bash
PID=$(docker inspect -f '{{.State.Pid}}' <container>)
taskset -cp $PID
grep -E 'Cpus_allowed_list|Mems_allowed_list' /proc/$PID/status   # Mems must be one node, not 0-1
```

To see where the MXL domain pages sit (`numastat -p` hides mapped files and `numastat -m` Shmem is host-wide):

```bash
df -T /Volumes/mxl/domain_1                       # expect tmpfs; other types use the page cache
sudo grep mxl-domain /proc/$PID/numa_maps         # grain mappings should end in N0=<pages> only
```

If a domain mapping shows `N1=` pages, the wrong process touched them first: tear down, clear the
domain (keep `domain_def.json`), and start the writer first.

## Test 1: process isolation of one container

Override file [docker-compose.numa.yml](docker-compose.numa.yml); it sets
the node 0 CPU list, so edit it for other hosts:

```yaml
services:
  abi-tester:
    cpuset: "0,2,4,6,8,10,12,14,16,18,20,22,24,26,28,30,32,34,36,38,40,42,44,46"  # node 0
```

```bash
export MXL_DOMAIN_DEVICE=/Volumes/mxl/domain_1      # tmpfs; must contain domain_def.json first
docker compose -f docker-compose-published.yml -f docker-compose.numa.yml up -d
docker update --cpuset-mems 0 abi-tester && docker restart abi-tester
```

Verify placement as above (expect node 0 CPUs and `Mems_allowed_list: 0`).

Run your scenario on port 9607 and keep `logs/events.ndjson` for each run:

| Run | Pinned | Noise | Expectation |
|---|---|---|---|
| A | no (plain published file) | none | reference |
| B | yes, node 0 | none | pinned baseline |
| C | yes, node 0 | node 1 | close to B |
| D | yes, node 0 | node 0 (negative control) | clear degradation |

```bash
# C: noise on the other node
docker run --rm --name noisy --cpuset-cpus $N1 --cpuset-mems 1 \
  polinux/stress-ng --cpu 16 --vm 8 --vm-bytes 4G --timeout 300s
# D: same, with --cpuset-cpus $N0 --cpuset-mems 0
```

If D shows no degradation, the load is too light. Compare p50/p99/max from `events.ndjson`,
and `perf stat -e cpu-migrations -p $PID`.

## Test 2: cross-NUMA read (writer node 0, reader node 1)

The writer instance comes with its own reader on node 0, which is the local baseline; the
separate reader on node 1 is the cross-node case. The domain lives on tmpfs, so pages are
allocated on the node of the process that first touches them: the writer, on node 0. The
reader on node 1 then reads them over UPI.

[docker-compose.numa-writer-reader.yml](docker-compose.numa-writer-reader.yml) pins the writer
and reader (edit its `cpuset` lists for other hosts):

```bash
export MXL_DOMAIN_DEVICE=/Volumes/mxl/domain_1      # tmpfs; must contain domain_def.json
mkdir -p ABI-tester/logs/writer ABI-tester/logs/reader
docker compose -f docker-compose.numa-writer-reader.yml up -d
docker update --cpuset-mems 0 abi-writer
docker update --cpuset-mems 1 abi-reader
docker restart abi-writer abi-reader
```

Verify both containers (writer `Mems_allowed_list: 0`, reader `1`) and the domain placement above.
Writer UI/API is on port 9607, reader on 9608.

Run, for at least 5 minutes and at least 3 times:

1. Start the writer scenario and let it reach steady state.
2. Start the reader scenario on the same flow(s). Scenario names come from `ABI-tester/scenarios`
   (not in this repo); use the pair from your transit/remote-reader runs.
3. Collect:

```bash
sudo pcm-memory 1 -csv=pcm-memory.csv     # per-node DRAM throughput
sudo pcm 1 -csv=pcm-upi.csv               # UPI link utilisation
```

Compare the writer's own reader (`ABI-tester/logs/writer/events.ndjson`, node 0) with the separate
reader (`ABI-tester/logs/reader/events.ndjson`, node 1): p50/p99/max, plus UPI traffic. Expect the
node 1 reader to be slower and more variable, with UPI traffic near the grain rate and none for
the local reader. If the transit metric covers only metadata and not the payload, the difference
will be about a microsecond (a few remote cache misses); use a scenario that reads the full grain
to see the bandwidth-bound cost.

### Under memory load (stress-ng on node 0)

Saturate node 0 memory bandwidth and repeat: the node 1 reader competes for the memory controller
and UPI, the local reader only for the controller, so the node 1 reader should degrade more. The
stress needs node 0 CPUs the writer does not use, so use the same CPU counts in both runs:

| Role | CPUs | Memory node |
|---|---|---|
| Writer | `0,2,4,6,8,10,12,14` | 0 |
| stress-ng | `32,34,36,38,40,42,44,46` | 0 |
| Reader | `1,3,5,7,9,11,13,15` | 1 |

Set these as the `cpuset` of `abi-writer` and `abi-reader`. Run A (no stress) and B (stress). For B,
start the stress after the writer is running and before the reader scenario:

```bash
docker run -d --rm --name noisy --cpuset-cpus 32,34,36,38,40,42,44,46 --cpuset-mems 0 \
  polinux/stress-ng --vm 8 --vm-bytes 4G --vm-keep --timeout 900s
sudo pcm-memory 1     # NODE 0 should be tens of GB/s (about 63 GB/s on the lab host), NODE 1 near zero
```

If node 0 throughput is low, use `--stream 8` instead of `--vm 8`. Copy the logs to a per-run folder
before the next run wipes the domain, and compare A against B for both readers and the writer.

## Clean up

```bash
docker rm -f noisy 2>/dev/null
docker compose -f docker-compose.numa-writer-reader.yml down
docker compose -f docker-compose-published.yml -f docker-compose.numa.yml down
```

Remove the domain directory, and undo any `AllowedCPUs` or `isolcpus` changes.

