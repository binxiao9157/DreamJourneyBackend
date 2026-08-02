#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_owner_truth_family_contribution \
  tests.test_owner_truth_family_contribution_api \
  tests.test_owner_truth_family_contribution_formal_api \
  tests.test_owner_truth_family_contribution_migration_contract \
  tests.test_owner_truth_family_contribution_formal_migration_contract \
  tests.test_release_policy \
  tests.test_route_ownership_registry \
  tests.test_route_authentication \
  tests.test_runtime_capabilities

"$PYTHON_BIN" -m py_compile \
  app/main.py \
  app/services/owner_truth_family_contribution.py \
  app/services/in_memory_store.py \
  app/services/postgres_store.py \
  app/services/release_policy.py \
  app/services/route_ownership.py \
  scripts/backend-owner-truth-family-contribution-formal-postgres-smoke.py

"$PYTHON_BIN" - <<'PY'
from pathlib import Path

service = Path("app/services/owner_truth_family_contribution.py").read_text(encoding="utf-8")
main = Path("app/main.py").read_text(encoding="utf-8")
migration = Path(
    "db/migrations/0072_owner_truth_family_contribution_formal_authorization.sql"
).read_text(encoding="utf-8")

for required in (
    'FAMILY_CONTRIBUTION_FORMAL_FEATURE = "ownerTruthFamilyContribution"',
    'FAMILY_CONTRIBUTION_ADMISSION_CLOSED_PILOT = "closedPilot"',
    '"familyContributionAdmissionMode":',
    '"candidateExtraction": "defaultOff"',
    "familyContributionGrantAdmissionModeMismatch",
):
    assert required in service, required

for required in (
    "/v2/vaults/{vault_id}/family-contribution/grants",
    "_owner_truth_family_contribution_product_context",
    "_owner_truth_family_contribution_product_contributor_context",
    "ownerTruthFamilyContributionUnavailable",
):
    assert required in main, required

for required in (
    "ADD COLUMN IF NOT EXISTS admission_mode",
    "ADD COLUMN IF NOT EXISTS authorization_evidence JSONB",
    "ownerTruthFamilyContribution",
    "family contribution grant identity is immutable",
):
    assert required in migration, required

for forbidden in (
    "voice_profile",
    "digital_human",
    "GRANT SELECT",
):
    assert forbidden not in migration.lower() if forbidden != "GRANT SELECT" else forbidden not in migration.upper()

print("owner truth family contribution formal contract gate passed")
PY
