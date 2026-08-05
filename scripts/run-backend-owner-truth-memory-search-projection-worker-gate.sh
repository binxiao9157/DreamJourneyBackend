#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"

PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_owner_truth_memory_projection_worker \
  tests.test_owner_truth_memory_search_projection

PYTHONPATH=. "$PYTHON_BIN" -m py_compile \
  app/async_effects/owner_truth_memory_projection_worker.py \
  app/async_effects/worker_lifecycle.py \
  app/services/owner_truth_memory_search_projection.py

"$PYTHON_BIN" - <<'PY'
from pathlib import Path

worker_source = Path("app/async_effects/owner_truth_memory_projection_worker.py").read_text(
    encoding="utf-8"
)
for required in (
    "owner_truth_memory_search_projection_worker_enabled",
    "owner_truth_memory_search_document_projection_repository",
    "search projection rebuild returned an invalid outcome",
    "search projection rebuild returned a cross-scope or stale checkpoint",
    "searchProjectionOutcome",
    "WorkerLeaseHeartbeat",
    "def _renew_lease(",
):
    assert required in worker_source, f"missing SearchDocument worker invariant: {required}"

config_source = Path("app/core/config.py").read_text(encoding="utf-8")
assert "OWNER_TRUTH_MEMORY_SEARCH_PROJECTION_WORKER_ENABLED" in config_source
env_source = Path(".env.example").read_text(encoding="utf-8")
assert "OWNER_TRUTH_MEMORY_SEARCH_PROJECTION_WORKER_ENABLED=false" in env_source
print("Owner Truth SearchDocument projection worker gate passed")
PY
