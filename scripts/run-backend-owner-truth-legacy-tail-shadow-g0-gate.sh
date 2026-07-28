#!/usr/bin/env bash
set -euo pipefail

# G0-only. C04 maps hash-only legacy tail descriptors to future outbox,
# object-reference and Provider planes. It must not persist an effect, touch
# object storage, call/query a Provider, accept a callback, or alter Authority.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
PYTHONPATH=. "$PYTHON_BIN" -m unittest -q \
  tests.test_owner_truth_legacy_tail_shadow \
  tests.test_owner_truth_legacy_tail_shadow_service \
  tests.test_owner_truth_legacy_tail_shadow_migration_contract \
  tests.test_owner_truth_legacy_tail_shadow_postgres_smoke_contract
"$PYTHON_BIN" -m py_compile app/domain/owner_truth/legacy_tail_shadow.py
"$PYTHON_BIN" -m py_compile app/services/owner_truth_legacy_tail_shadow.py

PYTHONPATH=. "$PYTHON_BIN" - <<'PY'
import ast
from pathlib import Path

from app.domain.owner_truth.legacy_tail_shadow import (
    OWNER_TRUTH_LEGACY_TAIL_SHADOW_SCHEMA_VERSION,
)
from app.db.migrator import default_migrations_dir, load_migrations
from app.services.owner_truth_legacy_tail_shadow import (
    OWNER_TRUTH_LEGACY_TAIL_SHADOW_SERVICE_SCHEMA_VERSION,
)

assert OWNER_TRUTH_LEGACY_TAIL_SHADOW_SCHEMA_VERSION == "owner-truth-legacy-tail-shadow-v1"
assert OWNER_TRUTH_LEGACY_TAIL_SHADOW_SERVICE_SCHEMA_VERSION == (
    "owner-truth-legacy-tail-shadow-service-v1"
)
migration_by_version = {item.version: item for item in load_migrations(default_migrations_dir())}
assert migration_by_version["0048"].name == "owner_truth_legacy_tail_shadow"
source_path = Path("app/domain/owner_truth/legacy_tail_shadow.py")
source = source_path.read_text(encoding="utf-8")
tree = ast.parse(source)

for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        module = getattr(node, "module", "") or ""
        assert not module.startswith("app.main"), "C04 must not import API routes"
        assert not module.startswith("app.services.postgres_store"), "C04 must not import persistence"
        assert not module.startswith("app.async_effects.repository"), "C04 must not import effect writers"

for forbidden in (
    "requests",
    "httpx",
    "urllib.request",
    "psycopg",
    "boto3",
    "subprocess",
    ".accept(",
):
    assert forbidden not in source, f"C04 G0 must not perform side effects: {forbidden}"

print("Owner Truth legacy tail C04 G0 gate passed")
PY

if [[ "${RUN_OWNER_TRUTH_LEGACY_TAIL_SHADOW_POSTGRES_SMOKE:-0}" == "1" ]]; then
  "$ROOT_DIR/scripts/run-backend-owner-truth-legacy-tail-shadow-postgres-smoke.sh"
fi
