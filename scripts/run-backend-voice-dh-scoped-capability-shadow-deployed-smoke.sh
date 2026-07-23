#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
"$PYTHON_BIN" scripts/backend-voice-dh-scoped-capability-shadow-deployed-smoke.py
