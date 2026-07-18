import unittest

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


if __name__ == "__main__":
    unittest.main()
