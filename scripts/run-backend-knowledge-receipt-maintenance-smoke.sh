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

STORE_BACKEND=memory PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_knowledge_receipt_maintenance \
  tests.test_knowledge_receipt_postgres_maintenance \
  tests.test_knowledge_privacy_maintenance \
  tests.test_knowledge_change_feed_compaction \
  tests.test_core_services.StoreTests.test_memory_receipts_are_compact_and_rebuild_from_change_then_snapshot \
  tests.test_core_services.StoreTests.test_memory_reads_legacy_full_receipt_and_checks_fingerprint_first \
  tests.test_core_services.KnowledgeSyncAPITests.test_mutation_api_marks_snapshot_fallback_after_receipt_change_compaction \
  tests.test_knowledge_governance.KnowledgeGovernanceAPITests.test_governance_compact_receipt_keeps_summary_after_change_compaction \
  tests.test_knowledge_governance.KnowledgeGovernanceAPITests.test_archive_compact_receipt_keeps_cascade_summary_without_change

HELP_OUTPUT="$("$PYTHON_BIN" scripts/maintain_knowledge_operation_receipts.py --help)"
grep -q -- "--apply" <<<"$HELP_OUTPUT"
grep -q -- "--keep-days" <<<"$HELP_OUTPUT"
grep -q -- "--batch-size" <<<"$HELP_OUTPUT"
grep -q 'action="store_true"' scripts/maintain_knowledge_operation_receipts.py
grep -q 'apply=args.apply' scripts/maintain_knowledge_operation_receipts.py

echo "Backend knowledge receipt maintenance smoke passed"
