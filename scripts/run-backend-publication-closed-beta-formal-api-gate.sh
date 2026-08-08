#!/usr/bin/env bash
set -euo pipefail

# ND-R6-01: formal Publication/Visitor contracts are registered separately
# from internal QA routes, but remain fail-closed under the D0 release policy.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
STORE_BACKEND=memory PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_publication_closed_beta_formal_api \
  tests.test_publication_authority_api \
  tests.test_publication_visitor_access_api \
  tests.test_publication_lifecycle_api \
  tests.test_publication_management_read_api

PYTHONPATH=. "$PYTHON_BIN" -m py_compile \
  app/main.py \
  app/services/release_policy.py \
  app/services/route_ownership.py \
  tests/test_publication_closed_beta_formal_api.py

PYTHONPATH=. "$PYTHON_BIN" - <<'PY'
import app.main as main_module

formal_routes = main_module.FORMAL_PUBLICATION_CLOSED_BETA_ROUTE_TEMPLATES
registered = {
    str(getattr(route, "path", "")): route
    for route in main_module.app.routes
    if str(getattr(route, "path", "")) in formal_routes
}
assert set(registered) == formal_routes
assert all(not route.include_in_schema for route in registered.values())

service = main_module.RELEASE_POLICY_SERVICE
for feature in ("publication", "visitorAccess"):
    decision = service.build_snapshot(
        audience="owner" if feature == "publication" else "visitor",
        cohort="closedPilotAdultSelf",
        client_build=service.min_client_build,
        requested_feature=feature,
    ).features[0]
    assert decision.enabled is False
    assert decision.reason == "publicationVisitorNotApproved"

print("publication closed-beta formal API gate passed")
PY

bash -n scripts/run-backend-publication-closed-beta-formal-api-gate.sh
