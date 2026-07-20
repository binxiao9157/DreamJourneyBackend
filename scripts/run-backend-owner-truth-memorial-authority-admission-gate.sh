#!/usr/bin/env bash
set -euo pipefail

# G0-only. Memorial authority stays fail-closed until an independent G2/G4
# implementation supplies controller, verification, claim/hold and rights
# records. This shadow must never grow a route, store, effect or Provider call.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
"$PYTHON_BIN" -m unittest \
  tests.test_owner_truth_persona_authority_command_shadow \
  tests.test_owner_truth_memorial_authority_admission_shadow
"$PYTHON_BIN" -m py_compile \
  app/services/owner_truth_persona_authority_command_shadow.py \
  app/services/owner_truth_memorial_authority_admission_shadow.py

"$PYTHON_BIN" - <<'PY'
import ast
from pathlib import Path

from app.services.owner_truth_memorial_authority_admission_shadow import (
    MemorialPersonaAuthorityCommandOrigin,
)

assert set(MemorialPersonaAuthorityCommandOrigin) == {
    MemorialPersonaAuthorityCommandOrigin.MEMORIAL_CONTROLLER_INTERACTIVE,
    MemorialPersonaAuthorityCommandOrigin.FAMILY_CONTRIBUTOR,
    MemorialPersonaAuthorityCommandOrigin.ASSISTANT,
    MemorialPersonaAuthorityCommandOrigin.PROVIDER,
    MemorialPersonaAuthorityCommandOrigin.RUNTIME,
    MemorialPersonaAuthorityCommandOrigin.UNKNOWN,
}

path = Path("app/services/owner_truth_memorial_authority_admission_shadow.py")
source = path.read_text(encoding="utf-8")
tree = ast.parse(source)
for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        module = getattr(node, "module", "") or ""
        assert not module.startswith("app.main"), "Memorial admission must not import API routes"
        assert not module.startswith("app.async_effects"), "Memorial admission must not import effects"
        assert not module.startswith("app.services.postgres_store"), "Memorial admission must not import persistence"
        assert not module.startswith("app.services.in_memory_store"), "Memorial admission must not import persistence"

for forbidden in ("subprocess", "requests", "httpx", "psycopg", "sqlalchemy"):
    assert forbidden not in source, f"Memorial admission must not depend on {forbidden}"

for required in (
    '"memorialAuthorityAdmitted": self.memorial_authority_admitted',
    '"representedPersonaLoginPrincipal": False',
    '"familyContributorMayOnlySubmitSourceOrCandidate"',
    '"representedPersonaCannotBeLoginPrincipal"',
    '"activeRightsClaimBlocksMemorialAuthorityMutation"',
    '"activeConflictHoldBlocksMemorialAuthorityMutation"',
    '"shadowMemorialClaimsCannotAuthorizeAuthority"',
):
    assert required in source, f"missing Memorial Persona G0 invariant: {required}"

print("owner truth memorial authority admission static gate passed")
PY
