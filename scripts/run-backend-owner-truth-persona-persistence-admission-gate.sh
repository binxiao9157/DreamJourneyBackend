#!/usr/bin/env bash
set -euo pipefail

# G0-only. A planned PersonaVersion/DecisionReceipt must never self-authorize
# persistence. This gate keeps the admission shadow default-off and free of
# route, store, effect and Provider dependencies.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
"$PYTHON_BIN" -m unittest \
  tests.test_owner_truth_persona_authority_command_shadow \
  tests.test_owner_truth_persona_authority_receipt_shadow \
  tests.test_owner_truth_persona_persistence_admission_shadow
"$PYTHON_BIN" -m py_compile \
  app/services/owner_truth_persona_authority_command_shadow.py \
  app/services/owner_truth_persona_authority_receipt_shadow.py \
  app/services/owner_truth_persona_persistence_admission_shadow.py

"$PYTHON_BIN" - <<'PY'
import ast
from pathlib import Path

path = Path("app/services/owner_truth_persona_persistence_admission_shadow.py")
source = path.read_text(encoding="utf-8")
tree = ast.parse(source)

for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        module = getattr(node, "module", "") or ""
        assert not module.startswith("app.main"), "Persona admission must not import API routes"
        assert not module.startswith("app.async_effects"), "Persona admission must not import effects"
        assert not module.startswith("app.services.postgres_store"), "Persona admission must not import persistence"
        assert not module.startswith("app.services.in_memory_store"), "Persona admission must not import persistence"

for forbidden in ("subprocess", "requests", "httpx", "psycopg", "sqlalchemy"):
    assert forbidden not in source, f"Persona admission must not depend on {forbidden}"

for required in (
    '"persistenceAdmitted": self.persistence_admitted',
    '"repositoryWritten": self.repository_written',
    '"schemaChanged": self.schema_changed',
    '"shadowPlanCannotSelfAuthorizePersonaPersistence"',
    '"shadowClaimsCannotAuthorizePersonaPersistence"',
    '"versionCasRequiresTransactionalRepositoryProof"',
):
    assert required in source, f"missing Persona persistence G0 invariant: {required}"

print("owner truth persona persistence admission static gate passed")
PY
