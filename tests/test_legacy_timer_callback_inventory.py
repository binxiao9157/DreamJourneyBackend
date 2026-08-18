from __future__ import annotations

import json
import unittest
from pathlib import Path

from app.async_effects.legacy_timer_callback_inventory import (
    LegacyTimerCallbackInventoryError,
    load_and_validate_inventory,
    validate_inventory,
)


ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "docs/backend/legacy-timer-callback-inventory-v1.json"


class LegacyTimerCallbackInventoryTests(unittest.TestCase):
    def test_repository_inventory_is_value_free_and_source_aligned(self) -> None:
        summary = load_and_validate_inventory(INVENTORY, ROOT)
        self.assertEqual(summary["entryCount"], 10)
        self.assertEqual(summary["legacyDirectEffectCount"], 0)
        self.assertGreaterEqual(summary["productClosedEffectCount"], 3)
        self.assertGreaterEqual(summary["hostUnverifiedCount"], 1)
        self.assertGreaterEqual(summary["externalBlockedCount"], 1)

    def test_product_closed_effect_cannot_self_authorize_reopen(self) -> None:
        payload = json.loads(INVENTORY.read_text(encoding="utf-8"))
        for entry in payload["entries"]:
            if entry["id"] == "time-letter-api-direct-dispatch":
                entry["cutoverState"] = "ACTIVE"
                break
        with self.assertRaisesRegex(LegacyTimerCallbackInventoryError, "PRODUCT_CLOSED"):
            validate_inventory(payload)

    def test_inventory_rejects_payload_fields(self) -> None:
        payload = json.loads(INVENTORY.read_text(encoding="utf-8"))
        payload["entries"][0]["payload"] = "must-not-be-recorded"
        with self.assertRaisesRegex(LegacyTimerCallbackInventoryError, "forbidden value field"):
            validate_inventory(payload)


if __name__ == "__main__":
    unittest.main()
