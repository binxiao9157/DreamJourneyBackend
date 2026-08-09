#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

"$ROOT_DIR/.venv/bin/python" -m unittest \
  tests.test_data_rights_external_deletion_executor \
  tests.test_data_rights_external_effect_reconciler

if [[ "${RUN_POSTGRES_EXTERNAL_DELETION_SMOKE:-0}" == "1" ]]; then
  "$ROOT_DIR/.venv/bin/python" \
    scripts/backend-data-rights-external-effect-receipts-postgres-smoke.py
else
  printf '%s\n' \
    'Postgres external-deletion smoke skipped; set RUN_POSTGRES_EXTERNAL_DELETION_SMOKE=1.'
fi
