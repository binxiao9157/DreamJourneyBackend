#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
  else
    PYTHON_BIN="python3"
  fi
fi

echo "== Backend unittest =="
STORE_BACKEND=memory PYTHONPATH=. "$PYTHON_BIN" -m unittest discover tests

echo "== Credential response boundary smoke =="
PYTHON_BIN="$PYTHON_BIN" scripts/run-credential-response-boundary-smoke.sh

echo "== Owner Truth knowledge dimension confirmation gate =="
PYTHON_BIN="$PYTHON_BIN" scripts/run-backend-owner-truth-knowledge-recommendation-gate.sh

echo "== Backend py_compile =="
"$PYTHON_BIN" -m compileall -q app tests
"$PYTHON_BIN" -m py_compile scripts/*.py

echo "== Voice clone 2.0 contract smoke =="
PYTHONPATH=. "$PYTHON_BIN" scripts/voice_clone_2_contract_smoke.py >/dev/null

echo "== Provider effect G0 contract gate =="
PYTHON_BIN="$PYTHON_BIN" scripts/run-backend-provider-effect-contract-gate.sh

echo "== Provider effect G2 reconciliation contract gate =="
PYTHON_BIN="$PYTHON_BIN" scripts/run-backend-provider-effect-reconciliation-contract-gate.sh

echo "== Provider-query operations G0 contract gate =="
PYTHON_BIN="$PYTHON_BIN" scripts/run-backend-provider-query-operations-contract-gate.sh

echo "== Business completion/message/notification G0 contract gate =="
PYTHON_BIN="$PYTHON_BIN" scripts/run-backend-business-message-notification-contract-gate.sh

echo "== Async-effect dead-letter G0 contract gate =="
PYTHON_BIN="$PYTHON_BIN" scripts/run-backend-async-effect-dead-letter-contract-gate.sh

echo "== Async-effect dead-letter G2 persistence contract gate =="
PYTHON_BIN="$PYTHON_BIN" scripts/run-backend-async-effect-dead-letter-persistence-contract-gate.sh

echo "== Async-effect dead-letter G2 replay-request contract gate =="
PYTHON_BIN="$PYTHON_BIN" scripts/run-backend-async-effect-dead-letter-replay-request-contract-gate.sh

echo "== Async-effect worker-loss evidence G0/G2 contract gate =="
PYTHON_BIN="$PYTHON_BIN" scripts/run-backend-async-effect-worker-loss-evidence-contract-gate.sh

echo "== Async-effect readiness/evidence G0 contract gate =="
PYTHON_BIN="$PYTHON_BIN" scripts/run-backend-async-effect-readiness-evidence-contract-gate.sh

echo "== Async-effect recovery/evidence G0 contract gate =="
PYTHON_BIN="$PYTHON_BIN" scripts/run-backend-async-effect-recovery-evidence-contract-gate.sh

echo "== Verified media processor admission G0 contract gate =="
PYTHON_BIN="$PYTHON_BIN" scripts/run-backend-verified-media-processor-shadow-gate.sh

echo "== Backend deployment files =="
test -f Dockerfile
test -f docker-compose.yml
test -f .env.example
test -f requirements.txt
test -f scripts/migrate_db.py
test -f scripts/backend-readiness-postgres-smoke.py
test -f scripts/backend-readiness-deployed-smoke.py
test -f scripts/backend-operation-metrics-deployed-smoke.py
test -f scripts/run-backend-operation-metrics-deployed-smoke.sh
test -f scripts/backend-incident-lifecycle-deployed-smoke.py
test -f scripts/run-backend-incident-lifecycle-deployed-smoke.sh
test -f scripts/backend-provider-redaction-smoke.py
test -f scripts/backend-provider-redaction-deployed-smoke.py
test -f scripts/run-backend-provider-redaction-deployed-smoke.sh
test -f scripts/backend-provider-cost-evidence-smoke.py
test -f scripts/backend-provider-cost-evidence-deployed-smoke.py
test -f scripts/run-backend-provider-cost-evidence-deployed-smoke.sh
test -f scripts/evidence_manifest_retention.py
test -f scripts/run-evidence-manifest-retention.sh
test -f scripts/backend-evidence-manifest-deployed-smoke.py
test -f scripts/run-backend-evidence-manifest-deployed-smoke.sh
test -f scripts/backend-account-deletion-rights-deployed-smoke.py
test -f scripts/run-backend-account-deletion-rights-deployed-smoke.sh
test -f scripts/backend-account-terminal-purge-deployed-smoke.py
test -f scripts/run-backend-account-terminal-purge-deployed-smoke.sh
test -f scripts/db/backup_postgres.sh
test -f scripts/db/audit_backup_retention.sh
test -f scripts/db/verify_backup_manifest.py
test -f scripts/db/verify_latest_backup.py
test -f scripts/db/backup-deployed-smoke.py
test -f deploy/systemd/dreamjourney-db-backup.timer
test -f deploy/systemd/dreamjourney-evidence-manifest-retention.service
test -f deploy/systemd/dreamjourney-evidence-manifest-retention.timer
test -f db/migrations/0001_existing_schema_baseline.sql
test -f db/migrations/0001_existing_schema_baseline.json
test -f db/migrations/0009_account_purge_receipts.sql
test -f db/migrations/0009_account_purge_receipts.json
test -f db/migrations/0010_evidence_manifest.sql
test -f db/migrations/0010_evidence_manifest.json
test -f db/migrations/0011_owner_truth_core.sql
test -f db/migrations/0011_owner_truth_core.json
test -f db/migrations/0012_owner_truth_source_commands.sql
test -f db/migrations/0012_owner_truth_source_commands.json
test -f scripts/backend-owner-truth-postgres-smoke.py
test -f scripts/run-backend-owner-truth-postgres-smoke.sh
test -f db/migrations/0013_async_effects_kernel.sql
test -f db/migrations/0013_async_effects_kernel.json
test -f scripts/backend-async-effects-postgres-smoke.py
test -f scripts/run-backend-async-effects-postgres-smoke.sh
	test -f db/migrations/0014_owner_truth_candidate_decisions.sql
	test -f db/migrations/0014_owner_truth_candidate_decisions.json
	test -f db/migrations/0016_owner_truth_memory_projection.sql
	test -f db/migrations/0016_owner_truth_memory_projection.json
	test -f db/migrations/0017_owner_truth_memory_projection_trigger_fix.sql
	test -f db/migrations/0017_owner_truth_memory_projection_trigger_fix.json
	test -f app/services/owner_truth_memory_projection.py
	test -f db/migrations/0018_owner_truth_answer_citations.sql
	test -f db/migrations/0018_owner_truth_answer_citations.json
	test -f db/migrations/0019_owner_truth_answer_citation_trigger_fix.sql
	test -f db/migrations/0019_owner_truth_answer_citation_trigger_fix.json
	test -f app/services/owner_truth_answer_citation.py
test -f db/migrations/0022_owner_truth_correction_resolver.sql
test -f db/migrations/0022_owner_truth_correction_resolver.json
test -f app/domain/owner_truth/memory_correction.py
test -f db/migrations/0023_owner_truth_legacy_migration_inventory.sql
test -f db/migrations/0023_owner_truth_legacy_migration_inventory.json
test -f app/domain/owner_truth/legacy_migration.py
test -f app/services/owner_truth_legacy_migration.py
test -f app/services/owner_truth_legacy_shadow_parity.py
test -f db/migrations/0024_echo_delayed_reply_answer_completion.sql
test -f db/migrations/0024_echo_delayed_reply_answer_completion.json
test -f app/services/echo_delayed_reply_effects.py
test -f app/services/echo_delayed_reply_service.py
test -f scripts/run-backend-echo-delayed-reply-answer-inbox-contract-gate.sh
test -f scripts/backend-echo-delayed-reply-atomic-completion-postgres-smoke.py
test -f scripts/run-backend-echo-delayed-reply-atomic-completion-postgres-smoke.sh
test -f scripts/backend-echo-context-reply-runtime-postgres-smoke.py
test -f scripts/run-backend-echo-context-reply-runtime-postgres-smoke.sh
test -f app/async_effects/provider_effects.py
test -f tests/test_provider_effects.py
test -f scripts/run-backend-provider-effect-contract-gate.sh
test -f db/migrations/0025_provider_effect_reconciliation_projection.sql
test -f db/migrations/0025_provider_effect_reconciliation_projection.json
test -f app/async_effects/provider_effect_repository.py
test -f tests/test_provider_effect_repository.py
test -f tests/test_provider_effect_reconciliation_migration_contract.py
test -f scripts/run-backend-provider-effect-reconciliation-contract-gate.sh
test -f scripts/backend-provider-effect-reconciliation-postgres-smoke.py
test -f scripts/run-backend-provider-effect-reconciliation-postgres-smoke.sh
test -f app/async_effects/provider_query_operations.py
test -f tests/test_provider_query_operations.py
test -f scripts/run-backend-provider-query-operations-contract-gate.sh
test -f scripts/backend-provider-query-operations-postgres-smoke.py
test -f scripts/run-backend-provider-query-operations-postgres-smoke.sh
test -f app/async_effects/message_notification_effects.py
test -f tests/test_business_message_notification_effects.py
test -f scripts/run-backend-business-message-notification-contract-gate.sh
test -f app/async_effects/dead_letter_effects.py
test -f tests/test_async_effect_dead_letter_contract.py
test -f scripts/run-backend-async-effect-dead-letter-contract-gate.sh
test -f db/migrations/0026_async_effect_dead_letter_persistence.sql
test -f db/migrations/0026_async_effect_dead_letter_persistence.json
test -f app/async_effects/dead_letter_repository.py
test -f tests/test_async_effect_dead_letter_repository.py
test -f tests/test_async_effect_dead_letter_persistence_migration_contract.py
test -f scripts/run-backend-async-effect-dead-letter-persistence-contract-gate.sh
test -f scripts/backend-async-effect-dead-letter-persistence-postgres-smoke.py
test -f scripts/run-backend-async-effect-dead-letter-persistence-postgres-smoke.sh
test -f db/migrations/0027_async_effect_dead_letter_replay_requests.sql
test -f db/migrations/0027_async_effect_dead_letter_replay_requests.json
test -f app/async_effects/dead_letter_replay_repository.py
test -f tests/test_async_effect_dead_letter_replay_repository.py
test -f tests/test_async_effect_dead_letter_replay_request_migration_contract.py
test -f scripts/run-backend-async-effect-dead-letter-replay-request-contract-gate.sh
test -f scripts/backend-async-effect-dead-letter-replay-request-postgres-smoke.py
test -f scripts/run-backend-async-effect-dead-letter-replay-request-postgres-smoke.sh
test -f db/migrations/0028_async_effect_worker_loss_observations.sql
test -f db/migrations/0028_async_effect_worker_loss_observations.json
test -f app/async_effects/worker_loss_evidence.py
test -f app/async_effects/worker_loss_observation_repository.py
test -f tests/test_async_effect_worker_loss_evidence.py
test -f tests/test_async_effect_worker_loss_observation_repository.py
test -f tests/test_async_effect_worker_loss_observation_migration_contract.py
test -f scripts/run-backend-async-effect-worker-loss-evidence-contract-gate.sh
test -f scripts/backend-async-effect-worker-loss-evidence-postgres-smoke.py
test -f scripts/run-backend-async-effect-worker-loss-evidence-postgres-smoke.sh
test -f db/migrations/0029_owner_truth_conversation_session_bootstrap.sql
test -f db/migrations/0029_owner_truth_conversation_session_bootstrap.json
test -f app/domain/owner_truth/conversation.py
test -f app/services/owner_truth_conversation.py
test -f app/services/owner_truth_interview_session_read.py
test -f tests/test_owner_truth_conversation.py
test -f tests/test_owner_truth_conversation_migration_contract.py
test -f tests/test_owner_truth_interview_session_state_api.py
test -f scripts/backend-owner-truth-conversation-postgres-smoke.py
test -f scripts/run-backend-owner-truth-conversation-postgres-smoke.sh
test -f db/migrations/0033_owner_truth_interview_candidate_proposal_admission.sql
test -f db/migrations/0033_owner_truth_interview_candidate_proposal_admission.json
test -f app/domain/owner_truth/interview_candidate_proposal.py
test -f app/services/owner_truth_interview_candidate_proposal.py
test -f tests/test_owner_truth_interview_candidate_proposal.py
test -f tests/test_owner_truth_interview_candidate_proposal_migration_contract.py
test -f app/domain/owner_truth/interview_candidate_review.py
test -f app/services/owner_truth_interview_candidate_review.py
test -f tests/test_owner_truth_interview_candidate_review.py
test -f tests/test_owner_truth_interview_candidate_review_api.py
test -f db/migrations/0034_owner_truth_interview_candidate_batch_decisions.sql
test -f db/migrations/0034_owner_truth_interview_candidate_batch_decisions.json
test -f app/domain/owner_truth/interview_candidate_batch_decision.py
test -f app/services/owner_truth_interview_candidate_batch_decision.py
test -f tests/test_owner_truth_interview_candidate_batch_decision.py
test -f db/migrations/0035_owner_truth_knowledge_dimension_confirmation_receipts.sql
test -f db/migrations/0035_owner_truth_knowledge_dimension_confirmation_receipts.json
test -f app/services/owner_truth_knowledge_dimension_confirmation.py
test -f app/domain/owner_truth/knowledge_dimension_read.py
test -f app/services/owner_truth_knowledge_recommendation_read.py
test -f scripts/backend-owner-truth-knowledge-dimension-confirmation-postgres-smoke.py
test -f scripts/run-backend-owner-truth-knowledge-dimension-confirmation-postgres-smoke.sh
test -f tests/test_owner_truth_knowledge_dimension_confirmation.py
test -f tests/test_owner_truth_knowledge_dimension_confirmation_api.py
test -f tests/test_owner_truth_knowledge_dimension_confirmation_migration_contract.py
test -f tests/test_owner_truth_knowledge_recommendation_read.py
test -f tests/test_owner_truth_knowledge_recommendation_read_api.py
test -f tests/test_owner_truth_interview_candidate_batch_decision_migration_contract.py
test -f app/domain/owner_truth/interview_candidate_single_review.py
test -f app/services/owner_truth_interview_candidate_single_review.py
test -f tests/test_owner_truth_interview_candidate_single_review.py
test -f db/migrations/0030_owner_truth_interview_pacing_state.sql
test -f db/migrations/0030_owner_truth_interview_pacing_state.json
test -f app/services/owner_truth_interview_session_orchestration.py
test -f tests/test_owner_truth_interview_pacing_state.py
test -f tests/test_owner_truth_interview_pacing_state_migration_contract.py
test -f db/migrations/0031_owner_truth_interview_review_batches.sql
test -f db/migrations/0031_owner_truth_interview_review_batches.json
test -f tests/test_owner_truth_interview_review_batch.py
test -f tests/test_owner_truth_interview_review_batch_migration_contract.py
test -f db/migrations/0032_owner_truth_interview_topic_switch.sql
test -f db/migrations/0032_owner_truth_interview_topic_switch.json
test -f tests/test_owner_truth_interview_topic_switch.py
test -f tests/test_owner_truth_interview_topic_switch_migration_contract.py
test -f app/async_effects/readiness_evidence.py
test -f tests/test_async_effect_readiness_evidence.py
test -f scripts/run-backend-async-effect-readiness-evidence-contract-gate.sh
test -f app/async_effects/recovery_evidence.py
test -f tests/test_async_effect_recovery_evidence.py
test -f scripts/run-backend-async-effect-recovery-evidence-contract-gate.sh
test -f scripts/backend-owner-truth-legacy-migration-postgres-smoke.py
test -f scripts/run-backend-owner-truth-legacy-migration-postgres-smoke.sh
test -f scripts/backend-owner-truth-legacy-shadow-parity-postgres-smoke.py
test -f scripts/run-backend-owner-truth-legacy-shadow-parity-postgres-smoke.sh
	grep -q "COPY db ./db" Dockerfile
grep -q "psycopg" requirements.txt

echo "== Backend FastAPI smoke =="
if "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1
import fastapi
import httpx
PY
then
  STORE_BACKEND=memory PYTHONPATH=. "$PYTHON_BIN" - <<'PY'
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
health = client.get("/health")
assert health.status_code == 200, health.text
assert health.json()["status"] == "ok"
assert health.json()["deprecated"] is True
live = client.get("/live")
assert live.status_code == 200, live.text
assert live.json()["status"] == "alive"
ready = client.get("/ready")
assert ready.status_code == 200, ready.text
assert ready.json()["status"] == "ready"
config = client.get(
    "/config/runtime",
    headers={
        "X-DreamJourney-Runtime-Contract-Version": "2",
        "X-DreamJourney-Client-Build": "9001",
    },
)
assert config.status_code == 200, config.text
assert "capabilities" in config.json()
print("FastAPI smoke verification passed")
PY
else
  echo "FastAPI/httpx not installed locally; skipping runtime smoke. Docker image installs requirements.txt."
fi

echo "== Backend knowledge delta smoke =="
STORE_BACKEND=memory PYTHONPATH=. "$PYTHON_BIN" scripts/backend-knowledge-delta-smoke.py

echo "== Backend knowledge v2 smoke =="
STORE_BACKEND=memory PYTHONPATH=. "$PYTHON_BIN" scripts/backend-knowledge-v2-smoke.py

echo "== Backend knowledge evidence smoke =="
STORE_BACKEND=memory PYTHONPATH=. "$PYTHON_BIN" scripts/backend-knowledge-evidence-smoke.py

echo "== Backend provider redaction smoke =="
STORE_BACKEND=memory PYTHONPATH=. "$PYTHON_BIN" scripts/backend-provider-redaction-smoke.py

echo "== Backend provider cost evidence smoke =="
STORE_BACKEND=memory PYTHONPATH=. "$PYTHON_BIN" scripts/backend-provider-cost-evidence-smoke.py

echo "== Backend knowledge receipt maintenance smoke =="
PYTHON_BIN="$PYTHON_BIN" scripts/run-backend-knowledge-receipt-maintenance-smoke.sh

echo "== PostgreSQL backup contract smoke =="
PYTHON_BIN="$PYTHON_BIN" scripts/db/run-backup-postgres-smoke.sh

echo "== Backend diff --check =="
git diff --check
git diff --cached --check
