#!/usr/bin/env bash
set -euo pipefail

# G0-only: validates provider effect identities and the migration catalog. It
# never invokes a Provider, starts a worker, or needs a database.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
STORE_BACKEND=memory PYTHONPATH=. "$PYTHON_BIN" -m unittest tests.test_provider_effects
PYTHONPATH=. "$PYTHON_BIN" -m py_compile app/async_effects/provider_effects.py
PYTHONPATH=. "$PYTHON_BIN" - <<'PY'
from app.async_effects.provider_effects import provider_effect_catalog_summary

summary = provider_effect_catalog_summary()
assert summary["entryCount"] == 10, summary
assert summary["providerCallsEnabledByCatalog"] is False, summary
assert summary["stableRequestRequiredCount"] >= 7, summary
print(
    "Provider effect catalog contract passed "
    f"entries={summary['entryCount']} "
    f"stableRequestRequired={summary['stableRequestRequiredCount']}"
)
PY

echo "Provider effect G0 contract gate passed"
