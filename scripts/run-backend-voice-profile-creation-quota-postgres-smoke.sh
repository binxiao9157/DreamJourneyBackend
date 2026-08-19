#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

cd "$ROOT_DIR"
PYTHONPATH=. "$PYTHON_BIN" scripts/backend-voice-profile-creation-quota-postgres-smoke.py
