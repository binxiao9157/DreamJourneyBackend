#!/usr/bin/env bash
set -euo pipefail

# Value-free R4 provider-readiness matrix. It uses fake COS/ClamAV clients and
# never reads credentials, calls a Provider, or touches user media.
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
export PYTHONPYCACHEPREFIX="${TMPDIR:-/tmp}/dreamjourney-media-provider-matrix-python-cache"

PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_runtime_capabilities \
  tests.test_owner_truth_media_content_safety_runtime \
  tests.test_owner_truth_media_capture_api \
  tests.test_owner_truth_media_deletion_worker

PYTHONPATH=. "$PYTHON_BIN" -m py_compile \
  app/services/owner_truth_media_source_object.py \
  app/services/media_release_admission.py \
  app/services/provider_runtime.py \
  scripts/backend-owner-truth-media-cos-provider-smoke.py \
  scripts/backend-owner-truth-media-clamav-sidecar-smoke.py

PYTHONPATH=. "$PYTHON_BIN" - <<'PY'
from pathlib import Path

env_example = Path(".env.example").read_text(encoding="utf-8")
for field in (
    "OWNER_TRUTH_MEDIA_STORAGE_PROVIDER=disabled",
    "OWNER_TRUTH_MEDIA_STORAGE_EXTERNAL_VERIFIED=false",
    "OWNER_TRUTH_MEDIA_STORAGE_EVIDENCE_TIMESTAMP=",
    "OWNER_TRUTH_MEDIA_S3_BUCKET=",
    "OWNER_TRUTH_MEDIA_S3_REGION=",
    "OWNER_TRUTH_MEDIA_S3_ENDPOINT_URL=",
    "OWNER_TRUTH_MEDIA_S3_ACCESS_KEY_ID=",
    "OWNER_TRUTH_MEDIA_S3_SECRET_ACCESS_KEY=",
    "OWNER_TRUTH_MEDIA_S3_SERVER_SIDE_ENCRYPTION=",
    "OWNER_TRUTH_MEDIA_CONTENT_SAFETY_PROVIDER=disabled",
    "OWNER_TRUTH_MEDIA_CLAMAV_HOST=",
    "OWNER_TRUTH_MEDIA_PROCESSING_EXTERNAL_VERIFIED=false",
    "OWNER_TRUTH_MEDIA_PROCESSING_EVIDENCE_TIMESTAMP=",
):
    assert field in env_example, field

compose = Path("docker-compose.yml").read_text(encoding="utf-8")
clamav_block = compose.split("\n  clamav:\n", 1)[1].split("\n  postgres:\n", 1)[0]
assert "owner-truth-media-safety" in clamav_block
assert 'expose:\n      - "3310"' in clamav_block
assert "ports:" not in clamav_block
assert "no-new-privileges:true" in clamav_block
assert "clamdscan --ping" in clamav_block

source = Path("app/services/owner_truth_media_source_object.py").read_text(encoding="utf-8")
assert "cos_endpoint_matches_region" in source
assert "private media object delete acknowledgement is unavailable" in source
assert "private media object delete verification failed" in source

for wrapper in (
    "scripts/run-backend-owner-truth-media-cos-provider-smoke.sh",
    "scripts/run-backend-owner-truth-media-clamav-sidecar-smoke.sh",
):
    assert Path(wrapper).is_file(), wrapper

print("Owner Truth media Provider readiness matrix passed")
PY

bash -n scripts/run-backend-owner-truth-media-cos-provider-smoke.sh
bash -n scripts/run-backend-owner-truth-media-clamav-sidecar-smoke.sh

printf '[owner-truth-media-provider-matrix-gate] passed\n'
