#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

cd "$ROOT_DIR"
STORE_BACKEND=memory PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_voice_profile_creation_quota \
  tests.test_voice_profile_lifecycle
