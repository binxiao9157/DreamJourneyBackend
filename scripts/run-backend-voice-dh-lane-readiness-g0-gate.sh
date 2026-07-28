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

PYTHONPATH=. "$PYTHON_BIN" -m unittest tests.test_voice_dh_lane_readiness

"$PYTHON_BIN" - <<'PY'
import ast
from pathlib import Path

source_path = Path("app/services/voice_dh_lane_readiness.py")
source = source_path.read_text(encoding="utf-8")
tree = ast.parse(source)

for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        module = getattr(node, "module", "") or ""
        assert not module.startswith("app.main"), "lane readiness must not import API routes"
        assert not module.startswith("app.async_effects"), "lane readiness must not dispatch effects"
        assert not module.startswith("app.services.postgres_store"), "lane readiness must not persist state"
        assert not module.startswith("app.services.in_memory_store"), "lane readiness must not persist state"
        assert not module.startswith("app.services.tts"), "lane readiness must not call TTS providers"
        assert not module.startswith("app.services.voice_clone"), "lane readiness must not call voice providers"
    if isinstance(node, ast.Call):
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        assert name not in {"request", "post", "put", "delete", "persist", "enqueue"}, (
            "lane readiness must remain observation-only"
        )

for forbidden in ("requests", "httpx", "boto3", "urllib.request", "psycopg", "sqlalchemy"):
    assert forbidden not in source, forbidden

for required in (
    '"promotionAllowed": False',
    '"status": "blocked"',
    '"memorialPilotNotApproved"',
    '"manualPromotionRequired"',
    '"trueDeviceAcceptanceRequired"',
):
    assert required in source, required

print("Voice/Digital Human lane-readiness G0 gate passed")
PY
