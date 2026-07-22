#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
[[ -x "$PYTHON_BIN" ]] || PYTHON_BIN="python3"
DOCKER_BIN="${DOCKER_BIN:-docker}"

RECOVERY_MANIFEST_PATH="${RECOVERY_MANIFEST_PATH:?RECOVERY_MANIFEST_PATH is required}"
RECOVERY_TARGET_DB="${RECOVERY_TARGET_DB:?RECOVERY_TARGET_DB is required}"
RECOVERY_DATABASE_URL="${RECOVERY_DATABASE_URL:?RECOVERY_DATABASE_URL is required}"
RECOVERY_OUTPUT_DIR="${RECOVERY_OUTPUT_DIR:?RECOVERY_OUTPUT_DIR is required}"
RECOVERY_PRODUCTION_DB="${RECOVERY_PRODUCTION_DB:-dreamjourney}"
RECOVERY_EXPECTED_CUTOVER="${RECOVERY_EXPECTED_CUTOVER:-NO_GO}"

cd "$ROOT_DIR"
mkdir -p "$RECOVERY_OUTPUT_DIR"
chmod 700 "$RECOVERY_OUTPUT_DIR"

scripts/db/restore_postgres.sh >/dev/null

backup_id="$($PYTHON_BIN -c 'import json,sys; print(json.load(open(sys.argv[1]))["backupId"])' "$RECOVERY_MANIFEST_PATH")"
cutoff_lsn="$($PYTHON_BIN -c 'import json,sys; print(json.load(open(sys.argv[1]))["lsn"])' "$RECOVERY_MANIFEST_PATH")"
restored_schema_head="$($PYTHON_BIN - "$RECOVERY_OUTPUT_DIR/migration-verify.json" <<'PY'
import json
import re
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
expected = str(payload.get("expectedHead") or "")
applied = str(payload.get("appliedHead") or "")
if payload.get("status") != "ready" or expected != applied or re.fullmatch(r"[0-9]{4,}", expected) is None:
    raise SystemExit("migrationHeadNotReady")
print(expected)
PY
)"

integrity_evidence="$RECOVERY_OUTPUT_DIR/integrity-evidence.json"
integrity_evidence_tmp="$integrity_evidence.tmp"
rm -f "$integrity_evidence_tmp"
DATABASE_URL="$RECOVERY_DATABASE_URL" \
RECOVERY_BACKUP_ID="$backup_id" \
RECOVERY_CUTOFF_LSN="$cutoff_lsn" \
RECOVERY_TARGET_DB="$RECOVERY_TARGET_DB" \
RECOVERY_PRODUCTION_DB="$RECOVERY_PRODUCTION_DB" \
RECOVERY_EXPECTED_SCHEMA_HEAD="$restored_schema_head" \
"$DOCKER_BIN" compose run --rm -T \
  -e DATABASE_URL \
  -e RECOVERY_BACKUP_ID \
  -e RECOVERY_CUTOFF_LSN \
  -e RECOVERY_TARGET_DB \
  -e RECOVERY_PRODUCTION_DB \
  -e RECOVERY_EXPECTED_SCHEMA_HEAD \
  api sh -ec '
    integrity_status=0
    python scripts/db/verify_recovery_integrity.py \
      --dsn "$DATABASE_URL" \
      --backup-id "$RECOVERY_BACKUP_ID" \
      --cutoff-lsn "$RECOVERY_CUTOFF_LSN" \
      --target-database "$RECOVERY_TARGET_DB" \
      --production-database "$RECOVERY_PRODUCTION_DB" \
      --expected-schema-head "$RECOVERY_EXPECTED_SCHEMA_HEAD" \
      --output /tmp/integrity-evidence.json >/dev/null || integrity_status=$?
    if [ ! -s /tmp/integrity-evidence.json ]; then
      exit "$integrity_status"
    fi
    cat /tmp/integrity-evidence.json
    case "$integrity_status" in
      0|2) exit 0 ;;
      *) exit "$integrity_status" ;;
    esac
  ' > "$integrity_evidence_tmp"
mv "$integrity_evidence_tmp" "$integrity_evidence"
chmod 600 "$integrity_evidence"

replay_args=(
  --backup-id "$backup_id"
  --cutoff-lsn "$cutoff_lsn"
  --output "$RECOVERY_OUTPUT_DIR/replay-evidence.json"
)
if [[ -n "${RECOVERY_REPLAY_BUNDLE_PATH:-}" ]]; then
  replay_args+=(--bundle "$RECOVERY_REPLAY_BUNDLE_PATH")
fi
if [[ -n "${RECOVERY_REPLAY_APPLICATION_EVIDENCE_PATH:-}" ]]; then
  replay_args+=(--application-evidence "$RECOVERY_REPLAY_APPLICATION_EVIDENCE_PATH")
fi
"$PYTHON_BIN" scripts/db/replay_recovery.py "${replay_args[@]}" >/dev/null

recovery_id="recovery-$(date -u +%Y%m%dT%H%M%SZ)-$($PYTHON_BIN -c 'import secrets; print(secrets.token_hex(4))')"
"$PYTHON_BIN" scripts/db/recovery-deployed-smoke.py \
  --manifest "$RECOVERY_MANIFEST_PATH" \
  --restore-evidence "$RECOVERY_OUTPUT_DIR/restore-evidence.json" \
  --integrity-evidence "$integrity_evidence" \
  --replay-evidence "$RECOVERY_OUTPUT_DIR/replay-evidence.json" \
  --target-database "$RECOVERY_TARGET_DB" \
  --production-database "$RECOVERY_PRODUCTION_DB" \
  --recovery-id "$recovery_id" \
  --expected-cutover "$RECOVERY_EXPECTED_CUTOVER" \
  --output "$RECOVERY_OUTPUT_DIR/recovery-record.json"
