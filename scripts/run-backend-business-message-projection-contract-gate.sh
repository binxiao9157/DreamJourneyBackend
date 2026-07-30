#!/usr/bin/env bash
set -euo pipefail

# G0/G2-A internal shadow only. This validates durable, append-only message
# metadata without changing mailbox_letters, public reads, notification
# dispatch, worker state, or Provider behavior.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
STORE_BACKEND=memory PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_business_message_notification_effects \
  tests.test_business_message_projection_repository \
  tests.test_business_message_projection_migration_contract \
  tests.test_business_message_projection_postgres_smoke_contract
PYTHONPATH=. "$PYTHON_BIN" -m py_compile \
  app/async_effects/message_notification_effects.py \
  app/async_effects/business_message_projection_repository.py \
  app/services/postgres_store.py

if [[ "${RUN_BUSINESS_MESSAGE_PROJECTION_POSTGRES_SMOKE:-0}" == "1" ]]; then
  PYTHONPATH=. "$PYTHON_BIN" scripts/backend-business-message-projection-postgres-smoke.py
fi

echo "Business-message projection shadow contract gate passed"
