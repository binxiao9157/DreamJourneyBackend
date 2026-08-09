#!/usr/bin/env python3
"""Claim and dispatch one bounded batch of durable APNs outbox jobs."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import sys
import time

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.main import _make_apns_delivery_service


def _run_once(service, *, worker_id: str, limit: int, lease_seconds: int) -> dict:
    jobs = service.dispatch_due(
        worker_id=worker_id,
        limit=limit,
        lease_seconds=lease_seconds,
    )
    return {
        "status": "completed",
        "workerId": worker_id,
        "jobCount": len(jobs),
        "jobs": [job.public_contract() for job in jobs],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true")
    args = parser.parse_args()
    service = _make_apns_delivery_service()
    if service is None:
        print(json.dumps({"status": "skipped", "reason": "apnsDisabled"}))
        return
    worker_id = os.environ.get("APNS_WORKER_ID") or f"{socket.gethostname()}-{os.getpid()}"
    limit = int(os.environ.get("APNS_WORKER_BATCH_SIZE", "25"))
    lease_seconds = int(os.environ.get("APNS_WORKER_LEASE_SECONDS", "60"))
    poll_seconds = max(1, int(os.environ.get("APNS_WORKER_POLL_SECONDS", "5")))
    try:
        while True:
            print(
                json.dumps(
                    _run_once(
                        service,
                        worker_id=worker_id,
                        limit=limit,
                        lease_seconds=lease_seconds,
                    ),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                flush=True,
            )
            if not args.loop:
                return
            time.sleep(poll_seconds)
    finally:
        service.close()


if __name__ == "__main__":
    main()
