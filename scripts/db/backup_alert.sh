#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
[[ -x "$PYTHON_BIN" ]] || PYTHON_BIN="python3"

UNIT_NAME="${1:-dreamjourney-db-backup.service}"
BACKUP_ROOT="${BACKUP_ROOT:-/var/backups/dreamjourney/postgres}"
BACKUP_ALERT_ROOT="${BACKUP_ALERT_ROOT:-$BACKUP_ROOT/alerts}"
BACKUP_ALERT_OWNER="${BACKUP_ALERT_OWNER:-backend-operations}"

"$PYTHON_BIN" "$ROOT_DIR/scripts/db/backup_alert.py" \
  --alert-root "$BACKUP_ALERT_ROOT" \
  --unit "$UNIT_NAME" \
  --owner "$BACKUP_ALERT_OWNER"

if command -v logger >/dev/null 2>&1; then
  logger -t dreamjourney-db-backup "databaseBackupFailed owner=$BACKUP_ALERT_OWNER unit=$UNIT_NAME"
fi
