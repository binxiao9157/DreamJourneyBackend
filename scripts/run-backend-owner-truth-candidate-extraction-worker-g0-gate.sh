#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"

PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_owner_truth_candidate_extraction \
  tests.test_owner_truth_candidate_extraction_worker

PYTHONPATH=. "$PYTHON_BIN" -m py_compile \
  app/async_effects/owner_truth_candidate_extraction_worker.py \
  app/services/owner_truth_candidate_extraction.py \
  app/services/postgres_store.py

"$PYTHON_BIN" - <<'PY'
from pathlib import Path

worker_source = Path("app/async_effects/owner_truth_candidate_extraction_worker.py").read_text(
    encoding="utf-8"
)
for required in (
    "owner_truth_candidate_extraction_worker_enabled",
    "ownerTruthCandidateExtractionWorkerDisabled",
    "owner_truth_candidate_extraction_input_repository",
    "record_in_unit_of_work",
    "candidateExtractionRetryableFailure",
    "DeterministicOwnerTruthCandidateExtractor",
):
    assert required in worker_source, f"missing candidate worker invariant: {required}"
assert 'payload["sourceText"]' not in worker_source
assert 'payload["sourceContent"]' not in worker_source

service_source = Path("app/services/owner_truth_candidate_extraction.py").read_text(
    encoding="utf-8"
)
for required in (
    "PostgresOwnerTruthCandidateExtractionInputRepository",
    "FOR SHARE",
    "record_in_unit_of_work",
):
    assert required in service_source, f"missing candidate extraction service invariant: {required}"

config_source = Path("app/core/config.py").read_text(encoding="utf-8")
assert "OWNER_TRUTH_CANDIDATE_EXTRACTION_WORKER_ENABLED" in config_source
env_source = Path(".env.example").read_text(encoding="utf-8")
assert "OWNER_TRUTH_CANDIDATE_EXTRACTION_WORKER_ENABLED=false" in env_source
print("Owner Truth candidate extraction worker G0 gate passed")
PY
