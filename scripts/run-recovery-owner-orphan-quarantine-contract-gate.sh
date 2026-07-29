#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "$ROOT_DIR"

"$PYTHON_BIN" -m unittest tests.test_recovery_owner_orphan_quarantine
"$PYTHON_BIN" -m py_compile \
  app/db/recovery_owner_orphan_quarantine.py \
  scripts/db/build_recovery_owner_orphan_quarantine_manifest.py

"$PYTHON_BIN" - <<'PY'
from pathlib import Path

source = Path("scripts/db/build_recovery_owner_orphan_quarantine_manifest.py").read_text(
    encoding="utf-8"
)
upper_source = source.upper()

assert "SET TRANSACTION READ ONLY" in source
assert "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ" in source
assert "SELECT current_database()" in source
assert "validate_recovery_target" in source
assert "conninfo_to_dict" in source
assert "write_recovery_record_atomic" in source
assert "--redaction-key-file" in source
assert "if output_directory.exists()" in source
for forbidden in (
    "INSERT ",
    "UPDATE ",
    "DELETE ",
    "CREATE ",
    "DROP ",
    "ALTER ",
    "TRUNCATE ",
):
    assert forbidden not in upper_source, forbidden
print("Recovery owner-orphan quarantine contract gate passed")
PY
