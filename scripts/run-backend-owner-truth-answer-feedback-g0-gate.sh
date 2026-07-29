#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "$ROOT_DIR"

PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_owner_truth_answer_feedback \
  tests.test_owner_truth_answer_feedback_migration_contract \
  tests.test_owner_truth_data_rights_projection \
  tests.test_owner_truth_candidate_review_api \
  tests.test_route_ownership_registry

"$PYTHON_BIN" -m py_compile \
  app/main.py \
  app/services/owner_truth_answer_citation.py \
  app/services/owner_truth_answer_feedback.py \
  app/services/owner_truth_data_rights.py \
  app/services/data_rights_module_inventory.py \
  app/services/route_ownership.py

"$PYTHON_BIN" - <<'PY'
from pathlib import Path

service = Path("app/services/owner_truth_answer_feedback.py").read_text(encoding="utf-8")
main = Path("app/main.py").read_text(encoding="utf-8")
migration = Path("db/migrations/0062_owner_truth_answer_feedback.sql").read_text(
    encoding="utf-8"
)
rights = Path("app/services/owner_truth_data_rights.py").read_text(encoding="utf-8")
ownership = Path("app/services/route_ownership.py").read_text(encoding="utf-8")

for required in (
    "OwnerTruthAnswerFeedbackService",
    "OwnerTruthAnswerCitationReadService",
    "metricEligible",
    "projectionUnavailable",
    "answer_citation_read_summary",
):
    assert required in service, required
for required in (
    '/v2/vaults/{vault_id}/answers/{answer_id}/citations',
    '/v2/vaults/{vault_id}/answers/{answer_id}/feedback',
    "_owner_truth_answer_citation_context",
    "include_in_schema=False",
):
    assert required in main, required
for required in (
    "CREATE TABLE owner_truth.answer_feedback",
    "UNIQUE (vault_id, answer_id)",
    "owner_truth_answer_feedback_reject_mutation",
    "metric eligibility is invalid",
):
    assert required in migration, required
for required in ("answerFeedback", "ownerTruthAnswerFeedback"):
    assert required in rights, required
for required in ("ownerTruthAnswerCitationRead", "ownerTruthAnswerFeedback"):
    assert required in ownership, required
for forbidden in ("answerText", "queryText", "memoryText", "public Echo feedback"):
    assert forbidden not in service, forbidden

print("Owner Truth Answer feedback G0 contract gate passed")
PY
