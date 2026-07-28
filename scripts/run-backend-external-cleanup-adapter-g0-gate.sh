#!/usr/bin/env bash
set -euo pipefail

# G0-only. This inventory classifies external cleanup boundaries and must not
# query/delete/close external resources, mutate retention or persist receipts.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
PYTHONPATH=. "$PYTHON_BIN" -m unittest tests.test_external_cleanup_adapter_shadow
PYTHONPATH=. "$PYTHON_BIN" -m py_compile app/services/external_cleanup_adapter_shadow.py

"$PYTHON_BIN" - <<'PY'
import ast
from pathlib import Path

from app.services.external_cleanup_adapter_shadow import (
    EXTERNAL_CLEANUP_ADAPTER_SHADOW_SCHEMA_VERSION,
    ExternalCleanupLayer,
    current_external_cleanup_adapter_inventory,
    plan_external_cleanup_adapter_shadow,
)

assert EXTERNAL_CLEANUP_ADAPTER_SHADOW_SCHEMA_VERSION == "external-cleanup-adapter-shadow-v1"
assert {item.layer for item in current_external_cleanup_adapter_inventory()} == set(ExternalCleanupLayer)
summary = plan_external_cleanup_adapter_shadow(
    current_external_cleanup_adapter_inventory(), enabled=True
).value_free_summary()
assert summary["externalCleanupPerformed"] is False
assert summary["providerCallPerformed"] is False
assert summary["receiptPersisted"] is False
assert summary["retentionChanged"] is False
assert "completed" not in {item["status"] for item in summary["surfaces"]}

path = Path("app/services/external_cleanup_adapter_shadow.py")
source = path.read_text(encoding="utf-8")
tree = ast.parse(source)

for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        module = getattr(node, "module", "") or ""
        assert not module.startswith("app.main"), "G0 must not import API routes"
        assert not module.startswith("app.services.in_memory_store"), "G0 must not import persistence"
        assert not module.startswith("app.services.postgres_store"), "G0 must not import persistence"
        assert not module.startswith("app.async_effects"), "G0 must not dispatch effects"
    if isinstance(node, ast.Call):
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        assert name not in {
            "delete", "remove", "revoke", "persist", "enqueue", "request", "post", "put", "close"
        }, "G0 must remain observation-only"

for forbidden in ("requests", "httpx", "boto3", "psycopg", "subprocess", "socket"):
    assert forbidden not in source, f"G0 must not depend on {forbidden}"

print("External cleanup adapter G0 contract gate passed")
PY
