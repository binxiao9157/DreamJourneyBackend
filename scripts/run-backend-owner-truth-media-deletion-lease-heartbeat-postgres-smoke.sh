#!/usr/bin/env bash
set -euo pipefail

# Reuses the Stage 2 disposable Postgres lifecycle smoke and opts into the
# blocking-delete lease-heartbeat assertion. The Python smoke also enables the
# physical deletion branch when this narrower regression flag is set.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

: "${DATABASE_URL:?DATABASE_URL is required}"
cd "$ROOT_DIR"
RUN_OWNER_TRUTH_MEDIA_LEASE_HEARTBEAT_SMOKE=1 \
  scripts/run-backend-owner-truth-media-processing-postgres-smoke.sh
