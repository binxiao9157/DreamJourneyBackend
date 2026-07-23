#!/usr/bin/env bash
set -euo pipefail

# G0-only.  The Voice/DH purpose-consent module must remain pure, default-off,
# value-free, and unable to invoke providers, persist consent, or expose UI.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
"$PYTHON_BIN" -m unittest tests.test_voice_dh_consent_policy
"$PYTHON_BIN" -m py_compile app/services/voice_dh_consent_policy.py

"$PYTHON_BIN" - <<'PY'
import ast
from pathlib import Path

from app.services.voice_dh_consent_policy import (
    VOICE_DH_PURPOSE_CONSENT_SCHEMA_VERSION,
    VoiceDHPurpose,
)

assert VOICE_DH_PURPOSE_CONSENT_SCHEMA_VERSION == "voice-dh-purpose-consent-v1"
assert {item.value for item in VoiceDHPurpose} == {
    "training",
    "preview",
    "private_synthesis",
    "memoir",
    "dh_audio_drive",
    "visitor_public_voice",
}

path = Path("app/services/voice_dh_consent_policy.py")
source = path.read_text(encoding="utf-8")
tree = ast.parse(source)

for node in ast.walk(tree):
    imported_modules = []
    if isinstance(node, ast.Import):
        imported_modules.extend(alias.name for alias in node.names)
    elif isinstance(node, ast.ImportFrom):
        base = node.module or ""
        imported_modules.append(base)
        imported_modules.extend(
            f"{base}.{alias.name}".strip(".")
            for alias in node.names
        )
    for module in imported_modules:
        assert not module.startswith("app.main"), "Voice/DH consent G0 must not import API routes"
        assert not module.startswith("app.async_effects"), "Voice/DH consent G0 must not import effects"
        assert not module.startswith("app.services.postgres_store"), "Voice/DH consent G0 must not import persistence"
        assert not module.startswith("app.services.in_memory_store"), "Voice/DH consent G0 must not import persistence"

for forbidden in ("subprocess", "requests", "httpx", "psycopg", "sqlalchemy"):
    assert forbidden not in source, f"Voice/DH consent G0 must not depend on {forbidden}"

assert "SHADOW_ELIGIBLE" not in source, "G0 must not expose a promotable eligibility state"

for required in (
    '"effectAllowed": self.effectAllowed',
    '"providerEffectAllowed": self.providerEffectAllowed',
    '"releaseVisible": self.releaseVisible',
    '"consentReceiptWritten": self.consentReceiptWritten',
    '"purposeGrantWritten": self.purposeGrantWritten',
    '"legacyConsentPromoted": self.legacyConsentPromoted',
    '"g0SyntheticPreconditionsOnlyNoProviderEffect"',
    '"legacyBooleanConsentNotAuthority"',
    '"visitorPublicVoiceRequiresM2G4Approval"',
    '"consentReceiptNotYetEffective"',
    '"purposeGrantNotYetEffective"',
):
    assert required in source, f"missing Voice/DH consent G0 invariant: {required}"

print("voice dh purpose consent G0 gate passed")
PY
