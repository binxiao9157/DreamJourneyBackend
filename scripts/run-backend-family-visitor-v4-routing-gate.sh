#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

STORE_BACKEND=memory PYTHONPATH=. "$ROOT_DIR/.venv/bin/python" -m unittest \
  tests.test_family_visitor_v4_routing \
  tests.test_publication_visitor_access \
  tests.test_publication_visitor_access_api \
  tests.test_publication_visitor_reader \
  tests.test_core_services.ArchiveAPITests.test_context_build_filters_unopened_time_letter_for_family_recipient \
  tests.test_core_services.ArchiveAPITests.test_context_build_blocks_pending_family_viewer_and_summarizes_care_snapshot \
  tests.test_core_services.ArchiveAPITests.test_context_build_requires_resource_grant_for_open_time_letter_recipient \
  tests.test_core_services.ArchiveAPITests.test_context_build_does_not_substitute_owner_care_for_family_viewer \
  tests.test_core_services.ArchiveAPITests.test_context_build_reports_fallbacks_when_voice_and_digital_human_are_unavailable

printf '%s\n' 'Family/Visitor V4 routing gate passed.'
