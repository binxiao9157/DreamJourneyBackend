"""G0 checks for the external cleanup adapter inventory."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import unittest

from app.services.external_cleanup_adapter_shadow import (
    ExternalCleanupAdapterDescriptor,
    ExternalCleanupAdapterMode,
    ExternalCleanupDisposition,
    ExternalCleanupLayer,
    current_external_cleanup_adapter_inventory,
    plan_external_cleanup_adapter_shadow,
)


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class ExternalCleanupAdapterShadowTests(unittest.TestCase):
    def test_disabled_path_does_not_inspect_inventory(self) -> None:
        result = plan_external_cleanup_adapter_shadow(object())

        self.assertEqual(result.disposition, ExternalCleanupDisposition.SHADOW_DISABLED)
        self.assertFalse(result.external_cleanup_performed)
        self.assertFalse(result.provider_call_performed)
        self.assertFalse(result.receipt_persisted)
        self.assertFalse(result.retention_changed)

    def test_current_inventory_discloses_all_layers_without_completion(self) -> None:
        result = plan_external_cleanup_adapter_shadow(
            current_external_cleanup_adapter_inventory(),
            enabled=True,
        )

        self.assertEqual(result.disposition, ExternalCleanupDisposition.EXTERNAL_GATES_REQUIRED)
        summary = result.value_free_summary()
        statuses = {item["layer"]: item["status"] for item in summary["surfaces"]}
        self.assertEqual(statuses["objectStorage"], "notApplicable")
        self.assertEqual(statuses["providerVoice"], "unsupported")
        self.assertEqual(statuses["providerDigitalHuman"], "unsupported")
        self.assertEqual(statuses["backupRetention"], "auditOnly")
        self.assertEqual(statuses["evidenceRetention"], "queryRequired")
        self.assertNotIn("completed", statuses.values())
        self.assertFalse(summary["externalCleanupPerformed"])
        self.assertFalse(summary["providerCallPerformed"])
        self.assertFalse(summary["receiptPersisted"])
        self.assertFalse(summary["retentionChanged"])

    def test_duplicate_or_incomplete_inventory_fails_closed(self) -> None:
        inventory = current_external_cleanup_adapter_inventory()
        result = plan_external_cleanup_adapter_shadow(
            inventory[:-1] + (inventory[0],),
            enabled=True,
        )

        self.assertEqual(result.disposition, ExternalCleanupDisposition.INVALID_INVENTORY)
        self.assertIn("completeUniqueAdapterInventoryRequired", result.reason_codes)

    def test_invalid_layer_mode_pair_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ExternalCleanupAdapterDescriptor(
                layer=ExternalCleanupLayer.BACKUP_RETENTION,
                mode=ExternalCleanupAdapterMode.NO_EXTERNAL_TARGET,
                policy_version="invalidBackupModeV1",
                configuration_hash=_digest("invalid-backup-mode"),
            )

    def test_future_query_only_mode_still_performs_no_query_or_cleanup(self) -> None:
        inventory = list(current_external_cleanup_adapter_inventory())
        inventory[1] = ExternalCleanupAdapterDescriptor(
            layer=ExternalCleanupLayer.PROVIDER_VOICE,
            mode=ExternalCleanupAdapterMode.QUERY_RECONCILE_ONLY,
            policy_version="futureVoiceQueryOnlyV1",
            configuration_hash=_digest("future-voice-query-only"),
        )
        result = plan_external_cleanup_adapter_shadow(inventory, enabled=True)
        voice = next(
            item
            for item in result.value_free_summary()["surfaces"]
            if item["layer"] == "providerVoice"
        )

        self.assertEqual(voice["status"], "queryRequired")
        self.assertFalse(result.provider_call_performed)
        self.assertFalse(result.external_cleanup_performed)

    def test_value_free_summary_does_not_leak_policy_or_configuration_values(self) -> None:
        inventory = (
            ExternalCleanupAdapterDescriptor(
                layer=ExternalCleanupLayer.OBJECT_STORAGE,
                mode=ExternalCleanupAdapterMode.NO_EXTERNAL_TARGET,
                policy_version="privatePolicyV1",
                configuration_hash=_digest("private-object-config"),
            ),
            *current_external_cleanup_adapter_inventory()[1:],
        )
        result = plan_external_cleanup_adapter_shadow(inventory, enabled=True)
        serialized = repr(result.value_free_summary())

        self.assertNotIn("privatePolicyV1", serialized)
        self.assertNotIn(_digest("private-object-config"), serialized)

    def test_module_has_no_api_network_persistence_or_effect_imports(self) -> None:
        source = (
            Path(__file__).parents[1] / "app/services/external_cleanup_adapter_shadow.py"
        ).read_text(encoding="utf-8")
        for forbidden in (
            "app.main",
            "app.services.in_memory_store",
            "app.services.postgres_store",
            "app.async_effects",
            "requests",
            "httpx",
            "boto3",
            "psycopg",
            "subprocess",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
