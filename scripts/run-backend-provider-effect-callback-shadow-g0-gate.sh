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

PYTHONPATH=. "$PYTHON_BIN" -m unittest tests.test_provider_effect_callback_shadow
"$PYTHON_BIN" -m py_compile app/async_effects/provider_effect_callback_shadow.py

"$PYTHON_BIN" - <<'PY'
from pathlib import Path

source = Path("app/async_effects/provider_effect_callback_shadow.py").read_text(encoding="utf-8")
for forbidden in (
    "FastAPI",
    "requests",
    "httpx",
    "urllib.request",
    "hmac",
    "psycopg",
    "sqlite3",
    "ProviderEffectReconciliation",
):
    assert forbidden not in source, forbidden
print("Provider-effect callback reconciliation shadow G0 static boundary passed")
PY
