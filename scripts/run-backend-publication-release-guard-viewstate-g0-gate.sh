#!/usr/bin/env bash
set -euo pipefail

# WI-S3-01-08 G0 only. It must not create an Owner/Visitor route, expose a
# release UI, issue a Visitor session, query a public store, persist metrics,
# or return private Source/Memory/Persona/Visitor content.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
PYTHONPATH=. "$PYTHON_BIN" -m unittest tests.test_publication_release_guard_viewstate
PYTHONPATH=. "$PYTHON_BIN" -m py_compile app/domain/publication/release_guard_viewstate.py

"$PYTHON_BIN" - <<'PY'
import ast
import json
from pathlib import Path

import app.main as main_module
from app.domain.publication.release_guard_viewstate import (
    PUBLICATION_RELEASE_GUARD_VIEWSTATE_G0_SCHEMA_VERSION,
    PublicationReleaseGuardDisposition,
    evaluate_publication_release_guard_viewstate,
)

assert PUBLICATION_RELEASE_GUARD_VIEWSTATE_G0_SCHEMA_VERSION == "publication-release-guard-viewstate-g0-v1"
disabled = evaluate_publication_release_guard_viewstate(
    context=object(), principal=object(), request=object()
)
assert disabled.disposition is PublicationReleaseGuardDisposition.SHADOW_DISABLED
assert disabled.owner_management_visible is False
assert disabled.visitor_feature_visible is False
assert disabled.visitor_session_accepted is False
assert disabled.aggregate_metrics_query_allowed is False
assert disabled.public_route_registered is False

for route in main_module.app.routes:
    path = str(getattr(route, "path", "")).lower()
    for forbidden in ("publication", "visitor", "guest", "public"):
        assert forbidden not in path, f"G0 must not register a public/Visitor route: {path}"

source_path = Path("app/domain/publication/release_guard_viewstate.py")
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

sql = Path("db/migrations/0056_publication_release_guard_viewstate.sql").read_text(encoding="utf-8").lower()
for required in (
    "create table publication.aggregate_metric_snapshots",
    "create table publication.release_guard_candidates",
    "revoke all on table publication.aggregate_metric_snapshots from public;",
    "revoke all on table publication.release_guard_candidates from public;",
    "minimum_sample_size",
):
    assert required in sql, f"missing release guard schema boundary: {required}"
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
    assert forbidden not in sql, f"G0 release guard schema must not retain private data: {forbidden}"

manifest = json.loads(
    Path("db/migrations/0056_publication_release_guard_viewstate.json").read_text(encoding="utf-8")
)
assert manifest["releaseFlags"] == {
    "publicationOwnerViewStateV1": False,
    "publicationReleaseGuardV1": False,
    "publicationVisitorFeatureV1": False,
}

print("Publication release-guard/ViewState G0 contract gate passed")
PY
