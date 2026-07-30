#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "$ROOT_DIR"

PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_owner_truth_projection_rights_fence \
  tests.test_owner_truth_projection_rights_async_effect \
  tests.test_owner_truth_memory_projection_worker
"$PYTHON_BIN" -m py_compile \
  app/domain/owner_truth/projection_rights.py \
  app/domain/owner_truth/memory_projection.py \
  app/services/owner_truth_projection_rights.py \
  app/services/owner_truth_memory_projection.py \
  app/services/owner_truth_memory_projection_effects.py \
  app/async_effects/target_admission.py \
  app/async_effects/consumer_repository.py \
  app/async_effects/owner_truth_memory_projection_worker.py

"$PYTHON_BIN" - <<'PY'
from pathlib import Path

domain = Path("app/domain/owner_truth/projection_rights.py").read_text(encoding="utf-8")
service = Path("app/services/owner_truth_projection_rights.py").read_text(encoding="utf-8")
projection = Path("app/services/owner_truth_memory_projection.py").read_text(encoding="utf-8")
effects = Path("app/services/owner_truth_memory_projection_effects.py").read_text(encoding="utf-8")
admission = Path("app/async_effects/target_admission.py").read_text(encoding="utf-8")
consumer = Path("app/async_effects/consumer_repository.py").read_text(encoding="utf-8")
worker = Path("app/async_effects/owner_truth_memory_projection_worker.py").read_text(encoding="utf-8")
migration = Path("db/migrations/0061_owner_truth_projection_rights_fence.sql").read_text(
    encoding="utf-8"
)

for required in (
    "OwnerTruthProjectionRightsRevisionCommand",
    "ProjectionRightsState",
    "event_hash",
    "command_id_hash",
):
    assert required in domain, required
for required in (
    "InMemoryOwnerTruthProjectionRightsRepository",
    "PostgresOwnerTruthProjectionRightsRepository",
    "build_memory_projection_rebuild_effect_intent_for_rights_revision",
    "only the Vault Owner may change projection rights",
):
    assert required in service, required
for required in (
    "rightsRevision",
    "rightsEventHash",
    "rightsRevoked",
    "rightsRevisionChanged",
):
    assert required in projection, required
for required in (
    "MEMORY_PROJECTION_RIGHTS_REBUILD_OPERATION_TYPE",
    "MEMORY_PROJECTION_RIGHTS_REBUILD_EVENT_TYPE",
    "projectionRightsRevision",
    "compatibilityProjectionRights",
):
    assert required in effects, required
for required in (
    "admit_owner_truth_projection_rights_rebuild",
    "projectionRightsNotActive",
    "projectionRightsRevisionChanged",
):
    assert required in admission, required
for required in (
    "_is_typed_memory_projection_rebuild",
    "projectionRightsRevision",
):
    assert required in consumer, required
for required in (
    "MEMORY_PROJECTION_RIGHTS_REBUILD_OPERATION_TYPE",
    "admit_owner_truth_projection_rights_rebuild",
):
    assert required in worker, required
for required in (
    "CREATE TABLE owner_truth.projection_rights_events",
    "rights_revision",
    "rights_event_hash",
    "owner truth projection checkpoint rights fence is stale",
    "owner_truth_projection_rights_events_reject_mutation",
):
    assert required in migration, required
for forbidden in ("APIRouter", "@router", "/context/build", "provider"):
    assert forbidden not in service, forbidden
print("Owner Truth projection rights fence G0 contract gate passed")
PY
