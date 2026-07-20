#!/usr/bin/env bash
set -euo pipefail

# G0-only. This validates the read-only Provider-query operations baseline.
# It never holds a Provider credential, invokes a Provider, replays an effect,
# or enables automatic reconciliation.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
STORE_BACKEND=memory PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_provider_effects \
  tests.test_provider_effect_repository \
  tests.test_provider_query_operations \
  tests.test_async_effect_worker
PYTHONPATH=. "$PYTHON_BIN" -m py_compile \
  app/async_effects/provider_effects.py \
  app/async_effects/provider_effect_repository.py \
  app/async_effects/provider_query_operations.py \
  app/async_effects/worker.py
"$PYTHON_BIN" - <<'PY'
from pathlib import Path

source = Path("app/async_effects/provider_query_operations.py").read_text(encoding="utf-8")
forbidden = ("urlopen(", "requests.", "httpx.", "query_status(")
assert not any(token in source for token in forbidden), "query baseline must not invoke a Provider"
assert '"providerQueryExecutionEnabled": False' in source
assert '"automaticReconciliationEnabled": False' in source
assert '"replayEnabled": False' in source
print("Provider-query operations G0 contract gate passed")
PY
