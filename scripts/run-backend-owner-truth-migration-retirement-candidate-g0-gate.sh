#!/usr/bin/env bash
set -euo pipefail

# G0-only. C10 may observe opaque evidence for one retirement candidate but
# cannot read live counters, drain work, remove code, revoke credentials, or
# authorize C11.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_owner_truth_migration_retirement_candidate_shadow
PYTHONPATH=. "$PYTHON_BIN" -m py_compile \
  app/domain/owner_truth/migration_retirement_candidate_shadow.py

"$PYTHON_BIN" - <<'PY'
import ast
from pathlib import Path

from app.domain.owner_truth.migration_retirement_candidate_shadow import (
    OWNER_TRUTH_MIGRATION_RETIREMENT_CANDIDATE_SHADOW_SCHEMA_VERSION,
    RetirementCandidateLifecycleState,
    RetirementSurfaceKind,
)

assert OWNER_TRUTH_MIGRATION_RETIREMENT_CANDIDATE_SHADOW_SCHEMA_VERSION == (
    "owner-truth-migration-retirement-candidate-shadow-v1"
)
assert set(RetirementCandidateLifecycleState) == {
    RetirementCandidateLifecycleState.DISCOVERED,
    RetirementCandidateLifecycleState.DRAINING,
    RetirementCandidateLifecycleState.ZERO_USE_OBSERVED,
    RetirementCandidateLifecycleState.CANDIDATE_APPROVED,
    RetirementCandidateLifecycleState.REOPENED,
}
assert {
    RetirementSurfaceKind.RIGHTS_ROUTE,
    RetirementSurfaceKind.RECONCILIATION_ROUTE,
}.issubset(set(RetirementSurfaceKind))

path = Path("app/domain/owner_truth/migration_retirement_candidate_shadow.py")
source = path.read_text(encoding="utf-8")
tree = ast.parse(source)

for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        module = getattr(node, "module", "") or ""
        assert not module.startswith("app.main"), "C10 must not import API routes"
        assert not module.startswith("app.async_effects"), "C10 must not dispatch effects"
        assert not module.startswith("app.services"), "C10 must not import service operations"
    if isinstance(node, ast.Call):
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        assert name not in {
            "request", "post", "put", "delete", "persist", "enqueue", "revoke", "remove"
        }, "C10 must remain observation-only"

for forbidden in ("subprocess", "requests", "httpx", "psycopg", "socket"):
    assert forbidden not in source, f"C10 must not depend on {forbidden}"

for required in (
    '"candidateApprovalAllowed": self.candidate_approval_allowed',
    '"credentialRevoked": self.credential_revoked',
    '"implementationDeleted": self.legacy_implementation_deleted',
    '"liveRuntimeCounterRead": self.live_runtime_counter_read',
    'summary["approverReferenceHashes"] = list(self.approver_hashes)',
    '"zeroUseWindowReset"',
    '"protectedRightsOrReconcileSurface"',
):
    assert required in source, f"missing C10 retirement fence: {required}"

print("Owner Truth migration C10 retirement-candidate G0 gate passed")
PY
