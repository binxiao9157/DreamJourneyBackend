#!/usr/bin/env bash
set -euo pipefail

# G0-only. This verifies a Persona authority preflight can build only a
# non-mutating immutable-record plan. It must not import routes, stores,
# effects or providers, and it cannot claim a write was committed.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
"$PYTHON_BIN" -m unittest \
  tests.test_owner_truth_persona_authority_command_shadow \
  tests.test_owner_truth_persona_authority_receipt_shadow
"$PYTHON_BIN" -m py_compile \
  app/services/owner_truth_persona_authority_command_shadow.py \
  app/services/owner_truth_persona_authority_receipt_shadow.py

"$PYTHON_BIN" - <<'PY'
import ast
from pathlib import Path

from app.services.owner_truth_persona_authority_receipt_shadow import (
    OWNER_TRUTH_PERSONA_PROFILE_CONTENT_SCHEMA_VERSION,
)

assert OWNER_TRUTH_PERSONA_PROFILE_CONTENT_SCHEMA_VERSION == "persona-profile-v1"

for path in (
    Path("app/services/owner_truth_persona_authority_command_shadow.py"),
    Path("app/services/owner_truth_persona_authority_receipt_shadow.py"),
):
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module = getattr(node, "module", "") or ""
            assert not module.startswith("app.main"), "Persona G0 must not import API routes"
            assert not module.startswith("app.async_effects"), "Persona G0 must not import effects"
            assert not module.startswith("app.services.postgres_store"), "Persona G0 must not import persistence"
            assert not module.startswith("app.services.in_memory_store"), "Persona G0 must not import persistence"
    for forbidden in ("subprocess", "requests", "httpx", "psycopg", "sqlalchemy"):
        assert forbidden not in source, f"Persona G0 must not depend on {forbidden}"

receipt_source = Path("app/services/owner_truth_persona_authority_receipt_shadow.py").read_text(
    encoding="utf-8"
)
for required in (
    '"recordsWritten": self.records_written',
    '"personaVersionWritten": self.persona_version_written',
    '"decisionReceiptWritten": self.decision_receipt_written',
    '"futureWriterMustAtomicallyPersistPersonaVersionAndDecisionReceipt"',
    '"shadowReceiptPlanDoesNotWriteAuthority"',
):
    assert required in receipt_source, f"missing Persona receipt G0 invariant: {required}"

print("owner truth persona authority receipt static gate passed")
PY
