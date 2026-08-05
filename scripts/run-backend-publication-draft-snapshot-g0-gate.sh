#!/usr/bin/env bash
set -euo pipefail

# WI-S3-01-03 G0 only. The Owner Draft Snapshot is a hidden, hash-only,
# default-deny contract. It must not create a writer, outbox effect, public
# route, public projection, readable draft copy, visitor session or release UI.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
PYTHONPATH=. "$PYTHON_BIN" -m unittest tests.test_publication_draft_snapshot
PYTHONPATH=. "$PYTHON_BIN" -m py_compile app/domain/publication/draft_snapshot.py

"$PYTHON_BIN" - <<'PY'
import ast
import json
from pathlib import Path

import app.main as main_module
from app.domain.publication.draft_snapshot import (
    PUBLICATION_DRAFT_SNAPSHOT_G0_SCHEMA_VERSION,
    PublicationDraftDisposition,
    evaluate_publication_draft_snapshot,
)

assert PUBLICATION_DRAFT_SNAPSHOT_G0_SCHEMA_VERSION == "publication-draft-snapshot-g0-v1"
disabled = evaluate_publication_draft_snapshot(
    context=object(), principal=object(), snapshot=object(), confirmation=object()
)
assert disabled.disposition is PublicationDraftDisposition.SHADOW_DISABLED
assert disabled.draft_write_allowed is False
assert disabled.publication_version_created is False
assert disabled.receipt_created is False
assert disabled.outbox_enqueued is False

for route in main_module.app.routes:
    raw_path = str(getattr(route, "path", ""))
    if raw_path.startswith(("/v2/internal/owner-authority/", "/v2/internal/publication-access/", "/v2/internal/publication-lifecycle/")):
        assert getattr(route, "include_in_schema", True) is False
        continue
    path = raw_path.lower()
    for forbidden in ("publication", "visitor", "share", "guest", "public", "index"):
        assert forbidden not in path, f"G0 must not register a public-access route: {path}"

source_path = Path("app/domain/publication/draft_snapshot.py")
source = source_path.read_text(encoding="utf-8")
tree = ast.parse(source)
for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        module = getattr(node, "module", "") or ""
        assert not module.startswith("app.main"), "G0 must not import API routes"
        assert not module.startswith("app.services"), "G0 must not import persistence layers"
        assert not module.startswith("app.async_effects"), "G0 must not dispatch effects"
    if isinstance(node, ast.Call):
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        assert name not in {"insert", "update", "delete", "persist", "enqueue", "request", "post", "put"}, (
            "G0 must remain contract-only"
        )

sql = Path("db/migrations/0051_publication_draft_snapshot.sql").read_text(encoding="utf-8").lower()
for required in (
    "create table publication.publication_drafts",
    "create table publication.publication_draft_memory_versions",
    "revoke all on table publication.publication_drafts from public;",
    "revoke all on table publication.publication_draft_memory_versions from public;",
):
    assert required in sql, f"missing draft schema boundary: {required}"
for forbidden in ("content_body", "source_payload", "object_url", "preview_url", "draft_text"):
    assert forbidden not in sql, f"G0 draft schema must not retain readable payloads: {forbidden}"

manifest = json.loads(
    Path("db/migrations/0051_publication_draft_snapshot.json").read_text(encoding="utf-8")
)
assert manifest["releaseFlags"] == {
    "publicationDraftPreviewV1": False,
    "publicationDraftSnapshotV1": False,
    "publicationDraftWriterV1": False,
}

print("Publication draft snapshot G0 contract gate passed")
PY
