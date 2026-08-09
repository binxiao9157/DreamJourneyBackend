#!/usr/bin/env bash
set -euo pipefail

# Compose existing focused Gates into one V4 functional code handoff. This
# script does not call real Providers or claim device/release acceptance.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
[[ -x "$PYTHON_BIN" ]] || PYTHON_BIN="python3"
RUN_ID="${RUN_ID:-$(date +%Y%m%d-%H%M%S)-v4-functional-code}"
OUTPUT_ROOT="${OUTPUT_ROOT:-$ROOT_DIR/tmp/qa/v4-complete-functional-code-gate}"
OUTPUT_DIR="$OUTPUT_ROOT/$RUN_ID"
STEPS_PATH="$OUTPUT_DIR/steps.jsonl"
MANIFEST_PATH="$OUTPUT_DIR/manifest.json"
REPORT_PATH="$OUTPUT_DIR/report.md"
RUN_FULL_VERIFY="${RUN_FULL_VERIFY:-1}"

mkdir -p "$OUTPUT_DIR/logs"
: > "$STEPS_PATH"
cd "$ROOT_DIR"

record_step() {
  local name="$1"
  local log_path="$2"
  STEP_NAME="$name" STEP_LOG="$log_path" "$PYTHON_BIN" - "$STEPS_PATH" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

with open(sys.argv[1], "a", encoding="utf-8") as handle:
    handle.write(json.dumps({
        "name": os.environ["STEP_NAME"],
        "status": "passed",
        "log": os.environ["STEP_LOG"],
        "recordedAt": datetime.now(timezone.utc).isoformat(),
    }, ensure_ascii=False, sort_keys=True) + "\n")
PY
}

run_step() {
  local name="$1"
  shift
  local log_path="$OUTPUT_DIR/logs/${name}.log"
  printf '== %s ==\n' "$name"
  if "$@" >"$log_path" 2>&1; then
    record_step "$name" "$log_path"
  else
    tail -n 100 "$log_path" >&2 || true
    exit 1
  fi
}

if [[ "$RUN_FULL_VERIFY" == "1" ]]; then
  run_step backend-full-verify bash scripts/verify_backend.sh
fi

run_step identity-provider bash scripts/run-backend-identity-challenge-provider-gate.sh
run_step media-provider-matrix bash scripts/run-backend-owner-truth-media-provider-matrix-gate.sh
run_step media-processing bash scripts/run-backend-owner-truth-media-processing-gate.sh
run_step worker-process bash scripts/run-backend-owner-truth-worker-process-gate.sh
run_step ownership-cutover bash scripts/run-backend-owner-truth-cutover-admission-shadow-gate.sh
run_step migration-context-authority bash scripts/run-backend-owner-truth-c2-c3-authority-gate.sh
run_step family-rights-pilot-notification bash scripts/run-backend-v4-family-rights-pilot-notification-gate.sh
run_step apns-foundation bash scripts/run-backend-apns-foundation-gate.sh
run_step voice-clone-c0 bash scripts/run-backend-voice-clone-c0-gate.sh
run_step voice-clone-c1 bash scripts/run-backend-voice-clone-c1-lifecycle-gate.sh
run_step voice-provider-boundary bash scripts/run-backend-voice-clone-r5-provider-boundary-gate.sh
run_step voice-echo-binding bash scripts/run-backend-voice-clone-r5-echo-binding-gate.sh
run_step voice-digital-human-readiness bash scripts/run-backend-voice-dh-lane-readiness-g0-gate.sh
run_step publication-closed-beta bash scripts/run-backend-publication-closed-beta-formal-api-gate.sh
run_step publication-visitor bash scripts/run-backend-publication-visitor-access-gate.sh
run_step publication-exit-readiness bash scripts/run-backend-publication-canary-exit-readiness-g0-gate.sh
run_step worktree-diff-check git diff --check

V4_GATE_RUN_ID="$RUN_ID" \
V4_GATE_ROOT="$ROOT_DIR" \
V4_GATE_RUN_FULL_VERIFY="$RUN_FULL_VERIFY" \
  "$PYTHON_BIN" - "$STEPS_PATH" "$MANIFEST_PATH" "$REPORT_PATH" <<'PY'
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

steps_path, manifest_path, report_path = map(Path, sys.argv[1:])
steps = [json.loads(line) for line in steps_path.read_text(encoding="utf-8").splitlines()]
root = Path(os.environ["V4_GATE_ROOT"])
commit = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
external = [
    {"id": "realOtpProvider", "state": "CONFIG_REQUIRED"},
    {"id": "privateCosBucket", "state": "CONFIG_REQUIRED"},
    {"id": "apnsCredentialAndDeviceReceipt", "state": "CONFIG_AND_DEVICE_REQUIRED"},
    {"id": "voiceIdentityAndProviderReceipt", "state": "EXTERNAL_PROVIDER_REQUIRED"},
    {"id": "m2LegalSafetyPilotApproval", "state": "PRODUCT_AND_LEGAL_REQUIRED"},
]
manifest = {
    "schemaVersion": "dreamjourney-v4-functional-code-gate-v1",
    "runId": os.environ["V4_GATE_RUN_ID"],
    "generatedAt": datetime.now(timezone.utc).isoformat(),
    "backendCommit": commit,
    "status": "passedCodeGate",
    "fullVerifyIncluded": os.environ["V4_GATE_RUN_FULL_VERIFY"] == "1",
    "steps": steps,
    "remainingExternalGates": external,
}
manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
lines = [
    "# DreamJourney V4 Functional Code Gate",
    "",
    f"- Status: `{manifest['status']}`",
    f"- Backend commit: `{commit}`",
    f"- Full verify included: `{manifest['fullVerifyIncluded']}`",
    "",
    "## Passed",
    *[f"- `{step['name']}`" for step in steps],
    "",
    "## External gates still required",
    *[f"- `{item['id']}`: `{item['state']}`" for item in external],
]
report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY

printf 'V4 complete functional code gate passed: %s\n' "$REPORT_PATH"
