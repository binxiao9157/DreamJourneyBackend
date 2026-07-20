#!/usr/bin/env bash
set -euo pipefail

# G0-only. Memorial primary-controller bootstrap/transfer is a future plan,
# not a writer. The gate forbids API/store/effect/Provider dependencies and
# proves that client payloads cannot select the Persona or either controller.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
"$PYTHON_BIN" -m unittest \
  tests.test_owner_truth_memorial_authority_admission_shadow \
  tests.test_owner_truth_memorial_controller_appointment_shadow
"$PYTHON_BIN" -m py_compile \
  app/services/owner_truth_memorial_authority_admission_shadow.py \
  app/services/owner_truth_memorial_controller_appointment_shadow.py

"$PYTHON_BIN" - <<'PY'
import ast
from pathlib import Path

from app.services.owner_truth_memorial_controller_appointment_shadow import (
    MemorialControllerAppointmentOperation,
    _COMMAND_FIELD_NAMES,
)

assert _COMMAND_FIELD_NAMES == {"commandId", "expectedVersion", "operation"}
assert set(MemorialControllerAppointmentOperation) == {
    MemorialControllerAppointmentOperation.BOOTSTRAP,
    MemorialControllerAppointmentOperation.TRANSFER,
}

path = Path("app/services/owner_truth_memorial_controller_appointment_shadow.py")
source = path.read_text(encoding="utf-8")
tree = ast.parse(source)
for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        module = getattr(node, "module", "") or ""
        assert not module.startswith("app.main"), "Memorial controller shadow must not import API routes"
        assert not module.startswith("app.async_effects"), "Memorial controller shadow must not import effects"
        assert not module.startswith("app.services.postgres_store"), "Memorial controller shadow must not import persistence"
        assert not module.startswith("app.services.in_memory_store"), "Memorial controller shadow must not import persistence"

for forbidden in ("subprocess", "requests", "httpx", "psycopg", "sqlalchemy"):
    assert forbidden not in source, f"Memorial controller shadow must not depend on {forbidden}"

for required in (
    '"authorityEpochChanged": False',
    '"controllerAppointmentWritten": self.controller_appointment_written',
    '"familyContributorMayOnlySubmitSourceOrCandidate"',
    '"representedPersonaCannotBeLoginPrincipal"',
    '"futureWriterMustAtomicallyRevokeAndActivatePrimaryController"',
    '"shadowControllerPlanDoesNotWriteAuthority"',
    '"transferRequiresCurrentPrimaryController"',
):
    assert required in source, f"missing Memorial controller G0 invariant: {required}"

print("owner truth memorial controller appointment static gate passed")
PY
