#!/usr/bin/env bash
set -euo pipefail

# P0-S2 worker gate. The message projection worker remains default-off and
# internal-only. The optional runtime smoke uses a disposable Postgres database
# and must not start the Compose worker profile or write mailbox_letters.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
STORE_BACKEND=memory PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_business_message_projection_effects \
  tests.test_business_message_projection_enqueue \
  tests.test_business_message_projection_request_repository \
  tests.test_business_message_projection_worker \
  tests.test_business_message_projection_worker_migration_contract \
  tests.test_business_message_projection_worker_postgres_smoke_contract \
  tests.test_owner_truth_worker_process \
  tests.test_operation_metric_coverage

PYTHONPATH=. "$PYTHON_BIN" -m py_compile \
  app/core/config.py \
  app/async_effects/business_message_projection_effects.py \
  app/async_effects/business_message_projection_enqueue.py \
  app/async_effects/business_message_projection_request_repository.py \
  app/async_effects/business_message_projection_worker.py \
  app/services/postgres_store.py \
  scripts/backend-business-message-projection-worker-postgres-smoke.py

PYTHONPATH=. "$PYTHON_BIN" - <<'PY'
from pathlib import Path

from app.core.config import Settings

settings = Settings()
assert settings.business_message_projection_worker_enabled is False

worker = Path("app/async_effects/business_message_projection_worker.py").read_text(
    encoding="utf-8"
)
assert "mailbox_letters" in worker
assert "never writes ``mailbox_letters``" in worker
assert "business_message_projection_worker_enabled" in worker
assert "WorkerLeaseHeartbeat" in worker
assert "admit_dead_letter" in worker
assert "businessMessageProjectionRetriesExhausted" in worker
assert "businessMessageProjectionInboxSnapshotMismatch" in worker
assert "businessMessageProjectionCrossAccountUnsupported" in worker
assert "OperationMetricRecorder" in worker
print("Business-message projection worker default-off contract gate passed")
PY

bash -n scripts/run-backend-business-message-projection-worker-postgres-smoke.sh

if [[ "${RUN_BUSINESS_MESSAGE_PROJECTION_WORKER_POSTGRES_SMOKE:-0}" == "1" ]]; then
  : "${DATABASE_URL:?DATABASE_URL is required when the worker Postgres smoke is enabled}"
  PYTHONPATH=. "$PYTHON_BIN" scripts/backend-business-message-projection-worker-postgres-smoke.py
fi

echo "Business-message projection worker gate passed"
