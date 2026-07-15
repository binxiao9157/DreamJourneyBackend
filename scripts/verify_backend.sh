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

echo "== Backend unittest =="
STORE_BACKEND=memory PYTHONPATH=. "$PYTHON_BIN" -m unittest discover tests

echo "== Credential response boundary smoke =="
PYTHON_BIN="$PYTHON_BIN" scripts/run-credential-response-boundary-smoke.sh

echo "== Backend py_compile =="
"$PYTHON_BIN" -m compileall -q app tests
"$PYTHON_BIN" -m py_compile scripts/*.py

echo "== Voice clone 2.0 contract smoke =="
PYTHONPATH=. "$PYTHON_BIN" scripts/voice_clone_2_contract_smoke.py >/dev/null

echo "== Backend deployment files =="
test -f Dockerfile
test -f docker-compose.yml
test -f .env.example
test -f requirements.txt
grep -q "psycopg" requirements.txt

echo "== Backend FastAPI smoke =="
if "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1
import fastapi
import httpx
PY
then
  STORE_BACKEND=memory PYTHONPATH=. "$PYTHON_BIN" - <<'PY'
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
health = client.get("/health")
assert health.status_code == 200, health.text
assert health.json()["status"] == "ok"
config = client.get("/config/runtime")
assert config.status_code == 200, config.text
assert "capabilities" in config.json()
print("FastAPI smoke verification passed")
PY
else
  echo "FastAPI/httpx not installed locally; skipping runtime smoke. Docker image installs requirements.txt."
fi

echo "== Backend knowledge delta smoke =="
STORE_BACKEND=memory PYTHONPATH=. "$PYTHON_BIN" scripts/backend-knowledge-delta-smoke.py

echo "== Backend knowledge v2 smoke =="
STORE_BACKEND=memory PYTHONPATH=. "$PYTHON_BIN" scripts/backend-knowledge-v2-smoke.py

echo "== Backend knowledge evidence smoke =="
STORE_BACKEND=memory PYTHONPATH=. "$PYTHON_BIN" scripts/backend-knowledge-evidence-smoke.py

echo "== Backend knowledge receipt maintenance smoke =="
PYTHON_BIN="$PYTHON_BIN" scripts/run-backend-knowledge-receipt-maintenance-smoke.sh

echo "== Backend diff --check =="
git diff --check
git diff --cached --check
