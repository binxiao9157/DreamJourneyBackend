#!/usr/bin/env bash
set -euo pipefail

# G0-only. A family relation cannot self-authorize an authority, query, high-risk
# capability or write. Future Source/Candidate contributions still need G2/G4.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
"$PYTHON_BIN" -m unittest \
  tests.test_owner_truth_memorial_family_contribution_shadow
"$PYTHON_BIN" -m py_compile \
  app/services/owner_truth_memorial_family_contribution_shadow.py

"$PYTHON_BIN" - <<'PY'
import ast
from pathlib import Path

from app.services.owner_truth_memorial_family_contribution_shadow import (
    MemorialFamilyContributionOperation,
)

assert {
    MemorialFamilyContributionOperation.SUBMIT_SOURCE,
    MemorialFamilyContributionOperation.SUBMIT_CANDIDATE,
    MemorialFamilyContributionOperation.WITHDRAW_OWN_CONTRIBUTION,
} == {
    item
    for item in MemorialFamilyContributionOperation
    if item.value in {"submit_source", "submit_candidate", "withdraw_own_contribution"}
}

path = Path("app/services/owner_truth_memorial_family_contribution_shadow.py")
source = path.read_text(encoding="utf-8")
tree = ast.parse(source)
for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        module = getattr(node, "module", "") or ""
        assert not module.startswith("app.main"), "Family contribution gate must not import API routes"
        assert not module.startswith("app.async_effects"), "Family contribution gate must not import effects"
        assert not module.startswith("app.services.postgres_store"), "Family contribution gate must not import persistence"
        assert not module.startswith("app.services.in_memory_store"), "Family contribution gate must not import persistence"

for forbidden in ("subprocess", "requests", "httpx", "psycopg", "sqlalchemy"):
    assert forbidden not in source, f"Family contribution gate must not depend on {forbidden}"

for required in (
    '"contributionAdmitted": self.contribution_admitted',
    '"privateQueryAllowed": self.private_query_allowed',
    '"publicationOrHighRiskCapabilityAllowed":',
    '"controllerAuthorityGranted": self.controller_authority_granted',
    '"familyContributorMayOnlySubmitSourceCandidateOrWithdrawOwnContribution"',
    '"familyRelationshipCannotAuthorizePrivateQueryOrHighRiskCapability"',
    '"syntheticClaimsCannotAuthorizeMemorialContribution"',
):
    assert required in source, f"missing Family Contribution G0 invariant: {required}"

print("owner truth memorial family contribution static gate passed")
PY
