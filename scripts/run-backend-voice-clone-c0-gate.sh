#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_voice_clone_c0_admission_gate \
  tests.test_voice_profile_lifecycle \
  tests.test_voice_sample_contract \
  tests.test_voice_synthesis_role_binding \
  tests.test_runtime_capabilities

"$PYTHON_BIN" - <<'PY'
from pathlib import Path

main = Path("app/main.py").read_text(encoding="utf-8")
lifecycle = Path("app/services/voice_profile_lifecycle.py").read_text(encoding="utf-8")
identity = Path("app/services/voice_identity_eligibility.py").read_text(encoding="utf-8")
runtime = Path("app/services/runtime_config.py").read_text(encoding="utf-8")

for source, needle in (
    (main, "make_voice_identity_eligibility_provider(settings)"),
    (main, "voice_identity_verification_unavailable"),
    (main, "VOICE_CLONE_TRAINING_CONSENT_VERSION"),
    (lifecycle, "_TRUSTED_ELIGIBILITY_PROVENANCES = frozenset({\"serverVerified\"})"),
    (identity, "class HttpJsonVoiceIdentityEligibilityProvider"),
    (identity, "class UnavailableVoiceIdentityEligibilityProvider"),
    (runtime, "trainingAdmissionEnabled"),
):
    assert needle in source, needle
assert "syntheticTest\"})" not in lifecycle
print("Voice clone C0 static fail-closed boundary passed")
PY
