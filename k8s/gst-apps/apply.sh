#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
# SPDX-License-Identifier: Apache-2.0
#
# Substitute the MetalLB addresses from .env into the manifests and apply them.
#
#   ./k8s/gst-apps/apply.sh                     # apply
#   ./k8s/gst-apps/apply.sh --dry-run=server    # validate only
#
# Any arguments are passed through to `kubectl apply`.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -f "$here/.env" ]]; then
  echo "missing $here/.env — copy .env.template and fill in the addresses" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
source "$here/.env"
set +a

: "${MXL_LB_IP_APPS:?not set in .env}"
: "${MXL_LB_IP_MEDIAMTX:?not set in .env}"

for f in "$here"/*.yaml; do
  envsubst '${MXL_LB_IP_APPS} ${MXL_LB_IP_MEDIAMTX}' <"$f"
  # leading newline: several manifests have no trailing newline of their own
  printf '\n---\n'
done | kubectl apply -f - "$@"
