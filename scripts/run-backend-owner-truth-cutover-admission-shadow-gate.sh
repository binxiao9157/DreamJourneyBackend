#!/usr/bin/env bash
set -euo pipefail

# G0-only. This gate models a future Owner Truth cutover without granting it.
# It must not import routes, stores, effects or providers, and it may never
# advance authorityEpoch or retire a legacy writer.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
"$PYTHON_BIN" -m unittest \
  tests.test_owner_truth_cutover_admission_shadow \
  tests.test_owner_truth_legacy_shadow_parity \
  tests.test_owner_truth_legacy_shadow_parity_api
"$PYTHON_BIN" -m py_compile \
  app/services/owner_truth_cutover_admission_shadow.py \
  app/services/owner_truth_legacy_shadow_parity.py

"$PYTHON_BIN" - <<'PY'
import ast
from pathlib import Path

path = Path("app/services/owner_truth_cutover_admission_shadow.py")
source = path.read_text(encoding="utf-8")
tree = ast.parse(source)

for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        module = getattr(node, "module", "") or ""
        assert not module.startswith("app.main"), "cutover shadow must not import API routes"
        assert not module.startswith("app.async_effects"), "cutover shadow must not import effects"
        assert not module.startswith("app.services.postgres_store"), "cutover shadow must not import persistence"
        assert not module.startswith("app.services.in_memory_store"), "cutover shadow must not import persistence"

for forbidden in ("subprocess", "requests", "httpx", "psycopg"):
    assert forbidden not in source, f"cutover shadow must not depend on {forbidden}"

for required in (
    '"authorityEpochChanged": self.authority_epoch_changed',
    '"legacyWriterRetired": self.legacy_writer_retired',
    '"separateProductionGoRecordRequired"',
):
    assert required in source, f"missing fail-closed cutover invariant: {required}"

print("owner truth cutover admission shadow static gate passed")
PY
