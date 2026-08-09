#!/usr/bin/env bash
set -euo pipefail

if [[ "${RUN_BACKEND_OWNER_TRUTH_WORKER_ACTIVATION_SMOKE:-0}" != "1" ]]; then
  echo "Owner Truth worker activation deployed smoke skipped"
  exit 0
fi

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

EXPECTED_STATE="${OWNER_TRUTH_WORKER_EXPECTED_STATE:-blocked}"
case "$EXPECTED_STATE" in
  blocked|ready) ;;
  *) echo "OWNER_TRUTH_WORKER_EXPECTED_STATE must be blocked or ready" >&2; exit 2 ;;
esac

TARGETS="${OWNER_TRUTH_WORKER_ACTIVATION_TARGETS:-ownerTruthCandidateExtraction,ownerTruthMemoryProjection,ownerTruthMediaProcessing,ownerTruthMediaDeletion}"
IFS=',' read -r -a workers <<< "$TARGETS"

cd "$ROOT_DIR"
for worker in "${workers[@]}"; do
  output_file="$(mktemp)"
  trap 'rm -f "$output_file"' EXIT
  set +e
  PYTHONPATH=. "$PYTHON_BIN" -m app.async_effects.owner_truth_worker_activation \
    --worker "$worker" >"$output_file" 2>&1
  command_status=$?
  set -e

  EXPECTED_STATE="$EXPECTED_STATE" \
  COMMAND_STATUS="$command_status" \
  EXPECTED_WORKER="$worker" \
  OUTPUT_FILE="$output_file" \
  "$PYTHON_BIN" - <<'PY'
import json
import os
from pathlib import Path

lines = [line.strip() for line in Path(os.environ["OUTPUT_FILE"]).read_text().splitlines() if line.strip()]
if not lines:
    raise SystemExit("worker preflight emitted no descriptor")
try:
    descriptor = json.loads(lines[-1])
except json.JSONDecodeError as error:
    raise SystemExit("worker preflight descriptor is not JSON") from error

required = {"contractVersion", "worker", "ready", "reason", "blockingDependency"}
if set(descriptor) != required:
    raise SystemExit("worker preflight descriptor fields changed")
if descriptor["contractVersion"] != 1:
    raise SystemExit("worker preflight contract version changed")
if descriptor["worker"] != os.environ["EXPECTED_WORKER"]:
    raise SystemExit("worker preflight returned a different worker")
if not isinstance(descriptor["ready"], bool):
    raise SystemExit("worker preflight ready must be boolean")

expected_ready = os.environ["EXPECTED_STATE"] == "ready"
command_status = int(os.environ["COMMAND_STATUS"])
if descriptor["ready"] != expected_ready:
    raise SystemExit(
        f"worker readiness mismatch: expected {os.environ['EXPECTED_STATE']}, "
        f"got {descriptor['reason']}"
    )
if (command_status == 0) != expected_ready:
    raise SystemExit("worker preflight exit status disagrees with descriptor")

serialized = json.dumps(descriptor, sort_keys=True).lower()
for forbidden in ("secret", "accesskey", "bucket", "objectkey", "ownerid", "vaultid"):
    if forbidden in serialized:
        raise SystemExit("worker preflight descriptor crossed the value-free boundary")

print(
    f"worker={descriptor['worker']} ready={str(descriptor['ready']).lower()} "
    f"reason={descriptor['reason']}"
)
PY
  rm -f "$output_file"
  trap - EXIT
done

echo "Owner Truth worker activation deployed smoke passed"
