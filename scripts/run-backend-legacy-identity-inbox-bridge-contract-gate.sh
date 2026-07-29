#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "$ROOT_DIR"

"$PYTHON_BIN" -m unittest \
  tests.test_legacy_identity_inbox_bridge \
  tests.test_legacy_identity_inbox_bridge_migration_contract
"$PYTHON_BIN" -m py_compile \
  app/async_effects/legacy_identity_inbox_bridge.py \
  app/services/postgres_store.py

"$PYTHON_BIN" - <<'PY'
from pathlib import Path

source = Path("app/async_effects/legacy_identity_inbox_bridge.py").read_text(encoding="utf-8")
assert "family_relationships" not in source
assert "access_grants" not in source
assert "mailbox_letters" not in source
assert "POST /" not in source
print("Legacy identity inbox bridge contract gate passed")
PY
