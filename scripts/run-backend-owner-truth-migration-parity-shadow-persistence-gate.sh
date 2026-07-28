#!/usr/bin/env bash
set -euo pipefail

# C05 persistence remains default-off and append-only. It may write its own
# evidence tables only; it cannot activate a cutover or execute a legacy/V4
# command, object copy, async effect or Provider request.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
PYTHONPATH=. "$PYTHON_BIN" -m unittest -q \
  tests.test_owner_truth_migration_parity_shadow \
  tests.test_owner_truth_migration_parity_shadow_service \
  tests.test_owner_truth_migration_parity_shadow_migration_contract
"$PYTHON_BIN" -m py_compile app/domain/owner_truth/migration_parity_shadow.py
"$PYTHON_BIN" -m py_compile app/services/owner_truth_migration_parity_shadow.py

PYTHONPATH=. "$PYTHON_BIN" - <<'PY'
import ast
from pathlib import Path

from app.db.migrator import default_migrations_dir, load_migrations
from app.domain.owner_truth.migration_parity_shadow import (
    OWNER_TRUTH_MIGRATION_PARITY_SHADOW_SCHEMA_VERSION,
)
from app.services.owner_truth_migration_parity_shadow import (
    OWNER_TRUTH_MIGRATION_PARITY_SHADOW_SERVICE_SCHEMA_VERSION,
)

assert OWNER_TRUTH_MIGRATION_PARITY_SHADOW_SCHEMA_VERSION == (
    "owner-truth-migration-parity-shadow-v1"
)
assert OWNER_TRUTH_MIGRATION_PARITY_SHADOW_SERVICE_SCHEMA_VERSION == (
    "owner-truth-migration-parity-shadow-service-v1"
)
migration_by_version = {item.version: item for item in load_migrations(default_migrations_dir())}
assert migration_by_version["0049"].name == "owner_truth_migration_parity_shadow"

source_path = Path("app/services/owner_truth_migration_parity_shadow.py")
source = source_path.read_text(encoding="utf-8")
tree = ast.parse(source)
for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        module = getattr(node, "module", "") or ""
        assert not module.startswith("app.main"), "C05 persistence must not import API routes"
        assert not module.startswith("app.async_effects"), "C05 must not import effect writers"
        assert not module.startswith("app.providers"), "C05 must not import Provider adapters"

for forbidden in (
    "requests",
    "httpx",
    "urllib.request",
    "boto3",
    "subprocess",
    "socket",
    "INSERT INTO async_effects",
    "INSERT INTO owner_truth.sources",
    "INSERT INTO owner_truth.memory_candidates",
    "INSERT INTO owner_truth.memory_versions",
    "UPDATE owner_truth.vaults",
    ".accept(",
):
    assert forbidden not in source, "C05 persistence must remain evidence-only: %s" % forbidden

print("Owner Truth migration parity C05 persistence gate passed")
PY
