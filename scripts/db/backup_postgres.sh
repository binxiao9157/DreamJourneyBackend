#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
[[ -x "$PYTHON_BIN" ]] || PYTHON_BIN="python3"
DOCKER_BIN="${DOCKER_BIN:-docker}"
OPENSSL_BIN="${OPENSSL_BIN:-openssl}"
BACKUP_ROOT="${BACKUP_ROOT:-/var/backups/dreamjourney/postgres}"
BACKUP_DB_SERVICE="${BACKUP_DB_SERVICE:-postgres}"
BACKUP_DB_USER="${BACKUP_DB_USER:-dreamjourney}"
BACKUP_DB_NAME="${BACKUP_DB_NAME:-dreamjourney}"
BACKUP_ENCRYPTION_KEY_FILE="${BACKUP_ENCRYPTION_KEY_FILE:-}"
BACKUP_ENCRYPTION_REF="${BACKUP_ENCRYPTION_REF:-notConfigured:v0}"
BACKUP_ALLOW_UNENCRYPTED="${BACKUP_ALLOW_UNENCRYPTED:-0}"
BACKUP_RETENTION_CLASS="${BACKUP_RETENTION_CLASS:-operationalBackup35d}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-35}"
BACKUP_MIN_FREE_BYTES="${BACKUP_MIN_FREE_BYTES:-104857600}"
BACKUP_ALERT_OWNER="${BACKUP_ALERT_OWNER:-backend-operations}"

mkdir -p "$BACKUP_ROOT" "$BACKUP_ROOT/failures" "$BACKUP_ROOT/alerts"
chmod 700 "$BACKUP_ROOT" "$BACKUP_ROOT/failures" "$BACKUP_ROOT/alerts"
lock_dir="$BACKUP_ROOT/.backup.lock.d"
if ! mkdir "$lock_dir" 2>/dev/null; then
  echo '{"status":"skipped","reason":"backupAlreadyRunning"}'
  exit 0
fi
trap 'rmdir "$lock_dir" 2>/dev/null || true' EXIT

created_at="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"
backup_id="dj-$(date -u +%Y%m%dT%H%M%SZ)-$($OPENSSL_BIN rand -hex 4)"
schema_head="unknown"
lsn="unknown"
failure_code="backupCommandFailed"
plain_partial="$BACKUP_ROOT/.${backup_id}.dump.partial"
encrypted_partial="$BACKUP_ROOT/.${backup_id}.dump.enc.partial"
artifact_path=""
manifest_path=""

write_failure() {
  local status="${1:-1}"
  trap - ERR INT TERM
  rm -f "$plain_partial" "$encrypted_partial"
  [[ -z "$artifact_path" ]] || rm -f "$artifact_path"
  [[ -z "$manifest_path" ]] || rm -f "$manifest_path"
  "$PYTHON_BIN" "$ROOT_DIR/scripts/db/backup_manifest.py" failed \
    --manifest "$BACKUP_ROOT/failures/${backup_id}.failure.json" \
    --backup-id "$backup_id" \
    --created-at "$created_at" \
    --schema-head "$schema_head" \
    --lsn "$lsn" \
    --encryption-ref "$BACKUP_ENCRYPTION_REF" \
    --retention-class "$BACKUP_RETENTION_CLASS" \
    --error-code "$failure_code" \
    --owner "$BACKUP_ALERT_OWNER" >/dev/null 2>&1 || true
  exit "$status"
}

interrupt_backup() {
  failure_code="backupInterrupted"
  write_failure 130
}

trap 'write_failure $?' ERR
trap interrupt_backup INT TERM

psql_value() {
  "$DOCKER_BIN" compose exec -T "$BACKUP_DB_SERVICE" \
    psql -U "$BACKUP_DB_USER" -d "$BACKUP_DB_NAME" -Atqc "$1"
}

