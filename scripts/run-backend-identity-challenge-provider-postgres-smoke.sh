#!/usr/bin/env bash
set -euo pipefail

# Creates and removes a dedicated database. It never calls an external SMS
# endpoint: the provider transport is an in-process accepted/rejected fake.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

[[ "${IDENTITY_CHALLENGE_PROVIDER_POSTGRES_SMOKE:-}" == "1" ]] || {
  echo "IDENTITY_CHALLENGE_PROVIDER_POSTGRES_SMOKE=1 is required" >&2
  exit 1
}

: "${IDENTITY_CHALLENGE_PROVIDER_SMOKE_DATABASE_URL:=${DATABASE_URL:-}}"
: "${IDENTITY_CHALLENGE_PROVIDER_SMOKE_DATABASE_URL:?IDENTITY_CHALLENGE_PROVIDER_SMOKE_DATABASE_URL or DATABASE_URL is required}"

cd "$ROOT_DIR"
DATABASE_URL="$IDENTITY_CHALLENGE_PROVIDER_SMOKE_DATABASE_URL" \
  PYTHONPATH=. "$PYTHON_BIN" scripts/backend-identity-challenge-provider-postgres-smoke.py
