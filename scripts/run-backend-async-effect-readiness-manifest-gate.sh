#!/usr/bin/env bash
set -euo pipefail

# G2-safe internal persistence contract only: append value-free readiness
# manifest metadata through the existing evidence sink. This never starts a
# worker, replays work, claims a lease, or calls a Provider.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
STORE_BACKEND=memory PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_async_effect_readiness_evidence \
  tests.test_async_effect_recovery_evidence \
  tests.test_async_effect_readiness_manifest_projection \
  tests.test_evidence_manifest
PYTHONPATH=. "$PYTHON_BIN" -m py_compile \
  app/async_effects/readiness_evidence.py \
  app/async_effects/readiness_manifest_projection.py \
  app/observability/evidence_manifest.py

if [[ "${RUN_ASYNC_EFFECT_READINESS_MANIFEST_POSTGRES_SMOKE:-0}" == "1" ]]; then
  PYTHONPATH=. "$PYTHON_BIN" scripts/backend-async-effect-readiness-manifest-postgres-smoke.py
fi

echo "Async-effect readiness manifest persistence gate passed"
