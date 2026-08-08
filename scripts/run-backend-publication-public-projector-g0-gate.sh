#!/usr/bin/env bash
set -euo pipefail

# WI-S3-01-04 G0 only. This models a one-way, hash-only event boundary. It
# must not query private Owner Truth, write a public store, register a public
# route, invoke an index provider, copy an object, or create a visitor session.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
PYTHONPATH=. "$PYTHON_BIN" -m unittest tests.test_publication_public_projector
PYTHONPATH=. "$PYTHON_BIN" -m py_compile app/domain/publication/public_projector.py

"$PYTHON_BIN" - <<'PY'
import ast
import json
from pathlib import Path

import app.main as main_module
from app.domain.publication.public_projector import (
    PUBLICATION_PUBLIC_PROJECTOR_G0_SCHEMA_VERSION,
    PublicationProjectorDisposition,
    evaluate_publication_projector,
)

assert PUBLICATION_PUBLIC_PROJECTOR_G0_SCHEMA_VERSION == "publication-public-projector-g0-v1"
disabled = evaluate_publication_projector(checkpoint=object(), event=object())
assert disabled.disposition is PublicationProjectorDisposition.SHADOW_DISABLED
assert disabled.projection_write_allowed is False
assert disabled.public_query_allowed is False
assert disabled.external_index_allowed is False
assert disabled.object_copy_allowed is False

for route in main_module.app.routes:
    raw_path = str(getattr(route, "path", ""))
    if raw_path.startswith(("/v2/internal/owner-authority/", "/v2/internal/publication-access/", "/v2/internal/publication-lifecycle/")):
        assert getattr(route, "include_in_schema", True) is False
        continue
    if raw_path in main_module.FORMAL_PUBLICATION_CLOSED_BETA_ROUTE_TEMPLATES:
        assert getattr(route, "include_in_schema", True) is False
        continue
    path = raw_path.lower()
    for forbidden in ("publication", "visitor", "share", "guest", "public", "index"):
        assert forbidden not in path, f"G0 must not register a public-access route: {path}"

source_path = Path("app/domain/publication/public_projector.py")
source = source_path.read_text(encoding="utf-8")
tree = ast.parse(source)
for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        module = getattr(node, "module", "") or ""
        assert not module.startswith("app.main"), "G0 must not import API routes"
        assert not module.startswith("app.services"), "G0 must not import persistence layers"
        assert not module.startswith("app.async_effects"), "G0 must not dispatch effects"
        assert not module.startswith("app.domain.owner_truth"), "G0 must not query private Owner Truth"
    if isinstance(node, ast.Call):
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        assert name not in {"insert", "update", "delete", "persist", "enqueue", "request", "post", "put"}, (
            "G0 must remain contract-only"
        )

sql = Path("db/migrations/0052_publication_public_projector.sql").read_text(encoding="utf-8").lower()
for required in (
    "create table publication.projector_checkpoints",
    "create table publication.public_projection_candidates",
    "revoke all on table publication.projector_checkpoints from public;",
    "revoke all on table publication.public_projection_candidates from public;",
):
    assert required in sql, f"missing projector schema boundary: {required}"
for forbidden in ("content_body", "source_payload", "object_url", "preview_url", "search_text"):
    assert forbidden not in sql, f"G0 projector schema must not retain readable payloads: {forbidden}"

manifest = json.loads(
    Path("db/migrations/0052_publication_public_projector.json").read_text(encoding="utf-8")
)
assert manifest["releaseFlags"] == {
    "publicIndexProviderV1": False,
    "publicProjectorV1": False,
    "publicStoreQueryV1": False,
}

print("Publication public projector G0 contract gate passed")
PY
