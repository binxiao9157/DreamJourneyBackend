#!/usr/bin/env python3
"""Permanently purge accounts whose restore window has expired.

The scheduled entry point deliberately accepts no client-provided cutoff. The
production clock is always generated inside the job and the printed receipt is
limited to aggregate metadata so account identifiers cannot enter journald.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.core.config import settings
from app.services.store_factory import close_store, init_store, make_store


def run_account_terminal_purge(store: Any, *, now: datetime) -> dict[str, Any]:
    purge = getattr(store, "purge_expired_deleted_users", None)
    if not callable(purge):
        raise RuntimeError("accountTerminalPurgeUnavailable")

    cutoff = now.astimezone(timezone.utc).isoformat()
    purged = purge(cutoff)
    return {
        "schemaVersion": 1,
        "job": "accountTerminalPurge",
        "status": "completed",
        "cutoff": cutoff,
        "cutoffSource": "serverClock",
        "purgedCount": len(purged),
    }


def main() -> None:
    if os.environ.get("ACCOUNT_TERMINAL_PURGE_RUN") != "1":
        raise SystemExit("ACCOUNT_TERMINAL_PURGE_RUN=1 is required")

    store = make_store(settings)
    init_store(store)
    try:
        receipt = run_account_terminal_purge(
            store,
            now=datetime.now(timezone.utc),
        )
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    finally:
        close_store(store)


if __name__ == "__main__":
    main()
