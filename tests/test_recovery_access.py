import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.main as main_module
from app.core.config import Settings
from app.main import app
from app.services.recovery_access import RecoveryAccessPolicy
from app.services.runtime_config import RuntimeConfigService


class RecoveryAccessPolicyTests(unittest.TestCase):
    def test_normal_read_only_and_maintenance_modes_fail_closed(self):
        normal = RecoveryAccessPolicy(mode="normal", authority_epoch="epoch-7")
        self.assertTrue(normal.evaluate(method="POST", path="/archive/items").allowed)

        read_only = RecoveryAccessPolicy(mode="readOnly", authority_epoch="epoch-8")
        self.assertTrue(read_only.evaluate(method="GET", path="/archive/items/u1").allowed)
        denied = read_only.evaluate(method="POST", path="/archive/items")
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.code, "recoveryWriteBlocked")

        maintenance = RecoveryAccessPolicy(mode="maintenance", authority_epoch="epoch-9")
        self.assertFalse(maintenance.evaluate(method="GET", path="/archive/items/u1").allowed)
        self.assertTrue(maintenance.evaluate(method="GET", path="/config/runtime").allowed)

        invalid = RecoveryAccessPolicy(mode="unexpected", authority_epoch="")
        self.assertEqual(invalid.public_descriptor()["mode"], "maintenance")
        self.assertFalse(invalid.evaluate(method="POST", path="/auth/login").allowed)

    def test_runtime_config_exposes_recovery_fence_without_secrets(self):
        config = RuntimeConfigService(
            Settings(recovery_access_mode="readOnly", authority_epoch="epoch-42")
        ).public_config()
        recovery = config["recovery"]
        self.assertEqual(recovery["schemaVersion"], 1)
        self.assertEqual(recovery["mode"], "readOnly")
        self.assertEqual(recovery["authorityEpoch"], "epoch-42")
        self.assertFalse(recovery["writesAllowed"])
        self.assertEqual(recovery["cacheWritePolicy"], "disabled")
        self.assertNotIn("token", str(recovery).lower())

    def test_http_middleware_blocks_writes_but_keeps_runtime_visible(self):
        policy = RecoveryAccessPolicy(mode="readOnly", authority_epoch="epoch-10")
        with patch.object(main_module, "RECOVERY_ACCESS_POLICY", policy):
            client = TestClient(app)
            blocked = client.post("/archive/items", json={"userId": "u1"})
            runtime = client.get("/config/runtime")
        self.assertEqual(blocked.status_code, 503)
        self.assertEqual(blocked.json()["code"], "recoveryWriteBlocked")
        self.assertEqual(blocked.headers["X-DreamJourney-Recovery-Mode"], "readOnly")
        self.assertEqual(runtime.status_code, 200)


if __name__ == "__main__":
    unittest.main()
