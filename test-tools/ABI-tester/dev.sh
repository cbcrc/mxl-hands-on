# SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
# SPDX-License-Identifier: Apache-2.0
#
# Hand-driving helpers for the ABI-tester. Source it, do not execute it:
#     source ~/mxl-hands-on/test-tools/ABI-tester/dev.sh
# A script runs in a subshell, so an executed copy would define these functions
# and then throw them away.

ABI_DIR="$HOME/mxl-hands-on/test-tools/ABI-tester"
ABI_DOMAIN="/Volumes/mxl/domain_1"
ABI_FLOW="a1b2c3d4-0001-4000-8000-000000000001"
B="http://localhost:9600"

# One ad-hoc ABI call. Body plus the status code, because a silent curl has had
# four distinct causes in this project and none of them printed anything.
call()  { curl -sS -X POST $B/call -H 'Content-Type: application/json' -d "$1" -w ' [%{http_code}]\n'; }

# Same, but projects a jq path out of the body: callj '{...}' '.fill'
callj() { curl -sS -X POST $B/call -H 'Content-Type: application/json' -d "$1" \
            -o /tmp/abi-r.json -w '[%{http_code}] '; jq -c "${2:-.}" /tmp/abi-r.json; }

# POST a file: postf /scenario /tmp/foo.json
postf() { curl -sS -X POST $B$1 -H 'Content-Type: application/json' -d @"$2" -w ' [%{http_code}]\n'; }

# POST with no body. The -d '' is not optional: without it curl sends no
# Content-Length, cpp-httplib reads until EOF, and the call costs exactly 5 s.
postn() { curl -sS -X POST $B$1 -d '' -w ' [%{http_code}]\n'; }

# The registry lives in the server process, so every restart starts from zero.
setup() {
  call "{\"call\":\"mxlCreateInstance\",\"args\":{\"domain\":\"$ABI_DOMAIN\",\"store_as\":\"I\"}}" > /dev/null
  local def=$(jq -Rs . "$ABI_DIR/flows/video-1080p2997-v210.json")
  call "{\"call\":\"mxlCreateFlowWriter\",\"args\":{\"instance\":\"I\",\"flow_def\":$def,\"store_as\":\"W\"}}" > /dev/null
  call "{\"call\":\"mxlCreateFlowReader\",\"args\":{\"instance\":\"I\",\"flow_id\":\"$ABI_FLOW\",\"store_as\":\"R\"}}" > /dev/null
  curl -sS $B/state | jq -c '.handles | keys'
}

# The current index at the video rate, for $(idx)-style capture in one line.
idx() { curl -sS -X POST $B/call -H 'Content-Type: application/json' \
          -d '{"call":"mxlGetCurrentIndex","args":{"edit_rate":{"num":30000,"den":1001}}}' | jq .index; }

# The whole log, one line per event.
logs() { curl -sS "$B/log?since=${1:-0}" | jq -c '.[]'; }

# Average CPU of the server over N seconds, from /proc. Fields 14+15 of
# /proc/<pid>/stat are utime+stime in clock ticks.
cpu() {
  local P=$(pgrep abi-tester) T=${1:-10}
  local A=$(awk '{print $14+$15}' /proc/$P/stat)
  sleep "$T"
  local B=$(awk '{print $14+$15}' /proc/$P/stat)
  echo "cpu% = $(echo "($B-$A)*100/($T*$(getconf CLK_TCK))" | bc -l)"
}