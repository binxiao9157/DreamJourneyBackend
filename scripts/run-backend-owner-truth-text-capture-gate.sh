#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_owner_truth_text_capture_api \
  tests.test_owner_truth_create_source \
  tests.test_release_policy \
  tests.test_route_ownership_registry
PYTHONPATH=. "$PYTHON_BIN" -m py_compile \
  app/domain/owner_truth/source_commands.py \
  app/services/owner_truth_source.py \
  app/services/in_memory_store.py \
  app/services/postgres_store.py \
  app/services/release_policy.py \
  app/services/route_ownership.py \
  app/main.py

PYTHONPATH=. "$PYTHON_BIN" - <<'PY'
from app.core.config import Settings
from app.services.release_policy import ReleasePolicyService

assert "ownerTextCaptureV1" in ReleasePolicyService._CLOSED_PILOT_OPT_IN_FEATURES
assert not Settings().release_policy_closed_pilot_features
print("Owner Truth closed-pilot text capture gate passed")
PY
