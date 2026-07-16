#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
[[ -x "$PYTHON_BIN" ]] || PYTHON_BIN="python3"

systemctl is-enabled --quiet dreamjourney-db-backup.timer
systemctl is-active --quiet dreamjourney-db-backup.timer
systemctl is-enabled --quiet dreamjourney-db-backup-retention-audit.timer
systemctl is-active --quiet dreamjourney-db-backup-retention-audit.timer
[[ -n "$(systemctl show dreamjourney-db-backup.service -p OnFailure --value)" ]]

cd "$ROOT_DIR"
BACKUP_TIMER_VERIFIED=1 "$PYTHON_BIN" scripts/db/backup-deployed-smoke.py
