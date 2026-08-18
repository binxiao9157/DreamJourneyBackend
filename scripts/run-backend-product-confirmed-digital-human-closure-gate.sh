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

PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_release_policy \
  tests.test_digital_human_sessions \
  tests.test_runtime_capabilities

"$PYTHON_BIN" - <<'PY'
from pathlib import Path

root = Path.cwd()
policy = (root / "app/services/release_policy.py").read_text(encoding="utf-8")
runtime = (root / "app/services/provider_runtime.py").read_text(encoding="utf-8")
main = (root / "app/main.py").read_text(encoding="utf-8")

required_policy_markers = (
    '_PRODUCT_CLOSED_FEATURES = {',
    '"digitalHumanLivePanel",',
    'reason = "productClosed"',
    'return "enforce"',
    'normalized_path.endswith("/release")',
)
for marker in required_policy_markers:
    assert marker in policy, marker

assert 'reason="productClosed"' in runtime
assert 'not RELEASE_POLICY_SERVICE.is_product_closed(feature)' in main
print("Product-confirmed digital-human closure source gate passed")
PY

echo "Backend product-confirmed digital-human closure gate passed"
