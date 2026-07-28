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

PYTHONPATH=. "$PYTHON_BIN" -m unittest tests.test_voice_generated_audio_binding_shadow

"$PYTHON_BIN" - <<'PY'
import ast
from pathlib import Path

source_path = Path("app/services/voice_generated_audio_binding_shadow.py")
source = source_path.read_text(encoding="utf-8")
tree = ast.parse(source)

for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        module = getattr(node, "module", "") or ""
        assert not module.startswith("app.main"), "binding shadow must not import API routes"
        assert not module.startswith("app.services.tts"), "binding shadow must not import TTS providers"
        assert not module.startswith("app.async_effects"), "binding shadow must not import effect dispatch"
        assert not module.startswith("app.services.postgres_store"), "binding shadow must not import persistence"
    if isinstance(node, ast.Call):
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        assert name not in {"synthesize", "persist", "enqueue", "upload", "request", "put"}, (
            "binding shadow must remain side-effect free"
        )

assert "if enabled is not True:" in source
for required in (
    '"generatedAudioPersisted": False',
    '"audioBytesStored": False',
    '"providerEffectAllowed": False',
    '"cachePromotionAllowed": False',
    '"releaseVisible": False',
):
    assert required in source, required
print("Voice GeneratedAudio binding shadow G0 contract gate passed")
PY
