#!/usr/bin/env bash
set -euo pipefail

# WI-S3-01-07 G0 only. It must not mutate Publication state, revoke a live
# grant/session, clear cache/index, call an external cleanup provider, persist
# a receipt, or expose a public/Visitor route.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
PYTHONPATH=. "$PYTHON_BIN" -m unittest tests.test_publication_lifecycle_propagation
PYTHONPATH=. "$PYTHON_BIN" -m py_compile app/domain/publication/lifecycle_propagation.py

"$PYTHON_BIN" - <<'PY'
import ast
import json
from pathlib import Path

import app.main as main_module
from app.domain.publication.lifecycle_propagation import (
    PUBLICATION_LIFECYCLE_PROPAGATION_G0_SCHEMA_VERSION,
    PublicationLifecycleDisposition,
    evaluate_publication_lifecycle_propagation,
)

assert PUBLICATION_LIFECYCLE_PROPAGATION_G0_SCHEMA_VERSION == "publication-lifecycle-propagation-g0-v1"
disabled = evaluate_publication_lifecycle_propagation(
    context=object(), principal=object(), command=object()
)
assert disabled.disposition is PublicationLifecycleDisposition.SHADOW_DISABLED
assert disabled.publication_mutated is False
assert disabled.grant_revoked is False
assert disabled.visitor_session_closed is False
assert disabled.gateway_access_denied is False
assert disabled.index_or_cache_cleared is False
assert disabled.external_cleanup_performed is False
assert disabled.propagation_receipt_persisted is False

for route in main_module.app.routes:
    raw_path = str(getattr(route, "path", ""))
    if raw_path.startswith(("/v2/internal/owner-authority/", "/v2/internal/publication-access/")):
        assert getattr(route, "include_in_schema", True) is False
        continue
    path = raw_path.lower()
    for forbidden in ("publication", "visitor", "guest", "public"):
        assert forbidden not in path, f"G0 must not register a public/Visitor route: {path}"

source_path = Path("app/domain/publication/lifecycle_propagation.py")
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

sql = Path("db/migrations/0055_publication_lifecycle_propagation.sql").read_text(encoding="utf-8").lower()
for required in (
    "create table publication.lifecycle_transition_receipts",
    "create table publication.propagation_cleanup_candidates",
    "revoke all on table publication.lifecycle_transition_receipts from public;",
    "revoke all on table publication.propagation_cleanup_candidates from public;",
    "last_transition_sequence",
):
    assert required in sql, f"missing lifecycle propagation schema boundary: {required}"
for forbidden in ("content_body", "source_payload", "object_url", "preview_url", "search_text"):
    assert forbidden not in sql, f"G0 lifecycle schema must not retain readable payloads: {forbidden}"

manifest = json.loads(
    Path("db/migrations/0055_publication_lifecycle_propagation.json").read_text(encoding="utf-8")
)
assert manifest["releaseFlags"] == {
    "publicationExternalCleanupV1": False,
    "publicationLifecycleCommandV1": False,
    "publicationRevokePropagationV1": False,
}

print("Publication lifecycle/propagation G0 contract gate passed")
PY
