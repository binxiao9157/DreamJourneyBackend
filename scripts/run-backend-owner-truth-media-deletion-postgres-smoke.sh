#!/usr/bin/env bash
set -euo pipefail

# Reuses the Stage 2 disposable Postgres lifecycle smoke, then opts into the
# dedicated physical deletion worker. The temporary database and filesystem
# media root are created and removed by the Python smoke itself.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

: "${DATABASE_URL:?DATABASE_URL is required}"
cd "$ROOT_DIR"
RUN_OWNER_TRUTH_MEDIA_PHYSICAL_DELETION_SMOKE=1 \
  scripts/run-backend-owner-truth-media-processing-postgres-smoke.sh
