#!/usr/bin/env bash
# usage: ./sweep.sh "1 8 32 96 256" [times]
# One server process per point: ABI_TESTER_LANES is read once in the Engine ctor.
set -uo pipefail
cd ~/mxl-hands-on/test-tools/ABI-tester
B=http://localhost:9600
TIMES=${2:-599}
SRV=""
cleanup() { [ -n "$SRV" ] && kill "$SRV" 2>/dev/null; }
trap 'cleanup; echo interrupted; exit 130' INT TERM
trap cleanup EXIT
# cpp-httplib sets SO_REUSEPORT (httplib.h:1860), so a stale server does NOT fail
# to bind -- the kernel round-robins connections between every listener.
if curl -sS -m 1 $B/state >/dev/null 2>&1; then
  echo "FATAL: something already answers on $B -- pkill -f build/abi-tester"; exit 1
fi
ulimit -n 65536 || echo "WARNING: could not raise fd limit; >145 flows will fail with EMFILE"
rm -f /tmp/abi-load-*.ndjson /tm/abi-srv-*.log /tmp/load-*.json
for N in ${1:-1 8 32 96 256}; do
  echo "=== $N lanes, $((TIMES + 1)) grains/lane ==="
  ./gen-load.sh "$N" "$TIMES" > /tmp/load-$N.json || { echo "gen failed"; continue; }
  find /Volumes/mxl/domain_1 -mindepth 1 -maxdepth 1 -type d -exec rm -rf {} +
  ABI_TESTER_LANES=$N ABI_TESTER_LOG_FILE=/tmp/abi-load-$N.ndjson \
    ./backend/build/abi-tester /Volumes/mxl/domain_1 > /tmp/abi-srv-$N.log 2>&1 &
  SRV=$!
  for _ in $(seq 50); do
    curl -sS -m 1 $B/state > /dev/null 2>&1 && break
    kill -0 $SRV 2>/dev/null || { echo "sever died:" cat /tmp/abi-srv-$N.log; break; }
    sleep 0.2
  done
  grep -E "^(Lanes|Domain|Log)" /tmp/abi-srv-$N.log
  POOL=$(curl -sS $B/state | jq -r .lane_pool)
  [ "$POOL" = "$N" ] || { echo "FATAL: lane_pool=$POOL, expected $N"; exit 1;}
  curl -sS -X POST $B/scenario -H 'Content-Type: application/json' -d @/tmp/load-$N.json; echo
  curl -sS -X POST $B/call -H 'Content-Type: application/json' \
       -d '{"call":"mxlCreateInstance","args":{"domain":"/Volumes/mxl/domain_1"},
            "out":{"instance":"I"}}'; echo
  curl -sS -X POST $B/run -d '{}'; echo
  sleep $(( TIMES / 30 + 12))           # grains, then 12 s idle so the flusher drains
  curl -sS $B/state | jq -c '{running, lane_pool, log}'
  jq -s 'group_by(.ok) | map({ok: .[0].ok, n: length})' /tmp/abi-load-$N.ndjson
  df -h /Volumes/mxl | tail -1
  kill $SRV; wait $SRV 2>/dev/null; SRV=""
  ls -l /tmp/abi-load-$N.ndjson
done