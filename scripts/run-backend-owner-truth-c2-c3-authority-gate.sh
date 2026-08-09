#!/usr/bin/env bash
set -euo pipefail

# Non-device C2/C3 gate. Existing migration stages remain observation-only and
# default-off; the only runtime path exercised here is the closed-pilot Context
# authority contract, which must never read or fall back to legacy memory.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"

PYTHON_BIN="$PYTHON_BIN" scripts/run-backend-owner-truth-legacy-backfill-g0-gate.sh
PYTHON_BIN="$PYTHON_BIN" scripts/run-backend-owner-truth-migration-parity-shadow-g0-gate.sh
PYTHON_BIN="$PYTHON_BIN" scripts/run-backend-owner-truth-migration-canary-g0-gate.sh
PYTHON_BIN="$PYTHON_BIN" scripts/run-backend-owner-truth-migration-lane-activation-g0-gate.sh
PYTHON_BIN="$PYTHON_BIN" scripts/run-backend-owner-truth-migration-retirement-candidate-g0-gate.sh
PYTHON_BIN="$PYTHON_BIN" scripts/run-backend-owner-truth-migration-removal-authorization-g0-gate.sh
PYTHON_BIN="$PYTHON_BIN" scripts/run-backend-owner-truth-context-authority-gate.sh

PYTHONPATH=. "$PYTHON_BIN" - <<'PY'
from app.core.config import Settings
from app.services.owner_truth_context_authority import (
    OWNER_TRUTH_CONTEXT_AUTHORITY_COHORT,
    OWNER_TRUTH_CONTEXT_FALLBACK_POLICY,
)

settings = Settings()
assert not settings.owner_truth_context_authority_closed_pilot_enabled
assert OWNER_TRUTH_CONTEXT_AUTHORITY_COHORT == "closedPilotAdultSelf"
assert OWNER_TRUTH_CONTEXT_FALLBACK_POLICY == "failClosedNoLegacy"
print("Owner Truth C2/C3 parity, retirement, and Context authority gate passed")
PY
