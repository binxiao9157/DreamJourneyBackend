#!/usr/bin/env bash
set -euo pipefail

# WI-S3-01-01 policy guard. It protects a value-free, default-deny release
# contract. Later additive schema work may exist, but this gate must still fail
# if a public content route, grant/session writer, index, provider call, or
# client-visible entry becomes reachable.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
"$PYTHON_BIN" -m unittest tests.test_release_policy
"$PYTHON_BIN" -m py_compile app/services/release_policy.py

"$PYTHON_BIN" - <<'PY'
from pathlib import Path

import app.main as main_module
from app.services.release_policy import ReleasePolicyService

service = ReleasePolicyService()
policy = service.publication_visitor_policy()

assert policy.policyVersion == "publication-visitor-policy-v1"
assert policy.status == "externalBlocked"
assert policy.publication.enabled is False
assert policy.visitor.enabled is False
assert policy.visitor.sessionTTLSeconds == 7 * 24 * 60 * 60
assert policy.visitor.offlineAccessMode == "deny"
assert policy.publication.allowedContent == ()
assert service.command_mode_for("publication") == "enforce"
assert service.command_mode_for("visitorAccess") == "enforce"

internal_qa_prefixes = (
    "/v2/internal/owner-authority/",
    "/v2/internal/publication-access/",
    "/v2/internal/publication-lifecycle/",
)

for route in main_module.app.routes:
    path = str(getattr(route, "path", ""))
    if path.startswith(internal_qa_prefixes):
        assert getattr(route, "include_in_schema", True) is False
        continue
    normalized_path = path.lower()
    for forbidden_route_term in ("publication", "visitor", "share", "guest", "public", "index"):
        assert forbidden_route_term not in normalized_path, (
            f"G0 must not add a public-access route containing {forbidden_route_term}"
        )

# The runtime route scan above is deliberately authoritative. A working tree
# may contain unrelated, authenticated product routes; rejecting every new
# `app/main.py` route made this policy-only G0 gate unusable outside its own
# original slice. It still fails if any registered public/visitor route exists.

source = Path("app/services/release_policy.py").read_text(encoding="utf-8")
for forbidden in (
    "app.domain.publication",
    "app.services.publication",
    "app.services.postgres_store",
    "app.async_effects",
    "requests",
    "httpx",
    "sqlalchemy",
):
    assert forbidden not in source, f"publication/visitor G0 policy must not depend on {forbidden}"

print("publication visitor default-deny G0 gate passed")
PY
