#!/usr/bin/env bash
set -euo pipefail

# R5 non-device Echo binding gate. It uses in-memory/fake Providers only and
# never consumes a real voice slot or sends audio outside the test process.
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_voice_synthesis_role_binding \
  tests.test_voice_generated_audio_binding_shadow \
  tests.test_voice_synthesis_family_scope \
  tests.test_voice_profile_lifecycle

PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_core_services.TokenAndProxyTests.test_voice_clone_synthesis_can_return_tencent_audio_drive_pcm_contract

PYTHONPATH=. "$PYTHON_BIN" -m py_compile app/main.py
bash -n scripts/run-backend-voice-clone-r5-echo-binding-gate.sh

echo "Voice clone R5 Echo binding gate passed"
