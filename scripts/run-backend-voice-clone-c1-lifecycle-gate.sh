#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

# This gate uses only fake provider observations. It proves lifecycle and
# binding behavior without consuming a real voice slot, contacting a provider,
# or requiring a simulator/device.
PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_voice_clone_c1_deletion_worker \
  tests.test_voice_profile_lifecycle \
  tests.test_voice_synthesis_role_binding

"$PYTHON_BIN" -m compileall -q \
  app/async_effects/voice_profile_deletion_worker.py \
  app/services/voice_clone.py \
  app/services/voice_profile_deletion_effects.py

"$PYTHON_BIN" - <<'PY'
from pathlib import Path

worker = Path("app/async_effects/voice_profile_deletion_worker.py").read_text(encoding="utf-8")
provider = Path("app/services/voice_clone.py").read_text(encoding="utf-8")
effects = Path("app/services/voice_profile_deletion_effects.py").read_text(encoding="utf-8")
config = Path("app/core/config.py").read_text(encoding="utf-8")

for source, needle in (
    (worker, "voice_clone_deletion_worker_enabled"),
    (worker, "Persist uncertainty before any provider call"),
    (worker, "voiceProfileDeletionManualReview"),
    (worker, "VoiceCloneProfileDeletionDisposition.UNSUPPORTED"),
    (provider, "class VoiceCloneProfileDeletionObservation"),
    (provider, "return VoiceCloneProfileDeletionObservation.unsupported"),
    (effects, "build_voice_profile_deletion_provider_effect_intent"),
    (config, "VOICE_CLONE_DELETION_WORKER_ENABLED"),
):
    assert needle in source, needle

# A configured train/query API must not guess an upstream delete endpoint.
assert "urllib.request.urlopen" not in worker
print("Voice clone C1 lifecycle fail-closed boundary passed")
PY
