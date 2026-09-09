#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

exec "${PYTHON_BIN:-python3}" \
  scripts/backend-owner-truth-dfx-postgres-load.py "$@"
