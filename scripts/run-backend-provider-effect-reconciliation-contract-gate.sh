#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
STORE_BACKEND=memory PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_provider_effects \
  tests.test_provider_effect_repository \
  tests.test_provider_effect_reconciliation_migration_contract
PYTHONPATH=. "$PYTHON_BIN" -m py_compile \
  app/async_effects/provider_effects.py \
  app/async_effects/provider_effect_repository.py
"$PYTHON_BIN" - <<'PY'
from app.db.migrator import default_migrations_dir, load_migrations

migration = next(item for item in load_migrations(default_migrations_dir()) if item.version == "0025")
assert "provider_effect_reconciliation_projection" in migration.sql
assert "providerQuery" in migration.sql
print("Provider-effect reconciliation contract gate passed")
PY
