#!/usr/bin/env bash
set -euo pipefail

# This is the V4 M0 non-device handoff gate.  It intentionally composes the
# existing focused tests instead of inventing a second implementation path.
# A plain invocation is fail-closed: it requires both an isolated Postgres DSN
# and a deployed API contract.  Developers may opt out of either leg only for
# local iteration, in which case the generated manifest is marked incomplete.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
[[ -x "$PYTHON_BIN" ]] || PYTHON_BIN="python3"

RUN_ID="${RUN_ID:-$(date +%Y%m%d-%H%M%S)-v4-m0-non-device}"
OUTPUT_ROOT="${OUTPUT_ROOT:-$ROOT_DIR/tmp/qa/v4-m0-non-device-release-gate}"
OUTPUT_DIR="$OUTPUT_ROOT/$RUN_ID"
STEP_FILE="$OUTPUT_DIR/steps.jsonl"
REPORT_PATH="$OUTPUT_DIR/report.md"
MANIFEST_PATH="$OUTPUT_DIR/manifest.json"

RUN_FULL_VERIFY="${RUN_FULL_VERIFY:-1}"
RUN_ISOLATED_POSTGRES="${RUN_ISOLATED_POSTGRES:-1}"
RUN_DEPLOYED="${RUN_DEPLOYED:-1}"
RUN_WORKTREE_DIFF_CHECK="${RUN_WORKTREE_DIFF_CHECK:-$RUN_FULL_VERIFY}"
FULL_VERIFY_EVIDENCE_PATH="${FULL_VERIFY_EVIDENCE_PATH:-}"
BACKEND_COMMIT="${BACKEND_COMMIT:-}"

BACKEND_BASE_URL="${BACKEND_BASE_URL:-${DREAMJOURNEY_BACKEND_BASE_URL:-}}"
BACKEND_API_TOKEN="${BACKEND_API_TOKEN:-${DREAMJOURNEY_BACKEND_API_TOKEN:-}}"
DATABASE_URL="${DATABASE_URL:-}"
OWNER_TRUTH_FORMAL_SMOKE_ADMIN_DATABASE_URL="${OWNER_TRUTH_FORMAL_SMOKE_ADMIN_DATABASE_URL:-$DATABASE_URL}"

mkdir -p "$OUTPUT_DIR/logs"
: > "$STEP_FILE"

record_step() {
  local name="$1"
  local status="$2"
  local log_path="$3"
  STEP_NAME="$name" STEP_STATUS="$status" STEP_LOG_PATH="$log_path" \
    "$PYTHON_BIN" - "$STEP_FILE" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

path = sys.argv[1]
record = {
    "name": os.environ["STEP_NAME"],
    "status": os.environ["STEP_STATUS"],
    "log": os.environ["STEP_LOG_PATH"],
    "recordedAt": datetime.now(timezone.utc).isoformat(),
}
with open(path, "a", encoding="utf-8") as handle:
    handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
PY
}

run_step() {
  local name="$1"
  shift
  local log_path="$OUTPUT_DIR/logs/${name//[^A-Za-z0-9._-]/_}.log"
  printf '== %s ==\n' "$name"
  if "$@" >"$log_path" 2>&1; then
    record_step "$name" "passed" "$log_path"
  else
    local code=$?
    record_step "$name" "failed" "$log_path"
    tail -80 "$log_path" >&2 || true
    exit "$code"
  fi
}

require_deployed_environment() {
  [[ -n "$BACKEND_BASE_URL" ]] || {
    printf '%s\n' 'BACKEND_BASE_URL is required for RUN_DEPLOYED=1' >&2
    exit 2
  }
  [[ -n "$BACKEND_API_TOKEN" ]] || {
    printf '%s\n' 'BACKEND_API_TOKEN is required for RUN_DEPLOYED=1' >&2
    exit 2
  }
}

require_isolated_postgres_environment() {
  [[ -n "$DATABASE_URL" ]] || {
    printf '%s\n' 'DATABASE_URL is required for RUN_ISOLATED_POSTGRES=1' >&2
    exit 2
  }
  [[ -n "$OWNER_TRUTH_FORMAL_SMOKE_ADMIN_DATABASE_URL" ]] || {
    printf '%s\n' 'OWNER_TRUTH_FORMAL_SMOKE_ADMIN_DATABASE_URL is required for formal confirmation smoke' >&2
    exit 2
  }
}

