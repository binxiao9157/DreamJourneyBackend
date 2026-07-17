#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
[[ -x "$PYTHON_BIN" ]] || PYTHON_BIN="python3"

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
schema_head="$($PYTHON_BIN -c 'import json,sys; print(json.load(open(sys.argv[1]))["schemaHead"])' "$RECOVERY_MANIFEST_PATH")"

"$PYTHON_BIN" scripts/db/verify_recovery_integrity.py \
  --dsn "$RECOVERY_DATABASE_URL" \
  --backup-id "$backup_id" \
  --cutoff-lsn "$cutoff_lsn" \
  --target-database "$RECOVERY_TARGET_DB" \
  --production-database "$RECOVERY_PRODUCTION_DB" \
  --expected-schema-head "$schema_head" \
  --output "$RECOVERY_OUTPUT_DIR/integrity-evidence.json" >/dev/null

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
  --integrity-evidence "$RECOVERY_OUTPUT_DIR/integrity-evidence.json" \
  --replay-evidence "$RECOVERY_OUTPUT_DIR/replay-evidence.json" \
  --target-database "$RECOVERY_TARGET_DB" \
  --production-database "$RECOVERY_PRODUCTION_DB" \
  --recovery-id "$recovery_id" \
  --expected-cutover "$RECOVERY_EXPECTED_CUTOVER" \
  --output "$RECOVERY_OUTPUT_DIR/recovery-record.json"
