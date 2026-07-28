#!/usr/bin/env bash
set -euo pipefail

# G0-only. This composes existing rights and recovery contracts. It must not
# run a deletion, purge, restore, replay, Provider exit, or production smoke.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
STORE_BACKEND=memory PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_owner_truth_migration_rights_restore_replay_g0 \
  tests.test_account_deletion_state \
  tests.test_data_rights_evidence_projection \
  tests.test_recovery_access \
  tests.test_recovery_record \
  tests.test_async_effect_recovery_evidence
PYTHONPATH=. "$PYTHON_BIN" -m py_compile \
  app/services/account_deletion_state.py \
  app/services/data_rights_evidence_projection.py \
  app/services/recovery_access.py \
  app/db/recovery.py \
  app/async_effects/recovery_evidence.py

"$PYTHON_BIN" - <<'PY'
from pathlib import Path

sources = {
    "account": Path("app/services/account_deletion_state.py").read_text(encoding="utf-8"),
    "rights": Path("app/services/data_rights_evidence_projection.py").read_text(encoding="utf-8"),
    "recovery": Path("app/db/recovery.py").read_text(encoding="utf-8"),
    "access": Path("app/services/recovery_access.py").read_text(encoding="utf-8"),
}

for required in (
    "accountLifecycleUpsertBlocked",
    "restoreLimitReached",
    "retentionHoldActive",
):
    assert required in sources["account"], f"missing lifecycle fence: {required}"
for required in (
    "terminalExecutionMissingReceipt",
    "missingAccessRevocationEvidence",
):
    assert required in sources["rights"], f"missing rights evidence fence: {required}"
for required in (
    'f"{kind}CoverageMissing"',
    'f"{kind}ReceiptNotApplied"',
    "purgedOwnerViolationCount",
):
    assert required in sources["recovery"], f"missing restore/replay fence: {required}"
assert "recoveryWriteBlocked" in sources["access"]

print("Owner Truth migration C09 rights/restore/replay G0 gate passed")
PY
