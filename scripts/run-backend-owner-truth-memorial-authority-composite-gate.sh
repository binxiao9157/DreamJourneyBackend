#!/usr/bin/env bash
set -euo pipefail

# G0 integration only. This proves the independent Memorial boundary modules
# compose fail-closed; it does not introduce a shared runtime or writer.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
"$PYTHON_BIN" -m unittest \
  tests.test_owner_truth_memorial_authority_admission_shadow \
  tests.test_owner_truth_memorial_controller_appointment_shadow \
  tests.test_owner_truth_memorial_controller_review_shadow \
  tests.test_owner_truth_memorial_capability_non_admission_shadow \
  tests.test_owner_truth_memorial_conflict_hold_shadow \
  tests.test_owner_truth_memorial_family_contribution_shadow \
  tests.test_owner_truth_memorial_authority_composite_gate
"$PYTHON_BIN" -m py_compile \
  app/services/owner_truth_memorial_authority_admission_shadow.py \
  app/services/owner_truth_memorial_controller_appointment_shadow.py \
  app/services/owner_truth_memorial_controller_review_shadow.py \
  app/services/owner_truth_memorial_capability_non_admission_shadow.py \
  app/services/owner_truth_memorial_conflict_hold_shadow.py \
  app/services/owner_truth_memorial_family_contribution_shadow.py

"$PYTHON_BIN" - <<'PY'
import ast
from pathlib import Path

paths = [
    Path("app/services/owner_truth_memorial_authority_admission_shadow.py"),
    Path("app/services/owner_truth_memorial_controller_appointment_shadow.py"),
    Path("app/services/owner_truth_memorial_controller_review_shadow.py"),
    Path("app/services/owner_truth_memorial_capability_non_admission_shadow.py"),
    Path("app/services/owner_truth_memorial_conflict_hold_shadow.py"),
    Path("app/services/owner_truth_memorial_family_contribution_shadow.py"),
]
for path in paths:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module = getattr(node, "module", "") or ""
            assert not module.startswith("app.main"), f"{path} must not import API routes"
            assert not module.startswith("app.async_effects"), f"{path} must not import effects"
            assert not module.startswith("app.services.postgres_store"), f"{path} must not import persistence"
            assert not module.startswith("app.services.in_memory_store"), f"{path} must not import persistence"
    for forbidden in ("subprocess", "requests", "httpx", "psycopg", "sqlalchemy"):
        assert forbidden not in source, f"{path} must not depend on {forbidden}"

print("owner truth memorial authority composite static gate passed modules=6")
PY
