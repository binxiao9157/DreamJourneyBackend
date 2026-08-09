#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

"$ROOT_DIR/.venv/bin/python" -m unittest \
  tests.test_owner_truth_family_contribution \
  tests.test_owner_truth_family_contribution_formal_api \
  tests.test_owner_truth_family_contribution_review_migration_contract \
  tests.test_owner_truth_data_rights \
  tests.test_data_rights_module_inventory \
  tests.test_route_ownership_registry \
  tests.test_data_export_package \
  tests.test_data_export_jobs \
  tests.test_data_rights_external_deletion_executor \
  tests.test_closed_pilot_admission \
  tests.test_apns_delivery \
  tests.test_business_message_notification_effects \
  tests.test_core_services.EchoDelayedReplyAPITests.test_push_device_token_api_registers_without_returning_raw_token \
  tests.test_core_services.EchoDelayedReplyAPITests.test_push_device_token_registration_enforces_configured_topic_and_environment

"$ROOT_DIR/.venv/bin/python" - <<'PY'
from app.services.closed_pilot_admission import (
    CLOSED_PILOT_FEATURE_ORDER,
    ClosedPilotReadiness,
    build_closed_pilot_admission_plan,
)

current = list(CLOSED_PILOT_FEATURE_ORDER[:-2])
requested = list(CLOSED_PILOT_FEATURE_ORDER[:-1])
plan = build_closed_pilot_admission_plan(
    owner_ids=["synthetic-owner-v4-closure"],
    current_features=current,
    requested_features=requested,
    readiness={
        feature: ClosedPilotReadiness(ready=True, reason="syntheticGateReady")
        for feature in requested
    },
)
assert plan["status"] == "ready"
assert plan["nextFeature"] == "ownerTruthFamilyContribution"
assert plan["applyAuthorized"] is True
assert plan["ownerDigests"] and "synthetic-owner-v4-closure" not in str(plan)
PY

if [[ "${RUN_POSTGRES_EXTERNAL_DELETION_SMOKE:-0}" == "1" ]]; then
  "$ROOT_DIR/.venv/bin/python" \
    scripts/backend-data-rights-external-effect-receipts-postgres-smoke.py
else
  printf '%s\n' \
    'Postgres external-deletion smoke skipped; set RUN_POSTGRES_EXTERNAL_DELETION_SMOKE=1.'
fi

printf '%s\n' 'V4 family, export, deletion, closed-pilot and APNs foundation gate passed.'
