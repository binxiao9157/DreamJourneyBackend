#!/usr/bin/env bash
set -euo pipefail

# G0-only. This verifies legacy profiles remain migration input, never Persona
# authority. The shadow must not import routes, stores, effects or providers.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
"$PYTHON_BIN" -m unittest tests.test_owner_truth_persona_profile_legacy_boundary_shadow
"$PYTHON_BIN" -m py_compile app/services/owner_truth_persona_profile_legacy_boundary_shadow.py

"$PYTHON_BIN" - <<'PY'
import ast
from pathlib import Path

from app.services.owner_truth_persona_profile_legacy_boundary_shadow import (
    _PERSONA_ALLOWLIST,
)

assert set(_PERSONA_ALLOWLIST.values()) == {"birthDate", "displayName", "gender"}
for excluded in ("avatarName", "region", "voiceProfileId", "digitalHumanId", "personaScope"):
    assert excluded not in _PERSONA_ALLOWLIST

path = Path("app/services/owner_truth_persona_profile_legacy_boundary_shadow.py")
source = path.read_text(encoding="utf-8")
tree = ast.parse(source)
for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        module = getattr(node, "module", "") or ""
        assert not module.startswith("app.main"), "profile shadow must not import API routes"
        assert not module.startswith("app.async_effects"), "profile shadow must not import effects"
        assert not module.startswith("app.services.postgres_store"), "profile shadow must not import persistence"
        assert not module.startswith("app.services.in_memory_store"), "profile shadow must not import persistence"

for forbidden in ("subprocess", "requests", "httpx", "psycopg"):
    assert forbidden not in source, f"profile shadow must not depend on {forbidden}"

for required in (
    '"personaCreated": self.persona_created',
    '"legacyProfileMigrated": self.legacy_profile_migrated',
    '"separatePersonaAuthorityCommandRequired"',
):
    assert required in source, f"missing Persona boundary invariant: {required}"

print("owner truth persona profile legacy boundary static gate passed")
PY
