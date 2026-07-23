#!/usr/bin/env bash
set -euo pipefail

if [[ ! -f "/.dockerenv" ]]; then
  echo "provider-effect callback shadow deployed smoke must run inside the deployed API container" >&2
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"
python3 scripts/backend-provider-effect-callback-shadow-deployed-smoke.py
