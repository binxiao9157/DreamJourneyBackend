#!/usr/bin/env bash
set -euo pipefail

# C1 non-device gate: exercises the local/test synthetic lane, the configured
# server-side HTTP JSON adapter contract, and production fail-closed behavior.
# It never contacts an actual SMS provider or sends a real message.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"

PYTHONPATH=. "$PYTHON_BIN" -m unittest tests.test_identity_bindings

PYTHONPATH=. "$PYTHON_BIN" -m py_compile \
  app/core/config.py \
  app/services/identity_bindings.py \
  app/services/runtime_config.py \
  app/main.py \
  scripts/backend-identity-challenge-provider-smoke.py \
  scripts/backend-identity-challenge-provider-postgres-smoke.py

PYTHONPATH=. "$PYTHON_BIN" scripts/backend-identity-challenge-provider-smoke.py

PYTHONPATH=. "$PYTHON_BIN" - <<'PY'
from app.core.config import Settings
from app.services.identity_bindings import (
    HttpJsonIdentityChallengeAdapter,
    UnavailableIdentityChallengeAdapter,
    make_identity_challenge_adapter,
)

configured = Settings(
    environment="production",
    identity_binding_hmac_key="identity-challenge-gate-key-" + ("x" * 40),
    identity_challenge_adapter="httpJson",
    identity_challenge_http_json_url="https://sms.example.test/v1/challenges",
    identity_challenge_http_json_api_key="server-only-test-key",
)
assert isinstance(make_identity_challenge_adapter(configured), HttpJsonIdentityChallengeAdapter)

for broken in (
    Settings(environment="production", identity_challenge_adapter="httpJson"),
    Settings(
        environment="production",
        identity_challenge_adapter="httpJson",
        identity_challenge_http_json_url="http://insecure.example.test/challenges",
        identity_challenge_http_json_api_key="server-only-test-key",
    ),
    Settings(
        environment="production",
        identity_challenge_adapter="synthetic",
        identity_challenge_synthetic_code="246810",
    ),
):
    assert isinstance(make_identity_challenge_adapter(broken), UnavailableIdentityChallengeAdapter)

print("Identity challenge provider configuration gate passed")
PY

bash -n scripts/run-backend-identity-challenge-deployed-smoke.sh
bash -n scripts/run-backend-identity-challenge-provider-postgres-smoke.sh
