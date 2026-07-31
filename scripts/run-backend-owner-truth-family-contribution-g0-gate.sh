#!/usr/bin/env bash
set -euo pipefail

# G0-only. This verifies the QA-only static family contribution lane. It must
# never widen a family relationship into Vault read, Candidate, Voice, Digital
# Human, Memorial, publication, or asynchronous extraction authority.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_async_effect_target_admission \
  tests.test_owner_truth_candidate_extraction_worker \
  tests.test_owner_truth_family_contribution \
  tests.test_owner_truth_family_contribution_api \
  tests.test_owner_truth_family_contribution_migration_contract \
  tests.test_delegated_access \
  tests.test_delegated_access_api \
  tests.test_route_ownership_registry

"$PYTHON_BIN" -m py_compile \
  app/main.py \
  app/core/config.py \
  app/services/owner_truth_family_contribution.py \
  app/async_effects/target_admission.py \
  app/services/in_memory_store.py \
  app/services/postgres_store.py \
  app/services/route_ownership.py

"$PYTHON_BIN" - <<'PY'
from pathlib import Path

service = Path("app/services/owner_truth_family_contribution.py").read_text(encoding="utf-8")
main = Path("app/main.py").read_text(encoding="utf-8")
migration = Path("db/migrations/0070_owner_truth_family_contribution_grants.sql").read_text(
    encoding="utf-8"
)

for required in (
    '"scope": FAMILY_CONTRIBUTION_SCOPE',
    '"origin": "familyContributionGrant"',
    '"perspectiveType": "familyReport"',
    '"candidateExtraction": "defaultOff"',
    '"candidateExtraction": {"status": "notRequested"}',
    "familyContributionRelationshipEpochMismatch",
    "familyContributionGrantInactive",
):
    assert required in service, required

admission = Path("app/async_effects/target_admission.py").read_text(encoding="utf-8")
for required in (
    '"candidateExtraction") == "defaultOff"',
    "sourceCandidateExtractionDisabled",
    "candidate_extraction_allowed",
):
    assert required in admission, required

for forbidden in (
    "build_source_created_effect_intent",
    "effect_kernel_repository",
):
    assert forbidden not in service, forbidden

import ast

tree = ast.parse(service)
for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        module = getattr(node, "module", "") or ""
        assert not module.startswith("app.async_effects"), module
        assert not module.startswith("app.services.voice"), module
        assert not module.startswith("app.services.digital_human"), module
        assert not module.startswith("app.services.owner_truth_memorial"), module

for required in (
    "OWNER_TRUTH_FAMILY_CONTRIBUTION_QA_ENABLED",
    "x-dreamjourney-qa-owner-truth",
    "/v2/vaults/{vault_id}/family-contribution-grants",
    "include_in_schema=False",
):
    assert required in main, required

for required in (
    "CREATE TABLE owner_truth.family_contribution_grants",
    "scope = 'submitTextSource'",
    "REFERENCES public.family_relationships(id)",
    "family contribution grant identity is immutable",
):
    assert required in migration, required

for forbidden in (
    "voice_profile",
    "digital_human",
    "create table owner_truth.publications",
):
    assert forbidden not in migration.lower(), forbidden

print("owner truth family contribution G0 contract gate passed")
PY
