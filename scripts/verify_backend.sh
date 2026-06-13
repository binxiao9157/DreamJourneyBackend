#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

echo "== Backend unittest =="
PYTHONPATH=. python3 -m unittest discover tests

echo "== Backend py_compile =="
python3 -m compileall -q app tests

echo "== Backend deployment files =="
test -f Dockerfile
test -f docker-compose.yml
test -f .env.example
test -f requirements.txt
grep -q "psycopg" requirements.txt

echo "== Backend FastAPI smoke =="
if python3 - <<'PY' >/dev/null 2>&1
import fastapi
import httpx
PY
then
  STORE_BACKEND=memory PYTHONPATH=. python3 - <<'PY'
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

echo "== Backend diff --check =="
git diff --check
git diff --cached --check
