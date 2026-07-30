#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="${PYTHON_BIN_FALLBACK:-python3}"
fi

cd "$ROOT_DIR"

PYTHONPATH=. "$PYTHON_BIN" -m unittest -q \
  tests.test_owner_truth_authority_epoch_offline_evaluation

PYTHONPATH=. "$PYTHON_BIN" - <<'PY'
import json
from pathlib import Path

fixture = Path("tests/fixtures/owner_truth/authority_epoch_offline_evaluation_v1.json")
payload = json.loads(fixture.read_text(encoding="utf-8"))
assert payload["schemaVersion"] == "owner-truth-authority-epoch-offline-evaluation-v1"
assert payload["syntheticOnly"] is True
assert len(payload["cases"]) == 7
print("Owner Truth authority-epoch offline evaluation gate passed")
PY
