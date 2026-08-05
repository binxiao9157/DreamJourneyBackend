#!/usr/bin/env bash
set -euo pipefail

# P2-S2a gate: ShareGrant issuance and Visitor session admission may exist only
# as default-off, no-store internal QA routes. This does not approve a public
# reader, deep link, Visitor answer surface, or release flag.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_publication_visitor_access \
  tests.test_publication_visitor_access_api
PYTHONPATH=. "$PYTHON_BIN" -m py_compile app/services/publication_visitor_access.py

"$PYTHON_BIN" - <<'PY'
import app.main as main_module

expected_paths = {
    "/v2/internal/publication-access/vaults/{vault_id}/grants",
    "/v2/internal/publication-access/vaults/{vault_id}/grants/{grant_id}/revoke",
    "/v2/internal/publication-access/grants/{grant_id}/sessions",
}
internal_paths = {
    str(getattr(route, "path", ""))
    for route in main_module.app.routes
    if str(getattr(route, "path", "")).startswith("/v2/internal/publication-access/")
}
assert internal_paths == expected_paths
for route in main_module.app.routes:
    path = str(getattr(route, "path", ""))
    if path in expected_paths:
        assert getattr(route, "include_in_schema", True) is False
    assert not path.startswith("/v2/publications/")
    assert not path.startswith("/v2/visitor/")
    assert not path.startswith("/v2/share/")

assert main_module.PUBLICATION_VISITOR_ACCESS_QA_ENABLED is False
print("publication visitor access default-off gate passed")
PY
