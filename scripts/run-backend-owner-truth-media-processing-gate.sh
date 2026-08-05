#!/usr/bin/env bash
set -euo pipefail

# Stage 2 closed-pilot contract gate. It uses only local fake storage and
# httpx mock transport; it never calls a real OCR/ASR provider or uploads
# bytes outside the test process.
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"

PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_owner_truth_media_capture_api \
  tests.test_owner_truth_media_processing_worker \
  tests.test_owner_truth_media_external_processor_contract \
  tests.test_owner_truth_media_processing_migration_contract \
  tests.test_owner_truth_media_deletion_migration_contract \
  tests.test_async_effect_lease_repository \
  tests.test_route_ownership_registry \
  tests.test_release_policy

PYTHONPATH=. "$PYTHON_BIN" -m py_compile \
  app/core/config.py \
  app/services/owner_truth_media_source_object.py \
  app/services/owner_truth_media_processing.py \
  app/services/owner_truth_media_deletion.py \
  app/async_effects/owner_truth_media_processing_worker.py \
  scripts/backend-owner-truth-media-processing-postgres-smoke.py \
  app/services/route_ownership.py \
  app/services/release_policy.py \
  app/main.py

PYTHONPATH=. "$PYTHON_BIN" - <<'PY'
from pathlib import Path

from app.core.config import Settings
from app.services.owner_truth_media_processing import OwnerTruthMediaProcessorRouter
from app.services.owner_truth_media_source_object import build_private_media_object_store

settings = Settings()
assert settings.owner_truth_media_capture_enabled is False
assert settings.owner_truth_media_processing_worker_enabled is False
assert settings.owner_truth_media_image_ocr_provider == "disabled"
assert settings.owner_truth_media_audio_asr_provider == "disabled"
assert OwnerTruthMediaProcessorRouter.from_settings(settings).identity_for({"mediaKind": "image"}) == (
    "disabledImageOCR",
    "v1",
)
assert build_private_media_object_store(provider="disabled", root="/tmp/unused").provider_name == "disabled"

worker = Path("app/async_effects/owner_truth_media_processing_worker.py").read_text(encoding="utf-8")
assert 'payload["extractedText"]' not in worker
assert 'payload["storageKey"]' not in worker
assert "owner_truth_media_processing_worker_enabled" in worker
assert "OperationMetricRecorder" in worker
assert "ownerTruthMediaProcessingWorker" in worker
assert "def _record_attempt(" in worker
print("Owner Truth Stage 2 private media processing gate passed")
PY

bash -n scripts/run-backend-owner-truth-media-processing-postgres-smoke.sh
bash -n scripts/run-backend-owner-truth-media-processing-deployed-smoke.sh
bash -n scripts/run-backend-owner-truth-media-closed-pilot-formal-postgres-smoke.sh
