#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"

PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_owner_truth_conversation \
  tests.test_owner_truth_interview_input_api \
  tests.test_owner_truth_interview_review_batch_automation \
  tests.test_owner_truth_interview_session_end_migration_contract \
  tests.test_route_ownership_registry \
  tests.test_route_authentication \
  tests.test_auth_sessions \
  tests.test_runtime_capabilities

"$PYTHON_BIN" -m py_compile \
  app/main.py \
  app/domain/owner_truth/conversation.py \
  app/services/owner_truth_conversation.py \
  app/services/route_ownership.py \
  scripts/backend-route-authentication-postgres-smoke.py

"$PYTHON_BIN" - <<'PY'
from pathlib import Path

domain = Path("app/domain/owner_truth/conversation.py").read_text(encoding="utf-8")
service = Path("app/services/owner_truth_conversation.py").read_text(encoding="utf-8")
main = Path("app/main.py").read_text(encoding="utf-8")
migration = Path("db/migrations/0063_owner_truth_interview_session_end.sql").read_text(
    encoding="utf-8"
)
ownership = Path("app/services/route_ownership.py").read_text(encoding="utf-8")

endpoint_start = main.index("def end_owner_truth_interview_session(")
next_route = main.index("@app.post(", endpoint_start + 1)
endpoint = main[endpoint_start:next_route]

for required in (
    "EndInterviewSessionCommand",
    "EndInterviewSessionWriteRecord",
    '"commandType": "endInterviewSession"',
    "InterviewSessionState.ENDED",
):
    assert required in domain, required
for required in (
    "def end_interview_session",
    "def end_session",
    "endInterviewSession",
    "cannot be ended again",
):
    assert required in service, required
for required in (
    "/v2/vaults/{vault_id}/interview-sessions/{session_id}/end",
    "_owner_truth_end_interview_session_command",
    "include_in_schema=False",
):
    assert required in main, required
for required in (
    "_owner_truth_interview_natural_input_context(request, vault_id=vault_id)",
    "_owner_truth_formal_review_batch_automation_in_active_unit_of_work",
    "_attach_owner_truth_formal_review_batch_session_version",
    "_owner_truth_review_batch_automation_after_qa_transition",
):
    assert required in endpoint, required
assert "_owner_truth_candidate_review_context" not in endpoint
assert '"candidates"' not in endpoint
assert '"privateText"' not in endpoint
for required in (
    "endInterviewSession",
    "expected_thread_version IS NOT NULL",
    "expected_session_version IS NOT NULL",
):
    assert required in migration, required
assert "INSERT INTO owner_truth.memory_candidates" not in migration
assert "INSERT INTO owner_truth.memories" not in migration
assert "INSERT INTO owner_truth.memory_versions" not in migration
assert "ownerTruthInterviewSessionEnd" in ownership

print("Owner Truth interview end G0 contract gate passed")
PY
