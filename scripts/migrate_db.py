#!/usr/bin/env python3
import argparse
import json
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.core.config import settings
from app.db.migrator import MigrationError, PostgresMigrator, default_migrations_dir


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run DreamJourney versioned Postgres migrations outside API startup."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Apply pending migrations.")
    mode.add_argument("--verify", action="store_true", help="Verify ledger head/checksums.")
    mode.add_argument("--dry-run", action="store_true", help="Inspect without writing. Default.")
    parser.add_argument(
        "--adopt-existing-baseline",
        action="store_true",
        help="Explicitly record a verified existing schema without replaying baseline DDL.",
    )
    parser.add_argument(
        "--build-id",
        default=os.environ.get("DEPLOY_BUILD_ID", "unknown"),
        help="Opaque deployment build identifier stored in the migration receipt.",
    )
    parser.add_argument(
        "--migrations-dir",
        type=Path,
        default=default_migrations_dir(),
        help="Versioned SQL/JSON migration directory.",
    )
    parser.add_argument("--lock-timeout-ms", type=int, default=5000)
    parser.add_argument("--statement-timeout-ms", type=int, default=30000)
    args = parser.parse_args()

    migrator = PostgresMigrator(
        dsn=settings.database_url,
        migrations_dir=args.migrations_dir,
        build_id=args.build_id,
        lock_timeout_ms=args.lock_timeout_ms,
        statement_timeout_ms=args.statement_timeout_ms,
    )
    try:
        if args.apply:
            result = migrator.apply(
                adopt_existing_baseline=args.adopt_existing_baseline,
            )
        elif args.verify:
            if args.adopt_existing_baseline:
                parser.error("--adopt-existing-baseline is only valid with --apply")
            result = migrator.verify()
        else:
            if args.adopt_existing_baseline:
                parser.error("--adopt-existing-baseline is only valid with --apply")
            result = migrator.plan()
    except Exception as exc:
        print(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "status": "failed",
                    "errorCode": type(exc).__name__,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
