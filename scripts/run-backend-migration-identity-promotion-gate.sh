#!/usr/bin/env bash
set -euo pipefail

# G0-only. This validates a pure, value-minimized identity-promotion preflight.
# It must not import API routes, stores, effects, providers, or perform a claim,
# session issue, route change, data migration, or Visitor enumeration.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
"$PYTHON_BIN" -m unittest tests.test_identity_promotion_preflight_shadow
"$PYTHON_BIN" -m py_compile app/services/identity_promotion_preflight_shadow.py

"$PYTHON_BIN" - <<'PY'
import ast
from pathlib import Path

from app.services.identity_promotion_preflight_shadow import (
    IDENTITY_PROMOTION_PREFLIGHT_SHADOW_SCHEMA_VERSION,
)

assert IDENTITY_PROMOTION_PREFLIGHT_SHADOW_SCHEMA_VERSION == "identity-promotion-preflight-shadow-v1"

path = Path("app/services/identity_promotion_preflight_shadow.py")
source = path.read_text(encoding="utf-8")
tree = ast.parse(source)

for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        module = getattr(node, "module", "") or ""
        assert not module.startswith("app.main"), "identity preflight must not import API routes"
        assert not module.startswith("app.async_effects"), "identity preflight must not import effects"
        assert not module.startswith("app.services.postgres_store"), "identity preflight must not import persistence"
        assert not module.startswith("app.services.in_memory_store"), "identity preflight must not import persistence"

for forbidden in ("subprocess", "requests", "httpx", "psycopg", "sqlalchemy"):
    assert forbidden not in source, f"identity preflight must not depend on {forbidden}"

for required in (
    '"promotionWritten": self.promotion_written',
    '"aliasClaimCommitted": self.alias_claim_committed',
    '"sessionIssued": self.session_issued',
    '"routePolicyChanged": self.route_policy_changed',
    '"visitorEnumerationAllowed": self.visitor_enumeration_allowed',
    '"separateG1G2G4ApprovalRequired"',
):
    assert required in source, f"missing identity promotion G0 invariant: {required}"

print("migration identity promotion preflight G0 gate passed")
PY
