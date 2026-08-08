#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
[[ -x "$PYTHON_BIN" ]] || PYTHON_BIN="python3"

: "${DATABASE_URL:?DATABASE_URL is required}"

OUTPUT_PATH="${OUTPUT_PATH:-$ROOT_DIR/tmp/qa/v4-synthetic-account-e2e/result.json}"
LOG_PATH="${LOG_PATH:-${OUTPUT_PATH%.json}.log}"
mkdir -p "$(dirname "$OUTPUT_PATH")" "$(dirname "$LOG_PATH")"

cd "$ROOT_DIR"
RUN_OWNER_TRUTH_MEDIA_PHYSICAL_DELETION_SMOKE=1 \
DATABASE_URL="$DATABASE_URL" \
PYTHONPATH=. \
  "$PYTHON_BIN" scripts/backend-owner-truth-media-processing-postgres-smoke.py \
  >"$LOG_PATH" 2>&1

V4_SYNTHETIC_LOG_PATH="$LOG_PATH" \
V4_SYNTHETIC_OUTPUT_PATH="$OUTPUT_PATH" \
  "$PYTHON_BIN" - <<'PY'
import json
import os
from pathlib import Path

log_path = Path(os.environ["V4_SYNTHETIC_LOG_PATH"])
output_path = Path(os.environ["V4_SYNTHETIC_OUTPUT_PATH"])
payload = None
for line in reversed(log_path.read_text(encoding="utf-8").splitlines()):
    candidate = line.strip()
    if not candidate.startswith("{"):
        continue
    try:
        payload = json.loads(candidate)
        break
    except json.JSONDecodeError:
        continue
if not isinstance(payload, dict):
    raise SystemExit("synthetic account smoke did not emit a JSON receipt")

required_true = (
    "ownerBoundUpload",
    "derivedSource",
    "pendingCandidate",
    "candidateConfirmed",
    "memoryVersionCreated",
    "projectionReady",
    "contextBuilt",
    "exportJobCreated",
    "exportOwnerTruthComplete",
    "exportOwnerIsolated",
    "exportExternalBoundaryPartial",
    "deletionAccessRevoked",
    "deletionRetryRequeued",
    "deletionProviderReceiptAccepted",
    "physicalDeletionCompleted",
    "deletedMediaExcludedFromContext",
    "responseRedaction",
)
missing = [name for name in required_true if payload.get(name) is not True]
if missing:
    raise SystemExit(f"synthetic account E2E evidence is incomplete: {missing}")

receipt = {
    "schemaVersion": "dreamjourney-v4-synthetic-account-e2e-v1",
    "status": "passed",
    "v4SyntheticAccountE2E": True,
    "stages": [
        "source",
        "processing",
        "candidate",
        "decision",
        "memoryVersion",
        "context",
        "export",
        "delete",
        "reconcile",
    ],
    "schemaHead": payload.get("schemaHead"),
    "defaultClosed": payload.get("defaultClosed") is True,
    "fakeProviderBoundary": payload.get("exportExternalBoundaryPartial") is True,
    "crossOwnerDenied": payload.get("crossOwnerDenied") is True,
    "privateValuesInReceipt": False,
}
output_path.write_text(
    json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
PY

echo "V4 synthetic account E2E gate passed: $OUTPUT_PATH"
