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
        description="Compact the Postgres knowledge change feed conservatively."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Delete eligible changes and advance floors atomically. Default: dry-run.",
    )
    parser.add_argument(
        "--keep-recent-revisions",
        type=int,
        default=1000,
        help="Keep at least this many latest revisions per user. Default: 1000.",
    )
    parser.add_argument(
        "--keep-days",
        type=int,
        default=30,
        help="Keep changes created within this many days. Default: 30.",
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
        help="Bound each user transaction statement time. Default: 30000.",
    )
    args = parser.parse_args()

    backend_store = make_store(settings)
    if not isinstance(backend_store, PostgresStore):
        raise RuntimeError("knowledge change-feed compaction requires STORE_BACKEND=postgres")
    report = backend_store.maintain_kb_change_feed_compaction(
        keep_recent_revisions=args.keep_recent_revisions,
        keep_days=args.keep_days,
        apply=args.apply,
        lock_timeout_ms=args.lock_timeout_ms,
        statement_timeout_ms=args.statement_timeout_ms,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
