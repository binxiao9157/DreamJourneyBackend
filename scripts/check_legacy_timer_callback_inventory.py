#!/usr/bin/env python3
"""Validate the value-free backend half of the legacy timer/callback inventory."""

from __future__ import annotations

from pathlib import Path

from app.async_effects.legacy_timer_callback_inventory import load_and_validate_inventory


ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "docs/backend/legacy-timer-callback-inventory-v1.json"


def main() -> None:
    summary = load_and_validate_inventory(INVENTORY, ROOT)
    print(
        "Legacy timer/callback backend inventory check passed: "
        f"entries={summary['entryCount']} sources={summary['sourceCount']} "
        f"legacyDirect={summary['legacyDirectEffectCount']} "
        f"hostUnverified={summary['hostUnverifiedCount']} "
        f"externalBlocked={summary['externalBlockedCount']}"
    )


if __name__ == "__main__":
    main()
