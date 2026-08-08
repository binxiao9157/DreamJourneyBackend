#!/usr/bin/env bash
set -euo pipefail

# WI-S3-01-02 G0 only. The disabled publication schema and authorization
# contract must not create a public route, repository, gateway, projection,
# writer, grant, visitor session, Provider effect or public DTO.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
PYTHONPATH=. "$PYTHON_BIN" -m unittest tests.test_publication_schema_authz
PYTHONPATH=. "$PYTHON_BIN" -m py_compile app/domain/publication/schema_authz.py

"$PYTHON_BIN" - <<'PY'
import ast
import json
from pathlib import Path

import app.main as main_module
from app.domain.publication.schema_authz import (
    PUBLICATION_SCHEMA_AUTHZ_G0_SCHEMA_VERSION,
    PublicationAccessAction,
    PublicationAuthorizationContext,
    PublicationAuthorizationPrincipal,
    PublicationDataPlane,
    PublicationPrincipalKind,
    evaluate_publication_schema_authz,
)

assert PUBLICATION_SCHEMA_AUTHZ_G0_SCHEMA_VERSION == "publication-schema-authz-g0-v1"
owner_hash = "a" * 64
summary = evaluate_publication_schema_authz(
    context=PublicationAuthorizationContext(
        vault_id="vault-publication-g0",
        owner_subject_hash=owner_hash,
        authority_epoch=0,
        policy_version="publication-visitor-policy-v1",
    ),
    principal=PublicationAuthorizationPrincipal(
        kind=PublicationPrincipalKind.OWNER,
        vault_id="vault-publication-g0",
        subject_hash=owner_hash,
    ),
    data_plane=PublicationDataPlane.PRIVATE_AUTHORITY,
    action=PublicationAccessAction.PUBLICATION_WRITE,
    enabled=True,
).value_free_summary()
assert summary["privateAuthorityReadAllowed"] is False
assert summary["publicationWriterAllowed"] is False
assert summary["publicStoreReadAllowed"] is False
assert summary["shareGrantIssued"] is False
assert summary["visitorSessionIssued"] is False

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

source_path = Path("app/domain/publication/schema_authz.py")
source = source_path.read_text(encoding="utf-8")
tree = ast.parse(source)
for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        module = getattr(node, "module", "") or ""
        assert not module.startswith("app.main"), "G0 must not import API routes"
        assert not module.startswith("app.services"), "G0 must not import service or persistence layers"
        assert not module.startswith("app.async_effects"), "G0 must not dispatch effects"
    if isinstance(node, ast.Call):
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        assert name not in {"insert", "update", "delete", "persist", "enqueue", "request", "post", "put"}, (
            "G0 must remain contract-only"
        )

manifest = json.loads(
    Path("db/migrations/0050_publication_visitor_schema.json").read_text(encoding="utf-8")
)
assert manifest["releaseFlags"] == {
    "publicationSchemaV1": False,
    "publicationWriterV1": False,
    "visitorGatewayV1": False,
}

print("Publication schema/AuthZ G0 contract gate passed")
PY
