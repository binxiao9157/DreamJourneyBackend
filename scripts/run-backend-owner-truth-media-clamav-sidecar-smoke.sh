#!/usr/bin/env bash
set -euo pipefail

# The ClamAV sidecar smoke is explicit because it requires the optional Docker
# profile and scans the standard EICAR test string. It never uploads to COS or
# reads user media.
if [[ "${RUN_BACKEND_OWNER_TRUTH_MEDIA_CLAMAV_SIDECAR_SMOKE:-0}" != "1" ]]; then
  echo "Owner Truth ClamAV sidecar smoke skipped (set RUN_BACKEND_OWNER_TRUTH_MEDIA_CLAMAV_SIDECAR_SMOKE=1)"
  exit 0
fi

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
PYTHONPATH=. "$PYTHON_BIN" scripts/backend-owner-truth-media-clamav-sidecar-smoke.py
