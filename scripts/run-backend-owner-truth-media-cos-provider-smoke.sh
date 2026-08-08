#!/usr/bin/env bash
set -euo pipefail

# The real COS smoke intentionally remains opt-in. It creates one value-free
# probe object and immediately deletes it, so it must run only after the
# server's private COS credentials and bucket policy are installed.
if [[ "${RUN_BACKEND_OWNER_TRUTH_MEDIA_COS_PROVIDER_SMOKE:-0}" != "1" ]]; then
  echo "Owner Truth COS provider smoke skipped (set RUN_BACKEND_OWNER_TRUTH_MEDIA_COS_PROVIDER_SMOKE=1)"
  exit 0
fi

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
PYTHONPATH=. "$PYTHON_BIN" scripts/backend-owner-truth-media-cos-provider-smoke.py
