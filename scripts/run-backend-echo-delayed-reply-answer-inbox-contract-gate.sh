#!/usr/bin/env bash
set -euo pipefail

# Default-off G0 contract gate for WI-S1-02-06. This validates only local
# semantics; it never calls a model Provider, starts a worker, or needs a DB.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
STORE_BACKEND=memory PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_echo_delayed_reply_effects \
  tests.test_echo_delayed_reply_service \
  tests.test_core_services.EchoDelayedReplyAPITests \
  tests.test_postgres_store

PYTHONPATH=. "$PYTHON_BIN" - <<'PY'
from app.db.migrator import default_migrations_dir, load_migrations

migrations = load_migrations(default_migrations_dir())
assert migrations[-1].version == "0024", migrations[-1].version
assert migrations[-1].name == "echo_delayed_reply_answer_completion", migrations[-1].name
print("Echo delayed reply Answer/Inbox migration contract passed")
PY

PYTHONPATH=. "$PYTHON_BIN" -m py_compile \
  app/services/echo_delayed_reply_effects.py \
  app/services/echo_delayed_reply_service.py \
  app/services/in_memory_store.py \
  app/services/postgres_store.py \
  scripts/backend-echo-delayed-reply-atomic-completion-postgres-smoke.py

echo "Echo delayed reply Answer/Inbox local contract gate passed"
