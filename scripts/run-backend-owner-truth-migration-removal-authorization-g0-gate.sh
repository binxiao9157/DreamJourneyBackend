#!/usr/bin/env bash
set -euo pipefail

# G0-only. C11 authorization planning cannot migrate a contract, remove a
# legacy asset, revoke a credential, or begin post-monitoring.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_owner_truth_migration_removal_authorization_shadow
PYTHONPATH=. "$PYTHON_BIN" -m py_compile \
  app/domain/owner_truth/migration_removal_authorization_shadow.py

"$PYTHON_BIN" - <<'PY'
import ast
from pathlib import Path

from app.domain.owner_truth.migration_removal_authorization_shadow import (
    OWNER_TRUTH_MIGRATION_REMOVAL_AUTHORIZATION_SHADOW_SCHEMA_VERSION,
    RemovalAuthorizationPhase,
)

assert OWNER_TRUTH_MIGRATION_REMOVAL_AUTHORIZATION_SHADOW_SCHEMA_VERSION == (
    "owner-truth-migration-removal-authorization-shadow-v1"
)
assert set(RemovalAuthorizationPhase) == {
    RemovalAuthorizationPhase.AUTHORIZATION,
    RemovalAuthorizationPhase.COMPLETION,
    RemovalAuthorizationPhase.REOPENED,
}

path = Path("app/domain/owner_truth/migration_removal_authorization_shadow.py")
source = path.read_text(encoding="utf-8")
tree = ast.parse(source)

for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        module = getattr(node, "module", "") or ""
        assert not module.startswith("app.main"), "C11 must not import API routes"
        assert not module.startswith("app.async_effects"), "C11 must not dispatch effects"
        assert not module.startswith("app.services"), "C11 must not import service operations"
    if isinstance(node, ast.Call):
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        assert name not in {
            "request", "post", "put", "delete", "persist", "enqueue", "revoke", "remove"
        }, "C11 must remain planning-only"

for forbidden in ("subprocess", "requests", "httpx", "psycopg", "socket"):
    assert forbidden not in source, f"C11 must not depend on {forbidden}"

for required in (
    '"contractMigrated": self.contract_migrated',
    '"credentialRevoked": self.credential_revoked',
    '"legacyArtifactRemoved": self.legacy_artifact_removed',
    '"postMonitorStarted": self.post_monitor_started',
    '"removalExecutionAllowed": self.removal_execution_allowed',
    '"retirementCandidateNotApproved"',
    '"externalMaintenanceWindowRequired"',
):
    assert required in source, f"missing C11 authorization fence: {required}"

print("Owner Truth migration C11 removal-authorization G0 gate passed")
PY
