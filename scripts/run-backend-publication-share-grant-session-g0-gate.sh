#!/usr/bin/env bash
set -euo pipefail

# WI-S3-01-05 G0 only. This is a value-minimized, default-deny authorization
# contract. It must not issue credentials, persist a session, consume a grant,
# register a Visitor/public route, or query a public/private content store.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
PYTHONPATH=. "$PYTHON_BIN" -m unittest tests.test_publication_share_grant_session
PYTHONPATH=. "$PYTHON_BIN" -m py_compile app/domain/publication/share_grant_session.py

"$PYTHON_BIN" - <<'PY'
import ast
import json
from pathlib import Path

import app.main as main_module
from app.domain.publication.share_grant_session import (
    PUBLICATION_SHARE_GRANT_SESSION_G0_SCHEMA_VERSION,
    PublicationShareGrantSessionDisposition,
    evaluate_publication_share_grant_session,
)

assert PUBLICATION_SHARE_GRANT_SESSION_G0_SCHEMA_VERSION == "publication-share-grant-session-g0-v1"
disabled = evaluate_publication_share_grant_session(
    owner_context=object(),
    owner_principal=object(),
    grant=object(),
    visitor=object(),
    command=object(),
    session=object(),
)
assert disabled.disposition is PublicationShareGrantSessionDisposition.SHADOW_DISABLED
assert disabled.grant_issued is False
assert disabled.grant_revoked is False
assert disabled.visitor_session_issued is False
assert disabled.public_query_allowed is False
assert disabled.use_consumed is False

for route in main_module.app.routes:
    raw_path = str(getattr(route, "path", ""))
    if raw_path.startswith(("/v2/internal/owner-authority/", "/v2/internal/publication-access/", "/v2/internal/publication-lifecycle/")):
        assert getattr(route, "include_in_schema", True) is False
        continue
    path = raw_path.lower()
    for forbidden in ("publication", "visitor", "guest", "public"):
        assert forbidden not in path, f"G0 must not register a Visitor/public route: {path}"

source_path = Path("app/domain/publication/share_grant_session.py")
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

sql = Path("db/migrations/0053_publication_share_grant_session_metadata.sql").read_text(encoding="utf-8").lower()
for required in (
    "create table publication.share_grant_authorization_receipts",
    "revoke all on table publication.share_grant_authorization_receipts from public;",
    "adult_verification_state",
    "relationship_origin",
    "expected_grant_use_count",
):
    assert required in sql, f"missing share grant/session schema boundary: {required}"
for forbidden in ("raw_credential", "bearer_value", "source_payload", "object_url", "visitor_body"):
    assert forbidden not in sql, f"G0 share/session schema must not retain: {forbidden}"

manifest = json.loads(
    Path("db/migrations/0053_publication_share_grant_session_metadata.json").read_text(encoding="utf-8")
)
assert manifest["releaseFlags"] == {
    "publicGatewayV1": False,
    "shareGrantIssueV1": False,
    "visitorSessionV1": False,
}

print("Publication share grant/session G0 contract gate passed")
PY
