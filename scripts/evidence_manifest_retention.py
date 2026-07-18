#!/usr/bin/env python3
"""Expire non-held evidence rows after their explicit manifest TTL.

This job is intentionally metadata-only: it calls the existing append-only
evidence retention primitive and prints counts plus hashed event identifiers.
It never reads, emits, or uploads acceptance package bodies.
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


def run_retention(store: Any, *, now: datetime) -> dict[str, Any]:
    expire = getattr(store, "expire_evidence_events", None)
    if not callable(expire):
        raise RuntimeError("evidenceRetentionUnavailable")
    receipt = expire(now.astimezone(timezone.utc).isoformat())
    return {
        "schemaVersion": 1,
        "job": "evidenceManifestRetention",
        "status": "completed",
        "cutoff": str(receipt.get("cutoff") or ""),
        "expiredCount": int(receipt.get("expiredCount") or 0),
        "heldCount": int(receipt.get("heldCount") or 0),
        "expiredEventIdHashes": list(receipt.get("expiredEventIdHashes") or []),
    }


def main() -> None:
    if os.environ.get("EVIDENCE_MANIFEST_RETENTION_RUN") != "1":
        raise SystemExit("EVIDENCE_MANIFEST_RETENTION_RUN=1 is required")
    store = make_store(settings)
    init_store(store)
    try:
        receipt = run_retention(store, now=datetime.now(timezone.utc))
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    finally:
        close_store(store)


if __name__ == "__main__":
    main()
