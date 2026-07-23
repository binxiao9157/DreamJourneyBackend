#!/usr/bin/env bash
set -euo pipefail

# WI-S3-01-01 G0 only. This gate protects a value-free, default-deny policy
# contract. It must not create a public content route, grant, session, index,
# database table, provider call, or client-visible entry.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
"$PYTHON_BIN" -m unittest tests.test_release_policy
"$PYTHON_BIN" -m py_compile app/services/release_policy.py

"$PYTHON_BIN" - <<'PY'
import re
import subprocess
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

for route in main_module.app.routes:
    path = getattr(route, "path", "")
    normalized_path = path.lower()
    for forbidden_route_term in ("publication", "visitor", "share", "guest", "public", "index"):
        assert forbidden_route_term not in normalized_path, (
            f"G0 must not add a public-access route containing {forbidden_route_term}"
        )

# This gate previously rejected every unrelated app/main.py change in a dirty
# worktree.  The actual isolation requirement is narrower: this G0 policy must
# not add a public route.  Inspect added route registrations instead, while the
# runtime route scan above remains the primary assertion.
main_diff = subprocess.check_output(
    ["git", "diff", "--unified=0", "HEAD", "--", "app/main.py"],
    text=True,
)
added_route_registration = re.compile(
    r"^\+\s*(?:@app\.(?:get|post|put|patch|delete|api_route)|app\.add_api_route\b)"
)
assert not any(added_route_registration.match(line) for line in main_diff.splitlines()), (
    "WI-S3-01-01 G0 must not add a route in app/main.py"
)

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
