#!/usr/bin/env bash
set -euo pipefail

# G0-only. This checks synthetic intent/commit validation. It does not issue
# URLs, touch object storage, perform HEAD, create objects, or call a worker.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

python3 -m unittest \
  tests.test_owner_truth_media_source_object_shadow \
  tests.test_owner_truth_media_source_object_commit_shadow
python3 -m py_compile \
  app/services/owner_truth_media_source_object_shadow.py \
  app/services/owner_truth_media_source_object_commit_shadow.py

python3 - <<'PY'
import ast
from pathlib import Path

path = Path("app/services/owner_truth_media_source_object_commit_shadow.py")
source = path.read_text(encoding="utf-8")
tree = ast.parse(source)

for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        module = getattr(node, "module", "") or ""
        assert not module.startswith("app.main"), "commit shadow must not import API routes"
        assert not module.startswith("app.async_effects"), "commit shadow must not import the effect kernel"
        assert not module.startswith("app.services.postgres_store"), "commit shadow must not import persistence"
    if isinstance(node, ast.Call):
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        assert name not in {"accept", "persist", "request", "upload", "put", "delete", "dispatch"}, (
            "commit shadow must remain side-effect free"
        )

function = next(
    node
    for node in tree.body
    if isinstance(node, ast.FunctionDef) and node.name == "build_media_source_object_intent_commit_shadow"
)
body = ast.get_source_segment(source, function) or ""
assert "if not enabled:" in body, "commit shadow must default off before input parsing"
assert "WOULD_COMMIT_QUARANTINED" in body, "commit shadow must stop before verified state"
assert "sourceObjectCreated\": False" in source
assert "objectStorageOperationPerformed\": False" in source
assert "providerHeadPerformed\": False" in source
print("Owner Truth media SourceObject intent/commit shadow G0 contract gate passed")
PY
