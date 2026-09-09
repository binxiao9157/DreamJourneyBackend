#!/usr/bin/env python3
"""Run the checked-in 200-case formal-memory comparison quality gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.services.owner_truth_memory_changeset_quality import (
    evaluate_owner_truth_memory_changeset_quality,
    load_owner_truth_memory_changeset_quality_corpus,
)


DEFAULT_CORPUS = (
    ROOT_DIR / "tests/fixtures/owner_truth/memory_changeset_quality_zh_v1.json"
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--result", type=Path)
    args = parser.parse_args()

    report = evaluate_owner_truth_memory_changeset_quality(
        load_owner_truth_memory_changeset_quality_corpus(args.corpus)
    )
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    if args.result is not None:
        args.result.parent.mkdir(parents=True, exist_ok=True)
        args.result.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0 if report["acceptance"]["passed"] else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
