#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
  else
    PYTHON_BIN="python3"
  fi
fi

PYTHONPATH=. "$PYTHON_BIN" -m unittest tests.test_digital_human_session_lifecycle_shadow

"$PYTHON_BIN" - <<'PY'
import ast
from pathlib import Path

source_path = Path("app/services/digital_human_session_lifecycle_shadow.py")
source = source_path.read_text(encoding="utf-8")
tree = ast.parse(source)

for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        module = getattr(node, "module", "") or ""
        assert not module.startswith("app.main"), "session lifecycle shadow must not import API routes"
        assert not module.startswith("app.services.in_memory_store"), "session lifecycle shadow must not import legacy leases"
        assert not module.startswith("app.services.postgres_store"), "session lifecycle shadow must not import persistence"
        assert not module.startswith("app.async_effects"), "session lifecycle shadow must not dispatch effects"
    if isinstance(node, ast.Call):
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        assert name not in {"open_session", "heartbeat", "close_session", "persist", "enqueue", "request"}, (
            "session lifecycle shadow must remain side-effect free"
        )

assert "if enabled is not True:" in source
for required in (
    '"providerSessionKnown": self.provider_session_known',
    '"providerSessionOpened": self.provider_session_opened',
    '"providerSessionClosed": self.provider_session_closed',
    '"cleanupReceiptPersisted": self.cleanup_receipt_persisted',
    '"localLeaseProviderReady": self.local_lease_provider_ready',
    '"releaseVisible": False',
):
    assert required in source, required
print("Digital Human session lifecycle shadow G0 contract gate passed")
PY
