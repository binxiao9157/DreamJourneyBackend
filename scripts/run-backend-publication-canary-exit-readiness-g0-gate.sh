#!/usr/bin/env bash
set -euo pipefail

# WI-S3-01-09 G0 only. It must not enroll an adult cohort, create public
# access, issue a Visitor session, dispatch an incident, remove data, call a
# provider, register a route or claim external/product approval.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
PYTHONPATH=. "$PYTHON_BIN" -m unittest tests.test_publication_canary_exit_readiness
PYTHONPATH=. "$PYTHON_BIN" -m py_compile app/domain/publication/canary_exit_readiness.py

"$PYTHON_BIN" - <<'PY'
import ast
import json
from pathlib import Path

import app.main as main_module
from app.domain.publication.canary_exit_readiness import (
    PUBLICATION_CANARY_EXIT_READINESS_G0_SCHEMA_VERSION,
    PublicationCanaryDecision,
    PublicationCanaryExitDisposition,
    evaluate_publication_canary_exit_readiness,
)

assert PUBLICATION_CANARY_EXIT_READINESS_G0_SCHEMA_VERSION == "publication-canary-exit-readiness-g0-v1"
disabled = evaluate_publication_canary_exit_readiness(
    context=object(), principal=object(), request=object()
)
assert disabled.disposition is PublicationCanaryExitDisposition.SHADOW_DISABLED
assert disabled.decision is PublicationCanaryDecision.NO_GO
assert disabled.cohort_enrolled is False
assert disabled.public_access_enabled is False
assert disabled.incident_dispatched is False
assert disabled.rights_exit_executed is False
assert disabled.regulatory_exit_approved is False

for route in main_module.app.routes:
    raw_path = str(getattr(route, "path", ""))
    if raw_path.startswith(("/v2/internal/owner-authority/", "/v2/internal/publication-access/")):
        assert getattr(route, "include_in_schema", True) is False
        continue
    path = raw_path.lower()
    for forbidden in ("publication", "visitor", "guest", "public"):
        assert forbidden not in path, f"G0 must not register a public/Visitor route: {path}"

source_path = Path("app/domain/publication/canary_exit_readiness.py")
source = source_path.read_text(encoding="utf-8")
tree = ast.parse(source)
for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        module = getattr(node, "module", "") or ""
        assert not module.startswith("app.main"), "G0 must not import API routes"
        assert not module.startswith("app.services"), "G0 must not import persistence layers"
        assert not module.startswith("app.async_effects"), "G0 must not dispatch effects"
        assert not module.startswith("app.domain.owner_truth"), "G0 must not read private Owner Truth"
    if isinstance(node, ast.Call):
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        assert name not in {"insert", "update", "delete", "persist", "enqueue", "request", "post", "put"}, (
            "G0 must remain contract-only"
        )

sql = Path("db/migrations/0057_publication_canary_exit_readiness.sql").read_text(encoding="utf-8").lower()
for required in (
    "create table publication.canary_decision_candidates",
    "create table publication.incident_exit_candidates",
    "revoke all on table publication.canary_decision_candidates from public;",
    "revoke all on table publication.incident_exit_candidates from public;",
    "decision in ('nogo', 'pause')",
):
    assert required in sql, f"missing Publication canary schema boundary: {required}"
for forbidden in (
    "content_body",
    "conversation_body",
    "source_payload",
    "object_url",
    "preview_url",
    "raw_identity",
    "search_text",
    "visitor_subject_hash",
):
    assert forbidden not in sql, f"G0 canary schema must not retain private data: {forbidden}"

manifest = json.loads(
    Path("db/migrations/0057_publication_canary_exit_readiness.json").read_text(encoding="utf-8")
)
assert manifest["releaseFlags"] == {
    "publicationAdultCanaryV1": False,
    "publicationRegulatoryExitV1": False,
    "publicationVisitorReleaseV1": False,
}

print("Publication canary/exit-readiness G0 contract gate passed")
PY
