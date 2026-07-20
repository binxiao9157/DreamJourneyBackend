#!/usr/bin/env bash
set -euo pipefail

# G0-only: validates that business completion, in-app messages, hash-only
# subscription rotation/revoke, and delivery observations remain separate. It
# never sends a raw device token, invokes APNs, schedules a local notification,
# or requires Postgres.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
STORE_BACKEND=memory PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_business_message_notification_effects
PYTHONPATH=. "$PYTHON_BIN" -m py_compile \
  app/async_effects/message_notification_effects.py

echo "Business completion/message/notification G0 contract gate passed"
