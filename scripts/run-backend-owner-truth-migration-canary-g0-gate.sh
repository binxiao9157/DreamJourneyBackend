#!/usr/bin/env bash
set -euo pipefail

# G0-only. C06 canary planning is default-off and may not route traffic,
# execute a command, copy an object, call a Provider, change Authority or
# retire a legacy writer.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
PYTHONPATH=. "$PYTHON_BIN" -m unittest -q tests.test_owner_truth_migration_canary_shadow
"$PYTHON_BIN" -m py_compile app/domain/owner_truth/migration_canary_shadow.py

PYTHONPATH=. "$PYTHON_BIN" - <<'PY'
import ast
from pathlib import Path

from app.domain.owner_truth.migration_canary_shadow import (
    MigrationCanaryRollbackPlane,
    OWNER_TRUTH_MIGRATION_CANARY_SHADOW_SCHEMA_VERSION,
)

assert OWNER_TRUTH_MIGRATION_CANARY_SHADOW_SCHEMA_VERSION == (
    "owner-truth-migration-canary-shadow-v1"
)
assert len(MigrationCanaryRollbackPlane) == 5

path = Path("app/domain/owner_truth/migration_canary_shadow.py")
source = path.read_text(encoding="utf-8")
tree = ast.parse(source)

for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        module = getattr(node, "module", "") or ""
        assert not module.startswith("app.main"), "C06 must not import API routes"
        assert not module.startswith("app.services"), "C06 G0 must not import service writers"
        assert not module.startswith("app.db"), "C06 G0 must not import persistence"
        assert not module.startswith("app.async_effects"), "C06 G0 must not import effects"
        assert not module.startswith("app.providers"), "C06 G0 must not import Providers"

for forbidden in (
    "requests",
    "httpx",
    "urllib.request",
    "psycopg",
    "boto3",
    "subprocess",
    "socket",
    ".execute(",
    ".accept(",
):
    assert forbidden not in source, f"C06 G0 must not perform side effects: {forbidden}"

for required in (
    '"canaryExecutionAllowed": self.canary_execution_allowed',
    '"authorityEpochChanged": self.authority_epoch_changed',
    '"legacyWriterRetired": self.legacy_writer_retired',
    '"publicTrafficAllowed": False',
):
    assert required in source, f"missing C06 fail-closed invariant: {required}"

print("Owner Truth migration canary C06 G0 gate passed")
PY
