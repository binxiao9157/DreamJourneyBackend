from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from app.services.owner_truth_live_topics import PostgresLiveTopicRepository

class ThemeDisplayProjectionTests(unittest.TestCase):
    def test_source_time_is_read_only_and_does_not_replace_revision(self):
        original = {"topicId": "topic", "proposalHash": "signed", "members": {}}
        saved = deepcopy(original)
        stamp = datetime(2026, 10, 8, 7, 32, tzinfo=timezone.utc)
        class Cursor:
            statements = []
            def execute(self, sql, params): self.statements.append(sql)
            def fetchall(self): return [{"payload": original, "source_created_at": stamp}]
        cursor = Cursor()
        class Service(PostgresLiveTopicRepository):
            @contextmanager
            def _cursor(self): yield cursor
            def _authority(self, cur, context, lock=False): return 1
            def _member_details(self, cur, payload): payload["memberDetails"] = {}
        service = object.__new__(Service)
        result = service.list_pending(context=SimpleNamespace(vault_id="v", owner_subject_id="o"))
        self.assertEqual(result["themes"][0]["sourceCreatedAt"], "2026-10-08T07:32:00+00:00")
        self.assertEqual(result["themes"][0]["proposalHash"], "signed")
        self.assertEqual(original, saved)
        self.assertEqual(len(cursor.statements), 1)
        self.assertTrue(cursor.statements[0].lstrip().startswith("SELECT"))
        self.assertIn("s.created_at AS source_created_at", cursor.statements[0])
