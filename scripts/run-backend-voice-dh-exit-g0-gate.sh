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

PYTHONPATH=. "$PYTHON_BIN" -m unittest tests.test_voice_dh_exit_shadow

"$PYTHON_BIN" - <<'PY'
import ast
from pathlib import Path

source_path = Path("app/services/voice_dh_exit_shadow.py")
source = source_path.read_text(encoding="utf-8")
tree = ast.parse(source)

for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        module = getattr(node, "module", "") or ""
        assert not module.startswith("app.main"), "exit shadow must not import API routes"
        assert not module.startswith("app.services.in_memory_store"), "exit shadow must not import legacy store"
        assert not module.startswith("app.services.postgres_store"), "exit shadow must not import persistence"
        assert not module.startswith("app.async_effects"), "exit shadow must not dispatch effects"
    if isinstance(node, ast.Call):
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        assert name not in {"delete", "revoke", "pause", "disable", "persist", "enqueue", "request"}, (
            "exit shadow must remain side-effect free"
        )

assert "if enabled is not True:" in source
for required in (
    '"accessRevocationRequired": self.access_revocation_required',
    '"newEffectsMustBeDenied": self.new_effects_must_be_denied',
    '"providerExitReceiptPersisted": self.provider_exit_receipt_persisted',
    '"providerExitState": self.provider_exit_state.value',
    '"releaseVisible": False',
):
    assert required in source, required
print("Voice/DH exit shadow G0 contract gate passed")
PY
