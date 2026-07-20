#!/usr/bin/env bash
set -euo pipefail

# G0-only. This proves the future Self Persona Authority writer has a strict,
# default-off command preflight and cannot mutate a profile, provider or store.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
"$PYTHON_BIN" -m unittest tests.test_owner_truth_persona_authority_command_shadow
"$PYTHON_BIN" -m py_compile app/services/owner_truth_persona_authority_command_shadow.py

"$PYTHON_BIN" - <<'PY'
import ast
from pathlib import Path

from app.services.owner_truth_persona_authority_command_shadow import (
    PERSONA_PROFILE_ALLOWED_FIELD_NAMES,
    OwnerTruthPersonaAuthorityCommandOrigin,
)

assert PERSONA_PROFILE_ALLOWED_FIELD_NAMES == {"birthDate", "displayName", "gender"}
assert set(OwnerTruthPersonaAuthorityCommandOrigin) == {
    OwnerTruthPersonaAuthorityCommandOrigin.OWNER_INTERACTIVE,
    OwnerTruthPersonaAuthorityCommandOrigin.FAMILY,
    OwnerTruthPersonaAuthorityCommandOrigin.ASSISTANT,
    OwnerTruthPersonaAuthorityCommandOrigin.PROVIDER,
    OwnerTruthPersonaAuthorityCommandOrigin.RUNTIME,
    OwnerTruthPersonaAuthorityCommandOrigin.UNKNOWN,
}

path = Path("app/services/owner_truth_persona_authority_command_shadow.py")
source = path.read_text(encoding="utf-8")
tree = ast.parse(source)
for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        module = getattr(node, "module", "") or ""
        assert not module.startswith("app.main"), "Persona preflight must not import API routes"
        assert not module.startswith("app.async_effects"), "Persona preflight must not import effects"
        assert not module.startswith("app.services.postgres_store"), "Persona preflight must not import persistence"
        assert not module.startswith("app.services.in_memory_store"), "Persona preflight must not import persistence"

for forbidden in ("subprocess", "requests", "httpx", "psycopg", "sqlalchemy"):
    assert forbidden not in source, f"Persona preflight must not depend on {forbidden}"

for required in (
    '"personaVersionWritten": self.persona_version_written',
    '"decisionReceiptWritten": self.decision_receipt_written',
    '"providerOrRuntimeMutated": self.provider_or_runtime_mutated',
    '"deceasedPersonaRequiresControllerNotLoginPrincipal"',
    '"familyCannotWritePersonaAuthority"',
    '"assistantCannotWritePersonaAuthority"',
    '"providerCannotWritePersonaAuthority"',
    '"runtimeCannotWritePersonaAuthority"',
    '"shadowPreflightDoesNotMutateAuthority"',
):
    assert required in source, f"missing Persona Authority G0 invariant: {required}"

print("owner truth persona authority command static gate passed")
PY
