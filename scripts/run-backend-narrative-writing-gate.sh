#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

export PYTHONPATH=.
export STORE_BACKEND=memory
export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-/tmp/dreamjourney-narrative-pycache}"

echo "== Narrative contracts, generation, versioning and migration =="
"$PYTHON_BIN" -m unittest \
  tests.test_narrative_contracts \
  tests.test_narrative_generation_unittest \
  tests.test_narrative_fact_guard \
  tests.test_narrative_golden_corpus \
  tests.test_narrative_deepseek_provider \
  tests.test_narrative_provider_contract \
  tests.test_narrative_postgres_repository_sql \
  tests.test_narrative_api_policy \
  tests.test_narrative_versioning \
  tests.test_narrative_migration_contract -v

echo "== Narrative import and route registration =="
"$PYTHON_BIN" -c 'import app.main; assert any("narrative-projects" in getattr(route, "path", "") for route in app.main.app.routes)'

echo "== Release policy, route ownership and worker registry =="
"$PYTHON_BIN" -m unittest \
  tests.test_release_policy \
  tests.test_route_ownership_registry \
  tests.test_worker_activation

echo "== Narrative source compilation =="
"$PYTHON_BIN" -m py_compile \
  app/api/narrative.py \
  app/async_effects/narrative_generation_worker.py \
  app/domain/narrative/contracts.py \
  app/domain/narrative/fact_guard.py \
  app/domain/narrative/state_machine.py \
  app/services/narrative_deepseek.py \
  app/services/narrative_generation.py \
  app/services/narrative_project.py \
  app/services/narrative_reader.py

echo "Narrative writing gate passed."
