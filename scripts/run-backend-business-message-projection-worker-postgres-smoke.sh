#!/usr/bin/env bash
set -euo pipefail

# Disposable P0-S2 runtime verification. It creates a temporary database,
# never writes the public mailbox, and leaves the real worker profile stopped.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

: "${DATABASE_URL:?DATABASE_URL is required}"
cd "$ROOT_DIR"
PYTHONPATH=. "$PYTHON_BIN" scripts/backend-business-message-projection-worker-postgres-smoke.py