verify_external_full_verify_evidence() {
  [[ -n "$FULL_VERIFY_EVIDENCE_PATH" ]] || {
    printf '%s\n' 'FULL_VERIFY_EVIDENCE_PATH is required when RUN_FULL_VERIFY=0' >&2
    exit 2
  }
  [[ -f "$FULL_VERIFY_EVIDENCE_PATH" ]] || {
    printf 'Full verify evidence does not exist: %s\n' "$FULL_VERIFY_EVIDENCE_PATH" >&2
    exit 2
  }
  FULL_VERIFY_EVIDENCE_PATH="$FULL_VERIFY_EVIDENCE_PATH" \
  BACKEND_COMMIT="$BACKEND_COMMIT" \
    "$PYTHON_BIN" - <<'PY'
import json
import os
from pathlib import Path

payload = json.loads(Path(os.environ["FULL_VERIFY_EVIDENCE_PATH"]).read_text(encoding="utf-8"))
config = payload.get("configuration") or {}
steps = {str(step.get("name")): str(step.get("status")) for step in payload.get("steps") or []}
if config.get("fullVerify") is not True:
    raise SystemExit("full verify evidence did not run the complete backend verifier")
for name in ("backend-verify", "backend-git-diff-check"):
    if steps.get(name) != "passed":
        raise SystemExit(f"full verify evidence missing passing {name}")
expected_commit = os.environ.get("BACKEND_COMMIT", "").strip()
if expected_commit and payload.get("backendCommit") != expected_commit:
    raise SystemExit("full verify evidence commit does not match deployed commit")
PY
}

finalize_evidence() {
  local exit_status=$?
  V4_ROOT_DIR="$ROOT_DIR" \
  V4_RUN_ID="$RUN_ID" \
  V4_OUTPUT_DIR="$OUTPUT_DIR" \
  V4_RUN_FULL_VERIFY="$RUN_FULL_VERIFY" \
  V4_RUN_ISOLATED_POSTGRES="$RUN_ISOLATED_POSTGRES" \
  V4_RUN_DEPLOYED="$RUN_DEPLOYED" \
  V4_RUN_WORKTREE_DIFF_CHECK="$RUN_WORKTREE_DIFF_CHECK" \
  V4_FULL_VERIFY_EVIDENCE_PATH="$FULL_VERIFY_EVIDENCE_PATH" \
  V4_BACKEND_COMMIT="$BACKEND_COMMIT" \
  V4_BACKEND_BASE_URL="$BACKEND_BASE_URL" \
  V4_GATE_EXIT_STATUS="$exit_status" \
  "$PYTHON_BIN" - "$STEP_FILE" "$MANIFEST_PATH" "$REPORT_PATH" <<'PY'
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

steps_path = Path(sys.argv[1])
manifest_path = Path(sys.argv[2])
report_path = Path(sys.argv[3])
root = Path(os.environ["V4_ROOT_DIR"])
steps = [json.loads(line) for line in steps_path.read_text(encoding="utf-8").splitlines() if line.strip()]
commit = os.environ.get("V4_BACKEND_COMMIT", "").strip()
if not commit:
    try:
        commit = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
        ).strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        commit = "unavailable-in-runtime-image"
full_verify_proven = os.environ["V4_RUN_FULL_VERIFY"] == "1" or bool(os.environ["V4_FULL_VERIFY_EVIDENCE_PATH"])
complete = (
    full_verify_proven
    and
    os.environ["V4_RUN_ISOLATED_POSTGRES"] == "1"
    and os.environ["V4_RUN_DEPLOYED"] == "1"
    and os.environ["V4_GATE_EXIT_STATUS"] == "0"
)
status = "passed" if complete else ("failed" if os.environ["V4_GATE_EXIT_STATUS"] != "0" else "incomplete")
manifest = {
    "schemaVersion": "dreamjourney-v4-m0-non-device-evidence-v1",
    "runId": os.environ["V4_RUN_ID"],
    "generatedAt": datetime.now(timezone.utc).isoformat(),
    "backendCommit": commit,
    "backendBaseUrlConfigured": bool(os.environ["V4_BACKEND_BASE_URL"]),
    "configuration": {
        "fullVerify": full_verify_proven,
        "fullVerifyExecutedHere": os.environ["V4_RUN_FULL_VERIFY"] == "1",
        "worktreeDiffExecutedHere": os.environ["V4_RUN_WORKTREE_DIFF_CHECK"] == "1",
        "isolatedPostgres": os.environ["V4_RUN_ISOLATED_POSTGRES"] == "1",
        "deployed": os.environ["V4_RUN_DEPLOYED"] == "1",
    },
    "status": status,
    "steps": steps,
    "remainingGates": [
        {
            "kind": "EXTERNAL_BLOCKED",
            "id": "real-identity-provider",
            "detail": "The deployed refresh smoke proves fail-closed identity admission and fixture-session refresh. Real SMS/identity-provider validation remains external.",
        },
        {
            "kind": "DEVICE_REQUIRED",
            "id": "m0-physical-device-acceptance",
            "detail": "Microphone, photo permission, foreground/background recovery, notification navigation, and device performance require Wave 7.",
        },
    ],
}
manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
lines = [
    "# V4 M0 Non-device Backend Release Gate",
    "",
    f"- Run ID: `{manifest['runId']}`",
    f"- Backend commit: `{commit}`",
    f"- Status: `{manifest['status']}`",
    "",
    "## Passed steps",
]
lines.extend(f"- `{step['name']}`" for step in steps)
lines.extend([
    "",
    "## Remaining gates",
    "- `EXTERNAL_BLOCKED`: real SMS/identity provider validation.",
    "- `DEVICE_REQUIRED`: Wave 7 physical-device acceptance.",
    "",
    f"- Manifest: `{manifest_path.name}`",
])
report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
  return "$exit_status"
}

