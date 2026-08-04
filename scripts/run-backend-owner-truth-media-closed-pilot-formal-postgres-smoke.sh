#!/usr/bin/env bash
set -euo pipefail

# This creates and drops a disposable Postgres database. It verifies the
# formal closed-pilot Stage 2 chain without touching production business rows.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

[[ "${DREAMJOURNEY_OWNER_TRUTH_MEDIA_FORMAL_SMOKE:-}" == "1" ]] || {
  echo "DREAMJOURNEY_OWNER_TRUTH_MEDIA_FORMAL_SMOKE=1 is required" >&2
  exit 1
}

: "${OWNER_TRUTH_FORMAL_SMOKE_ADMIN_DATABASE_URL:=${DATABASE_URL:-}}"
: "${OWNER_TRUTH_FORMAL_SMOKE_ADMIN_DATABASE_URL:?OWNER_TRUTH_FORMAL_SMOKE_ADMIN_DATABASE_URL or DATABASE_URL is required}"

cd "$ROOT_DIR"
DATABASE_URL="$OWNER_TRUTH_FORMAL_SMOKE_ADMIN_DATABASE_URL" \
  scripts/run-backend-owner-truth-media-processing-postgres-smoke.sh
