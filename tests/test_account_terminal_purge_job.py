import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.services.in_memory_store import InMemoryStore
from scripts.purge_expired_account_deletions import run_account_terminal_purge


ROOT_DIR = Path(__file__).resolve().parents[1]


class AccountTerminalPurgeJobTests(unittest.TestCase):
    def test_job_uses_injected_server_clock_and_only_returns_aggregate_receipt(self):
        store = InMemoryStore()
        phone = "13800138114"
        user = store.upsert_user(phone, "定时清理测试")
        store.soft_delete_user(
            user["id"],
            phone=phone,
            requested_at_iso="2026-01-01T00:00:00+00:00",
            deletion_request_id="rr_terminal_purge_job",
        )

        receipt = run_account_terminal_purge(
            store,
            now=datetime(2026, 2, 1, tzinfo=timezone.utc),
        )

        self.assertEqual(receipt["status"], "completed")
        self.assertEqual(receipt["job"], "accountTerminalPurge")
        self.assertEqual(receipt["cutoffSource"], "serverClock")
        self.assertEqual(receipt["purgedCount"], 1)
        serialized = json.dumps(receipt, ensure_ascii=False, sort_keys=True)
        self.assertNotIn(phone, serialized)
        self.assertNotIn(user["id"], serialized)
        self.assertNotIn("items", receipt)

        repeated = run_account_terminal_purge(
            store,
            now=datetime(2026, 2, 1, 0, 5, tzinfo=timezone.utc),
        )
        self.assertEqual(repeated["purgedCount"], 0)

    def test_systemd_timer_is_persistent_and_job_has_an_explicit_execution_gate(self):
        service = (
            ROOT_DIR / "deploy/systemd/dreamjourney-account-terminal-purge.service"
        ).read_text(encoding="utf-8")
        timer = (
            ROOT_DIR / "deploy/systemd/dreamjourney-account-terminal-purge.timer"
        ).read_text(encoding="utf-8")
        script = (ROOT_DIR / "scripts/purge_expired_account_deletions.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("ACCOUNT_TERMINAL_PURGE_RUN=1", service)
        self.assertIn("purge_expired_account_deletions.py", service)
        self.assertIn("Persistent=true", timer)
        self.assertIn("RandomizedDelaySec=5m", timer)
        self.assertIn('os.environ.get("ACCOUNT_TERMINAL_PURGE_RUN") != "1"', script)
        self.assertNotIn("BACKEND_API_TOKEN", service)
        self.assertNotIn("Environment=", service)


if __name__ == "__main__":
    unittest.main()
