#!/usr/bin/env bash
set -euo pipefail

# G0-only. A scope hold must not become a database mutation, provider stop or
# cleanup request until independent G2/G4 implementation proves the transaction.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
"$PYTHON_BIN" -m unittest \
  tests.test_owner_truth_memorial_conflict_hold_shadow
"$PYTHON_BIN" -m py_compile \
  app/services/owner_truth_memorial_conflict_hold_shadow.py

"$PYTHON_BIN" - <<'PY'
import ast
from pathlib import Path

from app.services.owner_truth_memorial_conflict_hold_shadow import (
    MemorialConflictHoldScope,
    MemorialConflictHoldTrigger,
)

assert {item.value for item in MemorialConflictHoldScope} == {
    "voice_training",
    "voice_synthesis_private",
    "portrait_rendering",
    "digital_human_private",
    "publication_text",
    "publication_voice",
    "publication_digital_human",
    "vault_closure",
}
assert {item.value for item in MemorialConflictHoldTrigger} == {
    "verified_close_relative_rights_claim",
    "court_or_regulator_order",
    "source_rights_dispute",
    "policy_change",
    "unknown",
}

path = Path("app/services/owner_truth_memorial_conflict_hold_shadow.py")
source = path.read_text(encoding="utf-8")
tree = ast.parse(source)
for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        module = getattr(node, "module", "") or ""
        assert not module.startswith("app.main"), "ConflictHold gate must not import API routes"
        assert not module.startswith("app.async_effects"), "ConflictHold gate must not import effects"
        assert not module.startswith("app.services.postgres_store"), "ConflictHold gate must not import persistence"
        assert not module.startswith("app.services.in_memory_store"), "ConflictHold gate must not import persistence"

for forbidden in ("subprocess", "requests", "httpx", "psycopg", "sqlalchemy"):
    assert forbidden not in source, f"ConflictHold gate must not depend on {forbidden}"

for required in (
    '"conflictHoldRequired": self.conflict_hold_required',
    '"authorityEpochIncrementRequired": self.conflict_hold_required',
    '"futureWriterMustAtomicallyAdvanceEpochAndSuspendScope"',
    '"futureWriterMustStopNewGenerationAndPlaybackBeforeProviderCleanup"',
    '"shadowConflictHoldDoesNotPersistOrCallProvider"',
):
    assert required in source, f"missing ConflictHold G0 invariant: {required}"

print("owner truth memorial conflict hold static gate passed")
PY
