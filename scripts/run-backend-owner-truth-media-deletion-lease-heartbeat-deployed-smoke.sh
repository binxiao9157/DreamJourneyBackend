#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

: "${BACKEND_BASE_URL:=${DREAMJOURNEY_BACKEND_BASE_URL:-}}"
: "${DREAMJOURNEY_DEPLOYED_CONTAINER_SMOKE:=}"

[[ -n "$BACKEND_BASE_URL" ]] || { echo "BACKEND_BASE_URL is required" >&2; exit 1; }
[[ "$DREAMJOURNEY_DEPLOYED_CONTAINER_SMOKE" == "1" ]] || {
  echo "DREAMJOURNEY_DEPLOYED_CONTAINER_SMOKE=1 is required" >&2
  exit 1
}

cd "$ROOT_DIR"
BACKEND_BASE_URL="$BACKEND_BASE_URL" \
  scripts/run-backend-readiness-deployed-smoke.sh
scripts/run-backend-owner-truth-media-deletion-lease-heartbeat-postgres-smoke.sh
