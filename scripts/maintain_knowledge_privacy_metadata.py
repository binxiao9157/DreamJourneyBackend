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
from app.services.store_factory import close_store, make_store, open_store


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Canonicalize persisted knowledge source titles without exposing content."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes in one transaction. The default is a read-only dry-run.",
    )
    args = parser.parse_args()

    backend_store = make_store(settings)
    if not isinstance(backend_store, PostgresStore):
        raise RuntimeError("knowledge privacy maintenance requires STORE_BACKEND=postgres")
    open_store(backend_store)
    try:
        report = backend_store.maintain_knowledge_privacy_metadata(apply=args.apply)
    finally:
        close_store(backend_store)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
