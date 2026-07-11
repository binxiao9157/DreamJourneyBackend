#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.core.config import settings
from app.services.postgres_store import PostgresStore
from app.services.store_factory import make_store


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compact retained Postgres knowledge operation receipts safely."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist compact receipt results. Default: dry-run.",
    )
    parser.add_argument(
        "--keep-days",
        type=int,
        default=30,
        help="Keep receipt results newer than this many days unchanged. Default: 30.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Read and update at most this many receipts per batch. Default: 100.",
    )
    parser.add_argument(
        "--lock-timeout-ms",
        type=int,
        default=5000,
        help="Skip a busy user after this lock wait. Default: 5000.",
    )
    parser.add_argument(
        "--statement-timeout-ms",
        type=int,
        default=30000,
        help="Bound each database statement. Default: 30000.",
    )
    args = parser.parse_args()

    backend_store = make_store(settings)
    if not isinstance(backend_store, PostgresStore):
        raise RuntimeError("knowledge receipt maintenance requires STORE_BACKEND=postgres")
    report = backend_store.maintain_knowledge_operation_receipts(
        keep_days=args.keep_days,
        batch_size=args.batch_size,
        apply=args.apply,
        lock_timeout_ms=args.lock_timeout_ms,
        statement_timeout_ms=args.statement_timeout_ms,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
