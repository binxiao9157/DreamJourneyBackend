#!/usr/bin/env python3
"""Run the synthetic 200-case embedding evaluation only with explicit approval.

This script never reads a user Vault.  It remains deliberately opt-in because
even synthetic evaluation consumes provider quota and the configured endpoint
must be separately privacy-reviewed before it is used for formal-memory data.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.core.config import Settings
from app.services.owner_truth_memory_search_embedding_runtime import (
    build_configured_embedding_provider,
    embedding_runtime_readiness,
)
from app.services.owner_truth_memory_search_quality_corpus import (
    evaluate_embedding_quality_corpus,
    load_owner_truth_memory_search_quality_corpus,
)


DEFAULT_CORPUS = (
    ROOT_DIR
    / "tests/fixtures/owner_truth/memory_search_quality_zh_v1.json"
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--result", type=Path)
    args = parser.parse_args()

    if os.environ.get("OWNER_TRUTH_MEMORY_SEARCH_QUALITY_EVALUATION_APPROVED") != "1":
        print(
            "BLOCKED: set OWNER_TRUTH_MEMORY_SEARCH_QUALITY_EVALUATION_APPROVED=1 "
            "after approving this synthetic provider-cost evaluation.",
            file=sys.stderr,
        )
        return 3
    settings = Settings.from_env()
    readiness = embedding_runtime_readiness(settings)
    provider = build_configured_embedding_provider(settings)
    if provider is None:
        print(
            json.dumps(
                {
                    "state": "BLOCKED",
                    "reason": readiness.reason,
                    "configurationStatus": readiness.configuration_status,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 3
    corpus = load_owner_truth_memory_search_quality_corpus(args.corpus)
    result = evaluate_embedding_quality_corpus(
        provider=provider,
        cases=corpus,
        batch_size=args.batch_size,
    )
    report = result.value_free_summary()
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    if args.result is not None:
        args.result.parent.mkdir(parents=True, exist_ok=True)
        args.result.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0 if result.acceptance_passed else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
