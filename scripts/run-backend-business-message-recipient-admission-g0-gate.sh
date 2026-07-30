#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "$ROOT_DIR"

"$PYTHON_BIN" -m unittest \
  tests.test_business_message_recipient_admission \
  tests.test_time_letter_recipient_admission_postgres_smoke_contract
"$PYTHON_BIN" -m py_compile \
  app/async_effects/business_message_recipient_admission.py \
  scripts/backend-time-letter-recipient-admission-postgres-smoke.py \
  app/async_effects/legacy_identity_inbox_bridge.py

if [[ "${RUN_TIME_LETTER_RECIPIENT_ADMISSION_POSTGRES_SMOKE:-0}" == "1" ]]; then
  PYTHONPATH=. "$PYTHON_BIN" scripts/backend-time-letter-recipient-admission-postgres-smoke.py
fi

"$PYTHON_BIN" - <<'PY'
from pathlib import Path

source = Path("app/async_effects/business_message_recipient_admission.py").read_text(encoding="utf-8")
assert "mailbox_letters" not in source
assert "add_mailbox_letter" not in source
assert "business_message_projections" not in source
assert "@app." not in source
assert "POST /" not in source
assert "get_family_relationship_by_member" not in source
assert "record_receipt=True" not in source
assert "enabled: bool = False" in source
print("Business message recipient admission G0 gate passed")
PY
