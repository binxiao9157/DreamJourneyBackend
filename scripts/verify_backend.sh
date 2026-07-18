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
test -f scripts/migrate_db.py
test -f scripts/backend-readiness-postgres-smoke.py
test -f scripts/backend-readiness-deployed-smoke.py
test -f scripts/backend-operation-metrics-deployed-smoke.py
test -f scripts/run-backend-operation-metrics-deployed-smoke.sh
test -f scripts/db/backup_postgres.sh
test -f scripts/db/audit_backup_retention.sh
test -f scripts/db/verify_backup_manifest.py
test -f scripts/db/verify_latest_backup.py
test -f scripts/db/backup-deployed-smoke.py
test -f deploy/systemd/dreamjourney-db-backup.timer
test -f db/migrations/0001_existing_schema_baseline.sql
test -f db/migrations/0001_existing_schema_baseline.json
grep -q "COPY db ./db" Dockerfile
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
assert health.json()["deprecated"] is True
live = client.get("/live")
assert live.status_code == 200, live.text
assert live.json()["status"] == "alive"
ready = client.get("/ready")
assert ready.status_code == 200, ready.text
assert ready.json()["status"] == "ready"
config = client.get(
    "/config/runtime",
    headers={
        "X-DreamJourney-Runtime-Contract-Version": "2",
        "X-DreamJourney-Client-Build": "9001",
    },
)
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

echo "== PostgreSQL backup contract smoke =="
PYTHON_BIN="$PYTHON_BIN" scripts/db/run-backup-postgres-smoke.sh

echo "== Backend diff --check =="
git diff --check
git diff --cached --check
