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

PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_voice_dh_authority \
  tests.test_voice_dh_authority_migration_contract \
  tests.test_voice_dh_blocked_sample_intent_migration_contract

"$PYTHON_BIN" - <<'PY'
from pathlib import Path

source = Path("app/services/voice_dh_authority.py").read_text(encoding="utf-8")
for forbidden in (
    "requests",
    "httpx",
    "boto3",
    "urllib.request",
    "audio_base64",
    "object_url",
    "provider_speaker_id",
):
    assert forbidden not in source, forbidden
print("Voice/DH blocked sample intent G0 static boundary passed")
PY

if [[ "${RUN_VOICE_DH_BLOCKED_SAMPLE_INTENT_POSTGRES_SMOKE:-0}" == "1" ]]; then
  PYTHONPATH=. "$PYTHON_BIN" scripts/backend-voice-dh-blocked-sample-intent-postgres-smoke.py
fi
