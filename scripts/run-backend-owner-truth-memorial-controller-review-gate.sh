#!/usr/bin/env bash
set -euo pipefail

# G0-only. Controller review is a fail-closed state assessment, never a Vault
# state transition, appointment activation, publication or Provider effect.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
"$PYTHON_BIN" -m unittest \
  tests.test_owner_truth_memorial_controller_review_shadow
"$PYTHON_BIN" -m py_compile \
  app/services/owner_truth_memorial_controller_review_shadow.py

"$PYTHON_BIN" - <<'PY'
import ast
from pathlib import Path

from app.services.owner_truth_memorial_controller_review_shadow import (
    MemorialControllerReviewCondition,
)

assert set(MemorialControllerReviewCondition) == {
    MemorialControllerReviewCondition.ACTIVE,
    MemorialControllerReviewCondition.CONTROLLER_UNREACHABLE,
    MemorialControllerReviewCondition.CONTROLLER_DECEASED,
    MemorialControllerReviewCondition.CONTROLLER_REVOKED,
    MemorialControllerReviewCondition.ACCOUNT_RECOVERY_FAILED,
    MemorialControllerReviewCondition.UNKNOWN,
}

path = Path("app/services/owner_truth_memorial_controller_review_shadow.py")
source = path.read_text(encoding="utf-8")
tree = ast.parse(source)
for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        module = getattr(node, "module", "") or ""
        assert not module.startswith("app.main"), "Controller review must not import API routes"
        assert not module.startswith("app.async_effects"), "Controller review must not import effects"
        assert not module.startswith("app.services.postgres_store"), "Controller review must not import persistence"
        assert not module.startswith("app.services.in_memory_store"), "Controller review must not import persistence"

for forbidden in ("subprocess", "requests", "httpx", "psycopg", "sqlalchemy"):
    assert forbidden not in source, f"Controller review must not depend on {forbidden}"

for required in (
    '"controllerReviewRequired": self.controller_review_required',
    '"controllerAppointmentActivated": self.controller_appointment_activated',
    '"publicationAllowed": self.publication_allowed',
    '"providerEffectAllowed": self.provider_effect_allowed',
    '"controllerReviewBlocksPublicationAndProviderEffect"',
    '"newAppointmentMustBeEffectiveBeforeAuthorityResumes"',
    '"shadowControllerReviewDoesNotWriteVaultState"',
):
    assert required in source, f"missing controller-review G0 invariant: {required}"

print("owner truth memorial controller review static gate passed")
PY
