#!/usr/bin/env python3
import argparse
import json
from datetime import datetime, timezone
from typing import Any, Dict


def product_closed_summary(now_iso: str) -> Dict[str, Any]:
    return {
        "status": "productClosed",
        "reason": "productClosed",
        "cutoff": now_iso,
        "itemCount": 0,
        "reminderCount": 0,
        "providerDeliveryAttempted": False,
        "itemIds": [],
        "reminderIds": [],
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
    _ = (max(1, min(args.limit, 200)), args.full)

    # Time letters are outside the confirmed product scope. Exit before
    # opening storage so an installed timer cannot mutate retained test rows
    # or enqueue APNs work while the feature is closed.
    print(
        json.dumps(
            product_closed_summary(args.now),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
