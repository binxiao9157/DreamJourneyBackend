#!/usr/bin/env bash
set -euo pipefail

# G0-only. This validates a default-off, value-free media processor plan. It
# must not read bytes, call a provider, enqueue a job, or write results,
# Candidates, Memory, Persona, or object authority.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

python3 -m unittest \
  tests.test_owner_truth_media_source_object_shadow \
  tests.test_owner_truth_verified_media_processor_shadow \
  tests.test_owner_truth_candidate_extraction
python3 -m py_compile \
  app/services/owner_truth_media_source_object_shadow.py \
  app/services/owner_truth_verified_media_processor_shadow.py

python3 - <<'PY'
import ast
from pathlib import Path

path = Path("app/services/owner_truth_verified_media_processor_shadow.py")
source = path.read_text(encoding="utf-8")
tree = ast.parse(source)

for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        module = getattr(node, "module", "") or ""
        assert not module.startswith("app.main"), "processor shadow must not import API routes"
        assert not module.startswith("app.async_effects"), "processor shadow must not import the effect kernel"
        assert not module.startswith("app.services.postgres_store"), "processor shadow must not import persistence"
        assert not module.startswith("app.services.owner_truth_candidate_extraction"), (
            "processor shadow must not persist Candidates"
        )
    if isinstance(node, ast.Call):
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        assert name not in {"accept", "delete", "dispatch", "enqueue", "persist", "put", "request", "upload"}, (
            "processor shadow must remain side-effect free"
        )

function = next(
    node
    for node in tree.body
    if isinstance(node, ast.FunctionDef) and node.name == "plan_verified_media_processor_admission"
)
body = ast.get_source_segment(source, function) or ""
assert "if not enabled:" in body, "processor planner must default off before input parsing"
assert "build_media_source_object_admission_shadow" in source
assert '"sourceExtractionEnqueued": False' in source
assert '"candidateProposalPerformed": False' in source
assert '"confirmedMemoryWritten": False' in source
assert '"personaWritten": False' in source
assert "WOULD_QUERY_RECONCILE" in source
assert "WOULD_RETRY_SOURCE_EXTRACTION" in source
print("Verified media processor admission shadow G0 contract gate passed")
PY
