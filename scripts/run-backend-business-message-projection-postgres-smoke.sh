#!/usr/bin/env bash
set -euo pipefail

# Disposable G2 verification only. The smoke never writes mailbox_letters,
# starts a worker, dispatches a notification, or calls a Provider.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

: "${DATABASE_URL:?DATABASE_URL is required}"
cd "$ROOT_DIR"
PYTHONPATH=. "$PYTHON_BIN" scripts/backend-business-message-projection-postgres-smoke.py
