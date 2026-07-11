import unittest

from app.services.knowledge_privacy_maintenance import (
    canonical_receipt_payload_hash,
    canonicalize_persisted_receipt_result,
)
from app.services.knowledge_receipt_maintenance import (
    KnowledgeReceiptMaintenanceError,
    canonicalize_compact_knowledge_receipt_result,
    compact_persisted_knowledge_receipt_result,
)


class KnowledgeReceiptMaintenanceTests(unittest.TestCase):
    def legacy_result(self, *, mutation=None):
        return {
            "userId": "private-user",
            "operationId": "op-1",
            "revision": 3,
            "updatedAt": "2026-07-11T12:00:00Z",
            "mutationSchemaVersion": 2 if mutation is not None else 1,
            "graph": {
                "facts": [
                    {
                        "id": "fact-1",
                        "statement": "private receipt statement",
                    }
                ]
            },
            "mutation": mutation,
        }

    def governance_mutation(self):
        return {
            "upserts": {
                "people": [],
                "places": [],
                "events": [],
                "facts": [
                    {
                        "id": "fact-1",
                        "statement": "corrected private statement",
                        "governanceMetadata": {
                            "operationId": "op-1",
                            "action": "correct",
                            "decidedAt": "2026-07-11T12:00:00Z",
                            "target": {"entityType": "facts", "entityId": "fact-1"},
                        },
                    }
                ],
            },
            "tombstones": [],
        }

    def test_compacts_sync_and_mutation_without_private_content(self):
        for operation_kind in ("kb.sync", "kb.mutation"):
            with self.subTest(operation_kind=operation_kind):
                compact = compact_persisted_knowledge_receipt_result(
                    self.legacy_result(),
                    operation_id="op-1",
                    operation_kind=operation_kind,
                )
                self.assertEqual(compact["receiptEnvelopeVersion"], 1)
                self.assertEqual(compact["revision"], 3)
                self.assertNotIn("graph", compact)
                self.assertNotIn("mutation", compact)
                self.assertNotIn("operationId", compact)
                self.assertNotIn("private receipt statement", str(compact))

    def test_compacts_governance_and_archive_with_id_only_summary(self):
        mutation = self.governance_mutation()
        for operation_kind in ("kb.governance", "archive.delete"):
            with self.subTest(operation_kind=operation_kind):
                compact = compact_persisted_knowledge_receipt_result(
                    self.legacy_result(mutation=mutation),
                    operation_id="op-1",
                    operation_kind=operation_kind,
                )
                self.assertEqual(
                    compact["governanceSummary"]["target"],
                    {"entityType": "facts", "entityId": "fact-1"},
                )
                self.assertNotIn("statement", str(compact))

    def test_archive_without_cascade_does_not_require_summary(self):
        compact = compact_persisted_knowledge_receipt_result(
            self.legacy_result(),
            operation_id="op-1",
            operation_kind="archive.delete",
        )
        self.assertNotIn("governanceSummary", compact)

    def test_canonical_compact_envelope_is_idempotent_and_strips_old_identity(self):
        compact = {
            "receiptEnvelopeVersion": 1,
            "userId": "legacy-user",
            "operationId": "legacy-op",
            "operationKind": "kb.mutation",
            "operationSchemaVersion": 2,
            "revision": 4,
            "mutationSchemaVersion": 2,
            "governanceSummary": {
                "action": "correct",
                "target": {"entityType": "facts", "entityId": "fact-1"},
                "privateTitle": "private title",
            },
        }
        canonical = canonicalize_compact_knowledge_receipt_result(compact)
        self.assertEqual(
            canonical,
            {
                "receiptEnvelopeVersion": 1,
                "revision": 4,
                "mutationSchemaVersion": 2,
                "governanceSummary": {
                    "action": "correct",
                    "target": {"entityType": "facts", "entityId": "fact-1"},
                },
            },
        )
        self.assertNotIn("private title", str(canonical))
        self.assertEqual(
            canonicalize_compact_knowledge_receipt_result(canonical),
            canonical,
        )

    def test_invalid_receipts_fail_closed(self):
        cases = [
            ({}, "kb.sync"),
            ({"revision": -1}, "kb.sync"),
            (self.legacy_result(), "unsupported"),
            (
                self.legacy_result(mutation={"upserts": {}, "tombstones": []}),
                "kb.governance",
            ),
        ]
        for result, operation_kind in cases:
            with self.subTest(operation_kind=operation_kind):
                with self.assertRaises(KnowledgeReceiptMaintenanceError):
                    compact_persisted_knowledge_receipt_result(
                        result,
                        operation_id="op-1",
                        operation_kind=operation_kind,
                    )

    def test_privacy_maintenance_accepts_compact_v2_and_preserves_hash(self):
        compact = {
            "receiptEnvelopeVersion": 1,
            "revision": 7,
            "mutationSchemaVersion": 2,
        }
        canonical = canonicalize_persisted_receipt_result(
            compact,
            require_v2_mutation=True,
        )
        self.assertEqual(canonical, compact)
        self.assertEqual(
            canonical_receipt_payload_hash(
                operation_kind="kb.mutation",
                schema_version=2,
                canonical_result=canonical,
                current_payload_hash="existing-hash",
            ),
            "existing-hash",
        )


if __name__ == "__main__":
    unittest.main()
