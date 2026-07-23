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

if [[ -f tests/test_voice_training_preflight_shadow.py ]]; then
  PYTHONPATH=. "$PYTHON_BIN" -m unittest tests.test_voice_training_preflight_shadow
else
  PYTHONPATH=. "$PYTHON_BIN" scripts/backend-voice-training-preflight-runtime-smoke.py
fi

"$PYTHON_BIN" - <<'PY'
from pathlib import Path

source = Path("app/services/voice_training_preflight_shadow.py").read_text(encoding="utf-8")
for forbidden in (
    "app.services.voice_clone",
    "requests",
    "httpx",
    "boto3",
    "urllib.request",
):
    assert forbidden not in source, forbidden
print("Voice training G0 preflight static boundary passed")
PY
