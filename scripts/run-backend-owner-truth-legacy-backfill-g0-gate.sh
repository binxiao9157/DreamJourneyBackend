#!/usr/bin/env bash
set -euo pipefail

# G0-only. C03 creates a value-minimized, append-only admission plan over an
# immutable legacy inventory. It must not create target Owner Truth records,
# mutate legacy records, retire a writer, or enable a cutover.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
PYTHONPATH=. "$PYTHON_BIN" -m unittest -q \
  tests.test_owner_truth_legacy_backfill_plan \
  tests.test_owner_truth_legacy_backfill_service \
  tests.test_owner_truth_legacy_backfill_migration_contract \
  tests.test_owner_truth_legacy_backfill_api

PYTHONPATH=. "$PYTHON_BIN" - <<'PY'
from app.db.migrator import default_migrations_dir, load_migrations
from app.domain.owner_truth.legacy_backfill import (
    OWNER_TRUTH_LEGACY_BACKFILL_PLAN_SCHEMA_VERSION,
)

migrations = load_migrations(default_migrations_dir())
migration_by_version = {item.version: item for item in migrations}
assert migration_by_version["0047"].name == "owner_truth_legacy_backfill_admission_plan"
assert OWNER_TRUTH_LEGACY_BACKFILL_PLAN_SCHEMA_VERSION == (
    "owner-truth-legacy-backfill-admission-plan-v1"
)
print("Owner Truth legacy backfill C03 G0 gate passed")
PY
