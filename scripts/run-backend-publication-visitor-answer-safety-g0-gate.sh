#!/usr/bin/env bash
set -euo pipefail

# WI-S3-01-06 G0 only. It must not register a route, read private Owner Truth,
# call a provider, query a public store, persist a report, or close a session.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
PYTHONPATH=. "$PYTHON_BIN" -m unittest tests.test_publication_visitor_answer_safety
PYTHONPATH=. "$PYTHON_BIN" -m py_compile app/domain/publication/visitor_answer_safety.py

"$PYTHON_BIN" - <<'PY'
import ast
import json
from pathlib import Path

import app.main as main_module
from app.domain.publication.visitor_answer_safety import (
    PUBLICATION_VISITOR_ANSWER_SAFETY_G0_SCHEMA_VERSION,
    PublicationVisitorAnswerSafetyDisposition,
    evaluate_publication_visitor_answer_safety,
)

assert PUBLICATION_VISITOR_ANSWER_SAFETY_G0_SCHEMA_VERSION == "publication-visitor-answer-safety-g0-v1"
disabled = evaluate_publication_visitor_answer_safety(
    grant=object(), visitor=object(), session=object(), request=object()
)
assert disabled.disposition is PublicationVisitorAnswerSafetyDisposition.SHADOW_DISABLED
assert disabled.ai_disclosure_required is True
assert disabled.answer_allowed is False
assert disabled.public_query_allowed is False
assert disabled.provider_call_allowed is False
assert disabled.owner_memory_read_allowed is False
assert disabled.owner_persona_allowed is False
assert disabled.voice_or_digital_human_allowed is False
assert disabled.feedback_persisted is False
assert disabled.session_closed is False

for route in main_module.app.routes:
    path = str(getattr(route, "path", "")).lower()
    for forbidden in ("publication", "visitor", "guest", "public"):
        assert forbidden not in path, f"G0 must not register a Visitor/public route: {path}"

source_path = Path("app/domain/publication/visitor_answer_safety.py")
source = source_path.read_text(encoding="utf-8")
tree = ast.parse(source)
for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        module = getattr(node, "module", "") or ""
        assert not module.startswith("app.main"), "G0 must not import API routes"
        assert not module.startswith("app.services"), "G0 must not import service dependencies"
        assert not module.startswith("app.async_effects"), "G0 must not dispatch effects"
        assert not module.startswith("app.domain.owner_truth"), "G0 must not read private Owner Truth"
    if isinstance(node, ast.Call):
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        assert name not in {"insert", "update", "delete", "persist", "enqueue", "request", "post", "put"}, (
            "G0 must remain contract-only"
        )

sql = Path("db/migrations/0054_publication_visitor_answer_safety.sql").read_text(encoding="utf-8").lower()
for required in (
    "create table publication.visitor_answer_safety_receipts",
    "revoke all on table publication.visitor_answer_safety_receipts from public;",
    "continuous_use_started_at",
    "requested_exit_channel",
    "public_citation_set_hash",
):
    assert required in sql, f"missing visitor-answer safety schema boundary: {required}"
for forbidden in (
    "question_body",
    "answer_body",
    "raw_prompt",
    "raw_message",
    "source_payload",
    "object_url",
    "private_memory",
):
    assert forbidden not in sql, f"G0 Visitor safety schema must not retain: {forbidden}"

manifest = json.loads(
    Path("db/migrations/0054_publication_visitor_answer_safety.json").read_text(encoding="utf-8")
)
assert manifest["releaseFlags"] == {
    "visitorCrisisFallbackV1": False,
    "visitorDurationGuardV1": False,
    "visitorReportV1": False,
    "visitorTextQAV1": False,
}

print("Publication Visitor answer safety G0 contract gate passed")
PY
