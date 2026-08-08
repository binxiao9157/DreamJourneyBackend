#!/usr/bin/env bash
set -euo pipefail

# R5 non-device gate. All Provider behavior is fake or configuration-only;
# this never uploads a real sample, consumes a slot, or contacts VolcEngine.
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_voice_clone_operation_capability_matrix \
  tests.test_voice_profile_lifecycle \
  tests.test_voice_clone_c1_deletion_worker \
  tests.test_voice_synthesis_role_binding \
  tests.test_runtime_capabilities

PYTHONPATH=. "$PYTHON_BIN" -m py_compile \
  app/services/voice_clone_operation_capabilities.py \
  app/services/voice_profile_lifecycle.py \
  app/services/voice_clone.py \
  app/services/runtime_config.py \
  app/async_effects/voice_profile_deletion_worker.py \
  scripts/backend-voice-clone-c0-runtime-smoke.py

PYTHONPATH=. "$PYTHON_BIN" - <<'PY'
import json

from app.core.config import Settings
from app.services.runtime_config import RuntimeConfigService

runtime = RuntimeConfigService(
    Settings(
        volcengine_voice_clone_api_key="fixture-training-key",
        volcengine_voice_clone_tts_api_key="fixture-synthesis-key",
    )
).public_config()["voiceClone"]
operations = runtime["operationMatrix"]["operations"]
assert operations["train"]["available"] is False
assert operations["train"]["reasonCode"] == "identityLivenessProviderUnavailable"
assert operations["query"]["available"] is True
assert operations["preview"]["available"] is True
assert operations["accept"]["providerCapability"] == "notRequired"
assert operations["synthesize"]["available"] is True
assert operations["pause"]["available"] is True
assert operations["delete"]["available"] is True
assert operations["delete"]["providerCapability"] == "unsupported"
assert operations["delete"]["providerCompletionAvailable"] is False

serialized = json.dumps(runtime, sort_keys=True).lower()
for forbidden in ("api_key", "secret", "speaker_id", "receiptid"):
    assert forbidden not in serialized, forbidden
print("Voice clone R5 Provider boundary gate passed")
PY

bash -n scripts/run-backend-voice-clone-r5-provider-boundary-gate.sh
bash -n scripts/run-backend-voice-clone-c0-deployed-smoke.sh
