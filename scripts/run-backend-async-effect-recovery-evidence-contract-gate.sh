#!/usr/bin/env bash
set -euo pipefail

# G0-only: validates post-restore replay fencing and the strict mapping from
# readiness states to an append-only evidence-manifest plan. It does not write
# a manifest, re-enqueue work, claim a worker lease, or call a Provider.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
STORE_BACKEND=memory PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_async_effect_dead_letter_contract \
  tests.test_async_effect_readiness_evidence \
  tests.test_async_effect_recovery_evidence
PYTHONPATH=. "$PYTHON_BIN" -m py_compile \
  app/async_effects/dead_letter_effects.py \
  app/async_effects/readiness_evidence.py \
  app/async_effects/recovery_evidence.py

echo "Async-effect recovery/evidence G0 contract gate passed"
