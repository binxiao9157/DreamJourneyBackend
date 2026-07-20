#!/usr/bin/env bash
set -euo pipefail

# G0-only. This validates value-free legacy media classification. It does not
# read bytes, call storage, create objects, send effects, or write authority.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

python3 -m unittest \
  tests.test_owner_truth_media_source_object_shadow \
  tests.test_owner_truth_media_source_object_commit_shadow \
  tests.test_owner_truth_legacy_media_non_promotion_shadow
python3 -m py_compile \
  app/services/owner_truth_media_source_object_shadow.py \
  app/services/owner_truth_media_source_object_commit_shadow.py \
  app/services/owner_truth_legacy_media_non_promotion_shadow.py

python3 - <<'PY'
import ast
from pathlib import Path

path = Path("app/services/owner_truth_legacy_media_non_promotion_shadow.py")
source = path.read_text(encoding="utf-8")
tree = ast.parse(source)

for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        module = getattr(node, "module", "") or ""
        assert not module.startswith("app.main"), "legacy inventory must not import API routes"
        assert not module.startswith("app.async_effects"), "legacy inventory must not import effect kernels"
        assert not module.startswith("app.services.postgres_store"), "legacy inventory must not import persistence"
    if isinstance(node, ast.Call):
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        assert name not in {"accept", "delete", "dispatch", "head", "persist", "put", "request", "upload"}, (
            "legacy inventory must remain side-effect free"
        )

function = next(
    node
    for node in tree.body
    if isinstance(node, ast.FunctionDef)
    and node.name == "inventory_legacy_archive_media_non_promotion"
)
body = ast.get_source_segment(source, function) or ""
assert "if not enabled:" in body, "legacy inventory must be default-off before parsing"
assert "would_be_verified_source_object" in source
assert '"legacyMediaPromoted": False' in source
assert '"sourceObjectCreated": False' in source
assert '"objectStorageOperationPerformed": False' in source
assert '"processorAdmissionPerformed": False' in source
print("Owner Truth legacy Archive media non-promotion G0 contract gate passed")
PY
