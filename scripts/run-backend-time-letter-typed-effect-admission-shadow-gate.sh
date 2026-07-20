#!/usr/bin/env bash
set -euo pipefail

# G0-only. This validates a default-off, value-free admission shadow. It does
# not start a worker, alter the legacy TimeLetter dispatcher, write an outbox,
# write a mailbox/receipt, or invoke a provider.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
STORE_BACKEND=memory PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_time_letter_delivery_effects \
  tests.test_time_letter_typed_effect_admission_shadow
PYTHONPATH=. "$PYTHON_BIN" -m py_compile \
  app/services/time_letter_delivery_effects.py
"$PYTHON_BIN" - <<'PY'
import ast
from pathlib import Path

source = Path("app/services/time_letter_delivery_effects.py").read_text(encoding="utf-8")
module = ast.parse(source)
shadow = next(
    node
    for node in module.body
    if isinstance(node, ast.FunctionDef)
    and node.name == "build_time_letter_delivery_admission_shadow"
)

call_names = set()
for node in ast.walk(shadow):
    if not isinstance(node, ast.Call):
        continue
    if isinstance(node.func, ast.Name):
        call_names.add(node.func.id)
    elif isinstance(node.func, ast.Attribute):
        call_names.add(node.func.attr)

forbidden_calls = {
    "accept",
    "add_mailbox_letter",
    "consume",
    "dispatch",
    "dispatch_due_time_letters_for_store",
    "update_time_letter_delivery_summary",
}
assert not (forbidden_calls & call_names), "admission shadow must remain side-effect free"
assert "time_letter_delivery_service" not in source, "shadow must not import the atomic delivery service"
assert "if not enabled:" in ast.get_source_segment(source, shadow)
assert "TIME_LETTER_DELIVERY_SCHEMA_VERSION" in ast.get_source_segment(source, shadow)
print("TimeLetter typed-effect admission shadow G0 contract gate passed")
PY
