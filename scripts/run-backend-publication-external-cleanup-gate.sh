#!/usr/bin/env bash
set -euo pipefail

# P2-S4C is a default-off internal lifecycle lane. This gate proves the
# materializer only creates pending effect evidence after local denial; it
# does not invoke a Provider or claim external completion.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
PYTHONPATH=. "$PYTHON_BIN" -m unittest -q \
  tests.test_publication_external_cleanup \
  tests.test_publication_external_cleanup_materializer_worker \
  tests.test_publication_external_cleanup_migration_contract \
  tests.test_publication_lifecycle_api \
  tests.test_publication_lifecycle_execution_migration_contract

PYTHONPATH=. "$PYTHON_BIN" -m py_compile \
  app/async_effects/publication_external_cleanup_materializer_worker.py \
  app/services/publication_external_cleanup.py \
  app/services/publication_lifecycle_execution.py

python3 - <<'PY'
from pathlib import Path

worker = Path("app/async_effects/publication_external_cleanup_materializer_worker.py").read_text(
    encoding="utf-8"
)
assert "publication_external_cleanup_materializer_enabled" in worker
assert "PublicationExternalCleanupCoordinator" in worker
assert "ProviderEffectState.COMPLETED" not in worker
assert "noPendingPublicationExternalCleanup" in worker
print("Publication external cleanup P2-S4C gate passed")
PY

bash -n scripts/run-backend-publication-external-cleanup-gate.sh
