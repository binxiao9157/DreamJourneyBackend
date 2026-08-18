#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-${ROOT_DIR}/.venv/bin/python}"

cd "${ROOT_DIR}"

"${PYTHON_BIN}" -m unittest \
  tests.test_password_authentication_v2 \
  tests.test_password_authentication_v2_migration_contract \
  tests.test_identity_bindings \
  tests.test_auth_sessions \
  tests.test_runtime_capabilities \
  tests.test_route_authentication \
  tests.test_route_ownership_registry

"${PYTHON_BIN}" -m py_compile \
  app/services/password_authentication.py \
  app/services/identity_bindings.py \
  app/services/in_memory_store.py \
  app/services/postgres_store.py \
  app/services/runtime_config.py \
  app/services/route_ownership.py \
  app/main.py

echo "password authentication v2 gate passed"
