#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
[[ -x "$PYTHON_BIN" ]] || PYTHON_BIN="python3"
BACKUP_ROOT="${BACKUP_ROOT:-/var/backups/dreamjourney/postgres}"
BACKUP_KEEP_MINIMUM="${BACKUP_KEEP_MINIMUM:-1}"

mkdir -p "$BACKUP_ROOT"
chmod 700 "$BACKUP_ROOT"
"$PYTHON_BIN" "$ROOT_DIR/scripts/db/audit_backup_retention.py" \
  "$BACKUP_ROOT" \
  --keep-minimum "$BACKUP_KEEP_MINIMUM" \
  --output "$BACKUP_ROOT/retention-latest.json"
