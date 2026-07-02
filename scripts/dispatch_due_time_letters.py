#!/usr/bin/env python3
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.core.config import settings
from app.services.store_factory import make_store
from app.services.time_letters import dispatch_due_time_letters_for_store


def _summary(result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": result.get("status"),
        "cutoff": result.get("cutoff"),
        "itemCount": result.get("itemCount", 0),
        "reminderCount": result.get("reminderCount", 0),
        "providerDeliveryAttempted": result.get("providerDeliveryAttempted", False),
        "itemIds": [str(item.get("id") or "") for item in result.get("items", []) if isinstance(item, dict)],
        "reminderIds": [
            str(item.get("id") or "")
            for item in result.get("reminders", [])
            if isinstance(item, dict)
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Dispatch due DreamJourney time letters.")
    parser.add_argument(
        "--now",
        default=datetime.now(timezone.utc).isoformat(),
        help="ISO-8601 cutoff time. Defaults to current UTC time.",
    )
    parser.add_argument("--limit", type=int, default=50, help="Maximum due time letters to dispatch.")
    parser.add_argument(
        "--full",
        action="store_true",
        help="Print full dispatched item/reminder payloads. Defaults to a redacted summary.",
    )
    args = parser.parse_args()
    limit = max(1, min(args.limit, 200))

    backend_store = make_store(settings)
    result = dispatch_due_time_letters_for_store(backend_store, now_iso=args.now, limit=limit)
    payload = result if args.full else _summary(result)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
