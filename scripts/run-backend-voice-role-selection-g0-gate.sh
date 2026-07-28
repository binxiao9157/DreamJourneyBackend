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

PYTHONPATH=. "$PYTHON_BIN" -m unittest tests.test_voice_role_selection_shadow

"$PYTHON_BIN" - <<'PY'
import ast
from pathlib import Path

source_path = Path("app/services/voice_role_selection_shadow.py")
source = source_path.read_text(encoding="utf-8")
tree = ast.parse(source)

for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        module = getattr(node, "module", "") or ""
        assert not module.startswith("app.main"), "role selection shadow must not import API routes"
        assert not module.startswith("app.services.tts"), "role selection shadow must not import TTS providers"
        assert not module.startswith("app.services.voice_clone"), "role selection shadow must not import legacy voice routes"
        assert not module.startswith("app.async_effects"), "role selection shadow must not dispatch effects"
        assert not module.startswith("app.services.postgres_store"), "role selection shadow must not import persistence"
    if isinstance(node, ast.Call):
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        assert name not in {"synthesize", "persist", "enqueue", "upload", "request", "put"}, (
            "role selection shadow must remain side-effect free"
        )

assert "if enabled is not True:" in source
for required in (
    '"providerEffectAllowed": False',
    '"providerEffectPerformed": False',
    '"releaseVisible": False',
    '"roleVoiceReceiptPersisted": False',
    '"clearPreviousProfile": self.clear_previous_profile',
):
    assert required in source, required
print("Voice role selection shadow G0 contract gate passed")
PY
