#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
[[ -x "$PYTHON_BIN" ]] || PYTHON_BIN="python3"
DOCKER_BIN="${DOCKER_BIN:-docker}"
OPENSSL_BIN="${OPENSSL_BIN:-openssl}"
RECOVERY_MANIFEST_PATH="${RECOVERY_MANIFEST_PATH:?RECOVERY_MANIFEST_PATH is required}"
RECOVERY_TARGET_DB="${RECOVERY_TARGET_DB:?RECOVERY_TARGET_DB is required}"
RECOVERY_PRODUCTION_DB="${RECOVERY_PRODUCTION_DB:-dreamjourney}"
RECOVERY_DATABASE_URL="${RECOVERY_DATABASE_URL:?RECOVERY_DATABASE_URL for the isolated target is required}"
RECOVERY_DB_SERVICE="${RECOVERY_DB_SERVICE:-postgres}"
RECOVERY_DB_USER="${RECOVERY_DB_USER:-dreamjourney}"
RECOVERY_OUTPUT_DIR="${RECOVERY_OUTPUT_DIR:?RECOVERY_OUTPUT_DIR is required}"
RECOVERY_ENCRYPTION_KEY_FILE="${RECOVERY_ENCRYPTION_KEY_FILE:-}"
RECOVERY_ALLOW_UNENCRYPTED="${RECOVERY_ALLOW_UNENCRYPTED:-0}"
RECOVERY_ALLOW_DROP_ISOLATED="${RECOVERY_ALLOW_DROP_ISOLATED:-0}"
RECOVERY_BUILD_ID="${RECOVERY_BUILD_ID:-recovery-drill}"

mkdir -p "$RECOVERY_OUTPUT_DIR"
chmod 700 "$RECOVERY_OUTPUT_DIR"

"$PYTHON_BIN" - "$RECOVERY_TARGET_DB" "$RECOVERY_PRODUCTION_DB" "$RECOVERY_DATABASE_URL" <<'PY'
import sys
from urllib.parse import urlparse

from app.db.recovery import validate_recovery_target

target = validate_recovery_target(sys.argv[1], sys.argv[2])
parsed = urlparse(sys.argv[3])
dsn_database = parsed.path.lstrip("/").lower()
if parsed.scheme not in {"postgres", "postgresql"} or dsn_database != target:
    raise SystemExit("unsafeRecoveryDatabaseURL")
PY

manifest_report="$RECOVERY_OUTPUT_DIR/manifest-verification.json"
"$PYTHON_BIN" scripts/db/verify_backup_manifest.py \
  "$RECOVERY_MANIFEST_PATH" > "$manifest_report"

manifest_fields_file="$RECOVERY_OUTPUT_DIR/manifest-fields.txt"
"$PYTHON_BIN" - "$RECOVERY_MANIFEST_PATH" > "$manifest_fields_file" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1]).resolve()
payload = json.loads(path.read_text(encoding="utf-8"))
for key in ("backupId", "schemaHead", "lsn", "checksum", "completedAt", "artifactFile"):
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise SystemExit(f"manifestFieldMissing:{key}")
    print(value)
artifact = (path.parent / payload["artifactFile"]).resolve()
if artifact.parent != path.parent:
    raise SystemExit("artifactReferenceInvalid")
print(artifact)
PY

backup_id="$(sed -n '1p' "$manifest_fields_file")"
schema_head="$(sed -n '2p' "$manifest_fields_file")"
cutoff_lsn="$(sed -n '3p' "$manifest_fields_file")"
artifact_checksum="$(sed -n '4p' "$manifest_fields_file")"
backup_completed_at="$(sed -n '5p' "$manifest_fields_file")"
artifact_path="$(sed -n '7p' "$manifest_fields_file")"
rm -f "$manifest_fields_file"
started_at="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"
temporary_dir="$(mktemp -d "${TMPDIR:-/tmp}/dreamjourney-recovery.XXXXXX")"
plain_dump="$temporary_dir/recovery.dump"
trap 'rm -rf "$temporary_dir"' EXIT

if [[ "$artifact_path" == *.enc ]]; then
  [[ -n "$RECOVERY_ENCRYPTION_KEY_FILE" && -r "$RECOVERY_ENCRYPTION_KEY_FILE" ]]
  "$OPENSSL_BIN" enc -d -aes-256-cbc -pbkdf2 \
    -pass "file:$RECOVERY_ENCRYPTION_KEY_FILE" \
    -in "$artifact_path" -out "$plain_dump"
