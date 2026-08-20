#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
export PYTHONPYCACHEPREFIX="${TMPDIR:-/tmp}/dreamjourney-pc-c1-python-cache"

PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_release_policy.ReleasePolicyServiceTests.test_authenticated_owner_media_requires_external_verification \
  tests.test_release_policy.ReleasePolicyServiceTests.test_authenticated_owner_media_opens_only_after_public_capability_is_ready \
  tests.test_release_policy.ReleasePolicyServiceTests.test_real_otp_authentication_cannot_bypass_media_public_readiness \
  tests.test_release_policy.ReleasePolicyServiceTests.test_owner_media_capture_requires_an_independent_closed_pilot_feature_grant \
  tests.test_runtime_capabilities.RuntimeCapabilityConfigTests.test_filesystem_storage_never_becomes_publicly_verified \
  tests.test_runtime_capabilities.RuntimeCapabilityConfigTests.test_storage_and_processing_require_independent_external_evidence \
  tests.test_runtime_capabilities.RuntimeCapabilityConfigTests.test_stale_media_external_evidence_fails_closed \
  tests.test_runtime_capabilities.RuntimeCapabilityConfigTests.test_processing_fails_closed_when_storage_runtime_control_is_blocked

PYTHONPATH=. "$PYTHON_BIN" -m py_compile \
  app/services/media_release_admission.py \
  app/services/release_policy.py \
  app/services/runtime_config.py

printf '[pc-c1-media-admission-gate] passed\n'
