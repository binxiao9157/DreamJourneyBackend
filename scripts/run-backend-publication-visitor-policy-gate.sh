#!/usr/bin/env bash
set -euo pipefail

# Publication/Visitor release guard. Formal closed-beta routes may be
# registered, but they must remain user-authenticated, hidden from OpenAPI and
# denied by the server-owned D0 policy until a later release decision.
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
formal_closed_beta_routes = main_module.FORMAL_PUBLICATION_CLOSED_BETA_ROUTE_TEMPLATES
observed_formal_routes = set()

for route in main_module.app.routes:
    path = str(getattr(route, "path", ""))
    if path.startswith(internal_qa_prefixes):
        assert getattr(route, "include_in_schema", True) is False
        continue
    if path in formal_closed_beta_routes:
        observed_formal_routes.add(path)
        assert getattr(route, "include_in_schema", True) is False
        for method in getattr(route, "methods", set()):
            match = main_module.ROUTE_AUTHENTICATION_POLICY.registry.match(method, path)
            assert match is not None
            assert match.rule.auth_mode.value == "user"
        continue
    normalized_path = path.lower()
    for forbidden_route_term in ("publication", "visitor", "share", "guest", "public", "index"):
        assert forbidden_route_term not in normalized_path, (
            f"G0 must not add a public-access route containing {forbidden_route_term}"
        )

assert observed_formal_routes == formal_closed_beta_routes

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
