#!/usr/bin/env bash
set -euo pipefail

# G0-only. No synthetic Memorial evidence may enable Voice, Portrait, Digital
# Human, Publication, a Provider call or a fallback to a relative's voice.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
"$PYTHON_BIN" -m unittest \
  tests.test_owner_truth_memorial_capability_non_admission_shadow
"$PYTHON_BIN" -m py_compile \
  app/services/owner_truth_memorial_capability_non_admission_shadow.py

"$PYTHON_BIN" - <<'PY'
import ast
from pathlib import Path

from app.services.owner_truth_memorial_capability_non_admission_shadow import (
    MemorialCapabilityPurpose,
)

assert {item.value for item in MemorialCapabilityPurpose} == {
    "voice_training",
    "voice_synthesis_private",
    "portrait_rendering",
    "digital_human_private",
    "publication_text",
    "publication_voice",
    "publication_digital_human",
}

path = Path("app/services/owner_truth_memorial_capability_non_admission_shadow.py")
source = path.read_text(encoding="utf-8")
tree = ast.parse(source)
for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        module = getattr(node, "module", "") or ""
        assert not module.startswith("app.main"), "Memorial capability gate must not import API routes"
        assert not module.startswith("app.async_effects"), "Memorial capability gate must not import effects"
        assert not module.startswith("app.services.postgres_store"), "Memorial capability gate must not import persistence"
        assert not module.startswith("app.services.in_memory_store"), "Memorial capability gate must not import persistence"

for forbidden in ("subprocess", "requests", "httpx", "psycopg", "sqlalchemy"):
    assert forbidden not in source, f"Memorial capability gate must not depend on {forbidden}"

for required in (
    '"capabilityAdmitted": self.capability_admitted',
    '"capabilityDecisionWritten": self.capability_decision_written',
    '"providerEffectAllowed": self.provider_effect_allowed',
    '"fallbackToFamilyVoiceAllowed": self.fallback_to_family_voice_allowed',
    '"defaultSystemVoiceMayBeDescribedAsDeceased":',
    '"deceasedIntentEvidenceMustCoverExactPurpose"',
    '"noCurrentLivingSelfVoiceOrDigitalHumanRuntimeMayBeReused"',
    '"syntheticClaimsCannotAuthorizeMemorialCapability"',
):
    assert required in source, f"missing Memorial capability G0 invariant: {required}"

print("owner truth memorial capability non-admission static gate passed")
PY
