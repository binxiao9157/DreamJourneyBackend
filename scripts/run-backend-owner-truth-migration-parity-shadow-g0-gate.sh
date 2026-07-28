#!/usr/bin/env bash
set -euo pipefail

# G0-only. C05 compares opaque legacy/V4 descriptors and must not read or
# write a database, execute a command, copy an object, call a Provider, or
# authorize cutover.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
PYTHONPATH=. "$PYTHON_BIN" -m unittest -q tests.test_owner_truth_migration_parity_shadow
"$PYTHON_BIN" -m py_compile app/domain/owner_truth/migration_parity_shadow.py

PYTHONPATH=. "$PYTHON_BIN" - <<'PY'
import ast
from pathlib import Path

from app.domain.owner_truth.migration_parity_shadow import (
    MigrationParityDimension,
    MigrationParityMismatchCode,
    OWNER_TRUTH_MIGRATION_PARITY_SHADOW_SCHEMA_VERSION,
)

assert OWNER_TRUTH_MIGRATION_PARITY_SHADOW_SCHEMA_VERSION == "owner-truth-migration-parity-shadow-v1"
assert set(MigrationParityMismatchCode) == {
    MigrationParityMismatchCode.M01,
    MigrationParityMismatchCode.M02,
    MigrationParityMismatchCode.M03,
    MigrationParityMismatchCode.M04,
    MigrationParityMismatchCode.M05,
    MigrationParityMismatchCode.M06,
    MigrationParityMismatchCode.M07,
    MigrationParityMismatchCode.M08,
}
assert len(set(MigrationParityDimension)) >= 30

source_path = Path("app/domain/owner_truth/migration_parity_shadow.py")
source = source_path.read_text(encoding="utf-8")
tree = ast.parse(source)
for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        module = getattr(node, "module", "") or ""
        assert not module.startswith("app.main"), "C05 must not import API routes"
        assert not module.startswith("app.services"), "C05 G0 must not import service writers"
        assert not module.startswith("app.db"), "C05 G0 must not import persistence"

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
    assert forbidden not in source, "C05 G0 must not perform side effects: %s" % forbidden

print("Owner Truth migration parity C05 G0 gate passed")
PY
