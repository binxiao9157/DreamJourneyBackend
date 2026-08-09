import unittest
from unittest.mock import patch

from app import main as main_module
from app.core.config import Settings
from app.services.runtime_config import RuntimeConfigService


class AsyncEffectsRuntimeConfigTests(unittest.TestCase):
    def test_effect_kernel_is_explicitly_disabled_by_default(self):
        payload = RuntimeConfigService(Settings()).public_config()

        self.assertFalse(payload["capabilities"]["asyncEffect"])
        self.assertEqual(
            payload["asyncEffect"],
            {
                "enabled": False,
                "workerEnabled": False,
                "serverCompletionAvailable": False,
                "reason": "asyncEffectV1Disabled",
                "defaultReleaseVisible": False,
                "contractVersion": 1,
            },
        )

    def test_a_feature_flag_without_a_worker_still_fails_closed(self):
        payload = RuntimeConfigService(
            Settings(async_effect_v1_enabled=True, async_effect_worker_enabled=True)
        ).public_config()

        self.assertTrue(payload["asyncEffect"]["enabled"])
        self.assertFalse(payload["asyncEffect"]["serverCompletionAvailable"])
        self.assertEqual(payload["asyncEffect"]["reason"], "asyncEffectSchemaNotReady")

    def test_schema_ready_worker_can_report_server_completion(self):
        payload = RuntimeConfigService(
            Settings(async_effect_v1_enabled=True, async_effect_worker_enabled=True),
            async_effect_schema_ready=True,
        ).public_config()

        self.assertEqual(
            payload["asyncEffect"],
            {
                "enabled": True,
                "workerEnabled": True,
                "serverCompletionAvailable": True,
                "reason": "asyncEffectRuntimeReady",
                "defaultReleaseVisible": False,
                "contractVersion": 1,
            },
        )

    def test_live_store_probe_is_the_only_schema_authority(self):
        class ReadyStore:
            def readiness_probe(self):
                return {
                    "databaseReason": "readWriteProbeSucceeded",
                    "schemaReason": "migrationHeadVerified",
                }

        class FailedStore:
            def readiness_probe(self):
                raise RuntimeError("database detail must remain private")

        with patch.object(main_module, "store", ReadyStore()):
            self.assertTrue(main_module._async_effect_schema_ready())
        with patch.object(main_module, "store", FailedStore()):
            self.assertFalse(main_module._async_effect_schema_ready())
        with patch.object(main_module, "store", object()):
            self.assertFalse(main_module._async_effect_schema_ready())


if __name__ == "__main__":
    unittest.main()
