import inspect
import json
import unittest

from app.services.data_rights_contract import DataRightsRequestAuthority
from app.services.in_memory_store import InMemoryStore
from app.services.postgres_store import PostgresStore


class DataRightsStoreContractTests(unittest.TestCase):
    def setUp(self):
        authority = DataRightsRequestAuthority()
        self.request = authority.create_request(
            command_id="delete-command-1",
            subject_id="subject-1",
            identity_proof={"kind": "reauthenticatedSession", "value": "private"},
            payload={
                "action": "account.delete",
                "scope": ["archive", "voice", "knowledge"],
                "privateBody": "must not be persisted in the public receipt",
            },
            now="2026-07-18T10:00:00Z",
        ).request
        self.store = InMemoryStore()

    def test_in_memory_store_persists_idempotent_execution_and_append_only_receipt(self):
        created = self.store.create_rights_request(self.request)
        duplicate = self.store.create_rights_request(self.request)
        self.assertEqual(created["outcome"], "created")
        self.assertEqual(duplicate["outcome"], "deduplicated")
        self.assertEqual(created["request"]["status"], "requested")

        pending = self.store.record_rights_execution(
            self.request.request_id,
            module_id="archive",
            resource_type="archiveItem",
            execution_id_hash="execution-archive-1",
            outcome="pending",
            updated_at="2026-07-18T10:01:00Z",
        )
        self.assertEqual(pending["request"]["status"], "pending")

        completed = self.store.record_rights_execution(
            self.request.request_id,
            module_id="archive",
            resource_type="archiveItem",
            execution_id_hash="execution-archive-2",
            outcome="completed",
            evidence_id_hash="evidence-archive-1",
            updated_at="2026-07-18T10:02:00Z",
        )
        self.assertEqual(completed["outcome"], "updated")
        self.assertEqual(completed["execution"]["attempt"], 2)
        self.assertEqual(completed["request"]["status"], "completed")

        receipt = self.store.append_resource_deletion_receipt(
            receipt_id="receipt-archive-1",
            request_id=self.request.request_id,
            execution_id_hash="execution-archive-2",
            module_id="archive",
            resource_scope_hash="scope-archive",
            outcome="completed",
            receipt_hash="receipt-hash-1",
            evidence_event_id_hash="evidence-archive-1",
            created_at="2026-07-18T10:03:00Z",
        )
        self.assertEqual(receipt["outcome"], "appended")
        self.assertEqual(
            self.store.append_resource_deletion_receipt(
                receipt_id="receipt-archive-1",
                request_id=self.request.request_id,
                execution_id_hash="execution-archive-2",
                module_id="archive",
                resource_scope_hash="scope-archive",
                outcome="completed",
                receipt_hash="receipt-hash-1",
                evidence_event_id_hash="evidence-archive-1",
                created_at="2026-07-18T10:03:00Z",
            )["outcome"],
            "deduplicated",
        )

        summary = self.store.summarize_rights_request(self.request.request_id)
        serialized = json.dumps(summary, ensure_ascii=False, sort_keys=True)
        self.assertEqual(len(summary["executions"]), 1)
        self.assertEqual(len(summary["receipts"]), 1)
        self.assertNotIn("private", serialized)

        with self.assertRaises(ValueError):
            self.store.append_resource_deletion_receipt(
                receipt_id="receipt-archive-1",
                request_id=self.request.request_id,
                execution_id_hash="execution-archive-2",
                module_id="archive",
                resource_scope_hash="scope-archive",
                outcome="failed",
                receipt_hash="different-hash",
            )

    def test_postgres_store_keeps_uow_lock_and_idempotency_boundaries(self):
        sources = {
            name: inspect.getsource(getattr(PostgresStore, name))
            for name in (
                "create_rights_request",
                "record_rights_execution",
                "append_resource_deletion_receipt",
            )
        }
        self.assertIn("request_unit_of_work", sources["create_rights_request"])
        self.assertIn("ON CONFLICT (subject_id, command_id)", sources["create_rights_request"])
        self.assertIn("FOR UPDATE", sources["create_rights_request"])
        self.assertIn("FOR UPDATE", sources["record_rights_execution"])
        self.assertIn("ON CONFLICT (id) DO NOTHING", sources["append_resource_deletion_receipt"])
        self.assertIn("append-only", sources["append_resource_deletion_receipt"])


if __name__ == "__main__":
    unittest.main()
