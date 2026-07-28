#!/usr/bin/env bash
set -euo pipefail

# G0-only. C08 must plan a single independent lane without enabling a route,
# worker, object reference, Provider, global switch or Authority mutation.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
"$PYTHON_BIN" -m unittest tests.test_owner_truth_migration_lane_activation_shadow
"$PYTHON_BIN" -m py_compile app/domain/owner_truth/migration_lane_activation_shadow.py

"$PYTHON_BIN" - <<'PY'
import ast
from pathlib import Path

from app.domain.owner_truth.migration_lane_activation_shadow import (
    MigrationActivationLane,
    OWNER_TRUTH_MIGRATION_LANE_ACTIVATION_SHADOW_SCHEMA_VERSION,
)

assert OWNER_TRUTH_MIGRATION_LANE_ACTIVATION_SHADOW_SCHEMA_VERSION == (
    "owner-truth-migration-lane-activation-shadow-v1"
)
assert len(MigrationActivationLane) == 6

path = Path("app/domain/owner_truth/migration_lane_activation_shadow.py")
source = path.read_text(encoding="utf-8")
tree = ast.parse(source)

for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        module = getattr(node, "module", "") or ""
        assert not module.startswith("app.main"), "lane activation must not import API routes"
        assert not module.startswith("app.async_effects"), "lane activation must not dispatch effects"
        assert not module.startswith("app.services"), "lane activation must not import services"
    if isinstance(node, ast.Call):
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        assert name not in {"request", "post", "put", "delete", "persist", "enqueue"}, (
            "lane activation must remain planning-only"
        )

for forbidden in ("subprocess", "requests", "httpx", "psycopg", "socket"):
    assert forbidden not in source, f"lane activation must not depend on {forbidden}"

for required in (
    '"laneActivationAllowed": self.lane_activation_allowed',
    '"globalActivationAllowed": self.global_activation_allowed',
    '"authorityEpochChanged": self.authority_epoch_changed',
    '"workerOrProviderStarted": self.worker_or_provider_started',
    '"objectReferencePromoted": self.object_reference_promoted',
    '"noGlobalActivationSwitch"',
):
    assert required in source, f"missing lane activation fence: {required}"

print("Owner Truth migration C08 lane-activation G0 gate passed")
PY