failure_code="databaseMetadataUnavailable"
schema_head="$(psql_value "SELECT version FROM schema_migrations WHERE state = 'applied' ORDER BY version DESC LIMIT 1")"
[[ "$schema_head" =~ ^[0-9]{4,}$ ]]
lsn="$(psql_value "SELECT pg_current_wal_lsn()")"
[[ "$lsn" =~ ^[0-9A-F]+/[0-9A-F]+$ ]]
database_size="$(psql_value "SELECT pg_database_size(current_database())")"
[[ "$database_size" =~ ^[0-9]+$ ]]

failure_code="insufficientSpace"
required_bytes=$((database_size * 2))
if (( required_bytes < BACKUP_MIN_FREE_BYTES )); then
  required_bytes="$BACKUP_MIN_FREE_BYTES"
fi
available_bytes="$($PYTHON_BIN -c 'import shutil,sys; print(shutil.disk_usage(sys.argv[1]).free)' "$BACKUP_ROOT")"
(( available_bytes >= required_bytes ))

failure_code="pgDumpFailed"
"$DOCKER_BIN" compose exec -T "$BACKUP_DB_SERVICE" \
  pg_dump -U "$BACKUP_DB_USER" -d "$BACKUP_DB_NAME" \
  --format=custom --compress=6 --no-owner --no-acl > "$plain_partial"
[[ -s "$plain_partial" ]]

if [[ -n "$BACKUP_ENCRYPTION_KEY_FILE" ]]; then
  failure_code="encryptionKeyUnavailable"
  [[ -r "$BACKUP_ENCRYPTION_KEY_FILE" ]]
  failure_code="backupEncryptionFailed"
  "$OPENSSL_BIN" enc -aes-256-cbc -pbkdf2 -salt \
    -pass "file:$BACKUP_ENCRYPTION_KEY_FILE" \
    -in "$plain_partial" -out "$encrypted_partial"
  rm -f "$plain_partial"
  artifact_path="$BACKUP_ROOT/${backup_id}.dump.enc"
  mv "$encrypted_partial" "$artifact_path"
elif [[ "$BACKUP_ALLOW_UNENCRYPTED" == "1" ]]; then
  artifact_path="$BACKUP_ROOT/${backup_id}.dump"
  mv "$plain_partial" "$artifact_path"
else
  failure_code="encryptionKeyMissing"
  false
fi
chmod 600 "$artifact_path"

failure_code="backupArchiveVerificationFailed"
if [[ "$artifact_path" == *.enc ]]; then
  "$OPENSSL_BIN" enc -d -aes-256-cbc -pbkdf2 \
    -pass "file:$BACKUP_ENCRYPTION_KEY_FILE" \
    -in "$artifact_path" \
    | "$DOCKER_BIN" compose exec -T "$BACKUP_DB_SERVICE" pg_restore --list >/dev/null
else
  "$DOCKER_BIN" compose exec -T "$BACKUP_DB_SERVICE" pg_restore --list \
    < "$artifact_path" >/dev/null
fi

completed_at="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"
manifest_path="$BACKUP_ROOT/${backup_id}.manifest.json"
failure_code="manifestWriteFailed"
"$PYTHON_BIN" "$ROOT_DIR/scripts/db/backup_manifest.py" complete \
  --manifest "$manifest_path" \
  --artifact "$artifact_path" \
  --backup-id "$backup_id" \
  --created-at "$created_at" \
  --completed-at "$completed_at" \
  --schema-head "$schema_head" \
  --lsn "$lsn" \
  --encryption-ref "$BACKUP_ENCRYPTION_REF" \
  --retention-class "$BACKUP_RETENTION_CLASS" \
  --retention-days "$BACKUP_RETENTION_DAYS" >/dev/null

failure_code="manifestVerificationFailed"
"$PYTHON_BIN" "$ROOT_DIR/scripts/db/verify_backup_manifest.py" \
  "$manifest_path" --expected-schema-head "$schema_head" >/dev/null

trap - ERR INT TERM
printf '{"status":"verified","backupId":"%s","schemaHead":"%s"}\n' \
  "$backup_id" "$schema_head"
