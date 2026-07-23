#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
  else
    PYTHON_BIN="python3"
  fi
fi

PYTHONPATH=. "$PYTHON_BIN" -m unittest tests.test_voice_dh_scoped_capability_shadow

"$PYTHON_BIN" - <<'PY'
from pathlib import Path

source = Path("app/services/voice_dh_scoped_capability_shadow.py").read_text(encoding="utf-8")
for forbidden in (
    "requests",
    "httpx",
    "boto3",
    "urllib.request",
    "psycopg",
    "sqlite3",
):
    assert forbidden not in source, forbidden
print("Voice/DH scoped capability shadow G0 static boundary passed")
PY