trap finalize_evidence EXIT

cd "$ROOT_DIR"

if [[ "$RUN_FULL_VERIFY" == "1" ]]; then
  run_step "backend-verify" bash scripts/verify_backend.sh
else
  run_step "external-full-verify-evidence" verify_external_full_verify_evidence
fi
if [[ "$RUN_WORKTREE_DIFF_CHECK" == "1" ]]; then
  run_step "backend-git-diff-check" git diff --check
fi

if [[ "$RUN_ISOLATED_POSTGRES" == "1" ]]; then
  require_isolated_postgres_environment
  run_step "migration-upgrade-replay" env DATABASE_URL="$DATABASE_URL" bash scripts/run-backend-db-migration-postgres-smoke.sh
  run_step "formal-owner-truth-confirmation" env \
    DREAMJOURNEY_OWNER_TRUTH_FORMAL_SMOKE=1 \
    OWNER_TRUTH_FORMAL_SMOKE_ADMIN_DATABASE_URL="$OWNER_TRUTH_FORMAL_SMOKE_ADMIN_DATABASE_URL" \
    bash scripts/run-backend-owner-truth-interview-confirmation-formal-postgres-smoke.sh
  run_step "owner-truth-conversation-retry-replay" env DATABASE_URL="$DATABASE_URL" bash scripts/run-backend-owner-truth-conversation-postgres-smoke.sh
  run_step "owner-truth-family-contribution" env DATABASE_URL="$DATABASE_URL" bash scripts/run-backend-owner-truth-family-contribution-formal-postgres-smoke.sh
  run_step "context-runtime-persistence" env DATABASE_URL="$DATABASE_URL" bash scripts/run-backend-echo-context-reply-runtime-postgres-smoke.sh
  run_step "context-v2-policy" bash scripts/run-echo-context-builder-v2-smoke.sh
fi

if [[ "$RUN_DEPLOYED" == "1" ]]; then
  require_deployed_environment
  run_step "deployed-readiness" env BACKEND_BASE_URL="$BACKEND_BASE_URL" OUTPUT_PATH="$OUTPUT_DIR/ready.json" bash scripts/run-backend-readiness-deployed-smoke.sh
  run_step "deployed-auth-refresh" env BACKEND_BASE_URL="$BACKEND_BASE_URL" BACKEND_AUTH_REFRESH_SMOKE_DIRECT_ISSUE=1 bash scripts/run-backend-auth-refresh-deployed-smoke.sh
  run_step "deployed-natural-input" env BACKEND_BASE_URL="$BACKEND_BASE_URL" DREAMJOURNEY_DEPLOYED_CONTAINER_SMOKE=1 OUTPUT_PATH="$OUTPUT_DIR/natural-input.json" bash scripts/run-backend-owner-truth-interview-natural-input-deployed-smoke.sh
  run_step "deployed-route-authentication" env BACKEND_BASE_URL="$BACKEND_BASE_URL" BACKEND_API_TOKEN="$BACKEND_API_TOKEN" bash scripts/run-backend-route-authentication-postgres-smoke.sh
  run_step "deployed-owner-isolation" env BACKEND_BASE_URL="$BACKEND_BASE_URL" BACKEND_API_TOKEN="$BACKEND_API_TOKEN" DATABASE_URL="$DATABASE_URL" bash scripts/run-backend-resource-authorization-postgres-smoke.sh
  run_step "deployed-data-rights" env BACKEND_BASE_URL="$BACKEND_BASE_URL" DREAMJOURNEY_DEPLOYED_CONTAINER_SMOKE=1 OUTPUT_PATH="$OUTPUT_DIR/data-rights.json" bash scripts/run-backend-account-deletion-rights-deployed-smoke.sh
  run_step "deployed-incident-lifecycle" env BACKEND_BASE_URL="$BACKEND_BASE_URL" BACKEND_API_TOKEN="$BACKEND_API_TOKEN" bash scripts/run-backend-incident-lifecycle-deployed-smoke.sh
  run_step "deployed-public-release-scope" env BACKEND_BASE_URL="$BACKEND_BASE_URL" BACKEND_API_TOKEN="$BACKEND_API_TOKEN" EXPECTED_RELEASE_POLICY_COMMAND_MODE=observe OUTPUT_PATH="$OUTPUT_DIR/public-release-scope.json" bash scripts/run-backend-public-release-scope-deployed-smoke.sh
fi

printf 'V4 M0 non-device backend gate finished: %s\n' "$REPORT_PATH"
