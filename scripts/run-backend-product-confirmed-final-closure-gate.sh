#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"

./scripts/run-backend-product-confirmed-digital-human-closure-gate.sh
./scripts/run-backend-product-confirmed-time-letter-delayed-reply-closure-gate.sh

PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_product_confirmed_first_release_scope \
  tests.test_route_authentication \
  tests.test_route_ownership_registry \
  tests.test_auth_sessions \
  tests.test_in_app_message_center \
  tests.test_archive_store \
  tests.test_owner_truth_formal_memory \
  tests.test_owner_truth_formal_memory_api \
  tests.test_owner_truth_memory_search_read_api \
  tests.test_echo_answer \
  tests.test_family_relationship_termination \
  tests.test_voice_profile_creation_quota \
  tests.test_voice_profile_lifecycle \
  tests.test_voice_clone_c0_admission_gate \
  tests.test_formal_memory_markdown_export \
  tests.test_formal_memory_markdown_export_api \
  tests.test_publication_lifecycle_api \
  tests.test_publication_management_read_api \
  tests.test_publication_registered_share_grant_contract \
  tests.test_publication_visitor_access \
  tests.test_publication_visitor_access_api \
  tests.test_publication_visitor_answer_safety \
  tests.test_production_readiness_report

PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-/tmp/dreamjourney-python-cache}" \
  "$PYTHON_BIN" -m py_compile \
  scripts/backend-release-policy-command-deployed-smoke.py \
  scripts/backend-product-confirmed-closed-capabilities-deployed-smoke.py

if [[ "${RUN_DEPLOYED_PC_E2_SMOKE:-0}" == "1" ]]; then
  ./scripts/run-backend-product-confirmed-closed-capabilities-deployed-smoke.sh
  ./scripts/run-backend-release-policy-command-deployed-smoke.sh
  ./scripts/run-backend-release-policy-stable-feature-deployed-smoke.sh
fi

echo "Backend PC-E2 product-confirmed final closure gate passed"
