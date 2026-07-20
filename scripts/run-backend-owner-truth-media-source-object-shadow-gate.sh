#!/usr/bin/env bash
set -euo pipefail

# G0-only. This validates a default-off, value-free SourceObject inventory.
# It must not create object authority, upload bytes, call storage/provider
# services, enqueue effects, or write ExtractionResult/Candidate/Memory state.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

python3 -m unittest \
  tests.test_owner_truth_media_source_object_shadow \
  tests.test_owner_truth_candidate_extraction
python3 -m py_compile app/services/owner_truth_media_source_object_shadow.py

python3 - <<'PY'
import ast
from pathlib import Path

path = Path("app/services/owner_truth_media_source_object_shadow.py")
source = path.read_text(encoding="utf-8")
tree = ast.parse(source)

for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        module = getattr(node, "module", "") or ""
        assert not module.startswith("app.async_effects"), "G0 inventory must not import the effect kernel"
        assert not module.startswith("app.services.postgres_store"), "G0 inventory must not import persistence"
        assert not module.startswith("app.services.owner_truth_candidate_extraction"), "G0 inventory must not persist candidates"
    if isinstance(node, ast.Call):
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        assert name not in {"accept", "persist", "request", "upload", "put", "delete", "dispatch"}, (
            "G0 inventory must remain side-effect free"
        )

function = next(
    node
    for node in tree.body
    if isinstance(node, ast.FunctionDef) and node.name == "build_media_source_object_admission_shadow"
)
body = ast.get_source_segment(source, function) or ""
assert "if not enabled:" in body, "inventory must default off before input parsing"
assert "MEDIA_SOURCE_OBJECT_PROTOCOL_VERSION" in body, "inventory must reject legacy/mock envelopes"
assert "sourceObjectCreated\": False" in source
assert "candidateProposalPerformed\": False" in source
print("Owner Truth media SourceObject admission shadow G0 contract gate passed")
PY