elif [[ "$RECOVERY_ALLOW_UNENCRYPTED" == "1" ]]; then
  cp "$artifact_path" "$plain_dump"
else
  echo "unencryptedRecoveryArtifactForbidden" >&2
  exit 1
fi
chmod 600 "$plain_dump"

"$DOCKER_BIN" compose exec -T "$RECOVERY_DB_SERVICE" \
  pg_restore --list < "$plain_dump" >/dev/null

database_exists="$($DOCKER_BIN compose exec -T "$RECOVERY_DB_SERVICE" \
  psql -U "$RECOVERY_DB_USER" -d postgres -Atqc \
  "SELECT 1 FROM pg_database WHERE datname = '$RECOVERY_TARGET_DB'")"
if [[ "$database_exists" == "1" ]]; then
  if [[ "$RECOVERY_ALLOW_DROP_ISOLATED" != "1" ]]; then
    echo "isolatedRecoveryTargetAlreadyExists" >&2
    exit 1
  fi
  "$DOCKER_BIN" compose exec -T "$RECOVERY_DB_SERVICE" \
    psql -U "$RECOVERY_DB_USER" -d postgres -v ON_ERROR_STOP=1 -c \
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$RECOVERY_TARGET_DB' AND pid <> pg_backend_pid();"
  "$DOCKER_BIN" compose exec -T "$RECOVERY_DB_SERVICE" \
    dropdb -U "$RECOVERY_DB_USER" "$RECOVERY_TARGET_DB"
fi

"$DOCKER_BIN" compose exec -T "$RECOVERY_DB_SERVICE" \
  createdb -U "$RECOVERY_DB_USER" "$RECOVERY_TARGET_DB"
"$DOCKER_BIN" compose exec -T "$RECOVERY_DB_SERVICE" \
  pg_restore -U "$RECOVERY_DB_USER" -d "$RECOVERY_TARGET_DB" \
  --no-owner --no-acl --exit-on-error < "$plain_dump"

migration_report="$RECOVERY_OUTPUT_DIR/migration-apply.json"
"$DOCKER_BIN" compose run --rm -T \
  -e "DATABASE_URL=$RECOVERY_DATABASE_URL" \
  -e "DEPLOY_BUILD_ID=$RECOVERY_BUILD_ID" \
  api python scripts/migrate_db.py --apply > "$migration_report"
"$DOCKER_BIN" compose run --rm -T \
  -e "DATABASE_URL=$RECOVERY_DATABASE_URL" \
  -e "DEPLOY_BUILD_ID=$RECOVERY_BUILD_ID" \
  api python scripts/migrate_db.py --verify > "$RECOVERY_OUTPUT_DIR/migration-verify.json"

completed_at="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"
restore_evidence="$RECOVERY_OUTPUT_DIR/restore-evidence.json"
"$PYTHON_BIN" - \
  "$RECOVERY_MANIFEST_PATH" "$backup_id" "$schema_head" "$cutoff_lsn" \
  "$artifact_checksum" "$backup_completed_at" "$started_at" "$completed_at" \
  "$RECOVERY_TARGET_DB" "$RECOVERY_PRODUCTION_DB" "$migration_report" "$restore_evidence" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

from app.db.recovery import build_restore_evidence, write_recovery_record_atomic

(
    manifest_path,
    backup_id,
    schema_head,
    cutoff_lsn,
    artifact_checksum,
    backup_completed_at,
    started_at,
    completed_at,
    target_database,
    production_database,
    migration_report_path,
    output_path,
) = sys.argv[1:]

def digest_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def canonical_hash(payload):
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

migration = json.loads(Path(migration_report_path).read_text(encoding="utf-8"))
if migration.get("status") != "ready" or migration.get("expectedHead") != migration.get("appliedHead"):
    raise SystemExit("migrationHeadNotReady")
payload = build_restore_evidence(
    backup_id=backup_id,
    backup_checksum=artifact_checksum,
    backup_completed_at=backup_completed_at,
    schema_head=schema_head,
    cutoff_lsn=cutoff_lsn,
    started_at=started_at,
    completed_at=completed_at,
    target_database=target_database,
    production_database=production_database,
    source_manifest_digest=digest_file(manifest_path),
    migration_evidence_id=canonical_hash(migration),
)
write_recovery_record_atomic(Path(output_path), payload)
print(json.dumps({"status": "restored", "backupId": backup_id, "evidenceId": payload["evidenceId"]}, sort_keys=True))
PY

cat "$restore_evidence"
