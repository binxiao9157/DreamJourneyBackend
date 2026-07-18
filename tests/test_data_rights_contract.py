import json
import unittest

from app.services.data_rights_contract import (
    DataRightsCommandConflict,
    DataRightsExecutionConflict,
    DataRightsRequestAuthority,
    DataRightsValidationError,
)


class DataRightsContractTests(unittest.TestCase):
    def setUp(self):
        self.authority = DataRightsRequestAuthority()
        self.payload = {
            "action": "deleteAccount",
            "scope": ["archive", "voice", "knowledge"],
            "phone": "13800000000",
            "body": "private account deletion explanation",
        }

    def create(self, *, command_id="cmd-delete-1", subject_id="user-1"):
        return self.authority.create_request(
            command_id=command_id,
            payload=self.payload,
            subject_id=subject_id,
            identity_proof={
                "kind": "reauthenticatedSession",
                "token": "private-proof-token",
            },
            now="2026-07-18T10:00:00Z",
        )

    def test_identity_proof_is_required(self):
        with self.assertRaisesRegex(DataRightsValidationError, "identity proof"):
            self.authority.create_request(
                command_id="cmd-missing-proof",
                payload=self.payload,
                subject_id="user-1",
                identity_proof=None,
            )

    def test_same_command_and_payload_is_idempotent_but_payload_conflict_is_explicit(self):
        first = self.create()
        duplicate = self.authority.create_request(
            command_id="cmd-delete-1",
            payload={
                "body": "private account deletion explanation",
                "scope": ["archive", "voice", "knowledge"],
                "phone": "13800000000",
                "action": "deleteAccount",
            },
            subject_id="user-1",
            identity_proof="a different proof is allowed for a retry",
            now="2026-07-18T10:01:00Z",
        )

        self.assertEqual(first.outcome, "created")
        self.assertEqual(duplicate.outcome, "deduplicated")
        self.assertEqual(first.request.request_id, duplicate.request.request_id)
        self.assertEqual(first.request.created_at, duplicate.request.created_at)

        conflicting_payload = dict(self.payload)
        conflicting_payload["scope"] = ["archive"]
        with self.assertRaises(DataRightsCommandConflict):
            self.authority.create_request(
                command_id="cmd-delete-1",
                payload=conflicting_payload,
                subject_id="user-1",
                identity_proof="private-proof-token",
            )

    def test_command_id_is_scoped_to_subject(self):
        owner = self.create(subject_id="user-1")
        other_subject = self.create(subject_id="user-2")

        self.assertNotEqual(owner.request.request_id, other_subject.request.request_id)
        self.assertNotEqual(owner.request.subject_hash, other_subject.request.subject_hash)

    def test_execution_updates_by_module_and_aggregates_non_boolean_status(self):
        request = self.create().request
        pending = self.authority.record_execution(
            request_id=request.request_id,
            module="archive",
            execution_id="archive-attempt-1",
            outcome="pending",
            now="2026-07-18T10:01:00Z",
        )
        self.assertEqual(pending.request.status, "pending")

        completed = self.authority.record_execution(
            request_id=request.request_id,
            module="archive",
            execution_id="archive-attempt-2",
            outcome="completed",
            evidence_id="private-archive-receipt",
            now="2026-07-18T10:02:00Z",
        )
        self.assertEqual(completed.outcome, "updated")
        self.assertEqual(completed.request.status, "completed")

        partial = self.authority.record_execution(
            request_id=request.request_id,
            module="voice",
            execution_id="voice-attempt-1",
            outcome="failed",
            now="2026-07-18T10:03:00Z",
        )
        self.assertEqual(partial.request.status, "partial")

        duplicate = self.authority.record_execution(
            request_id=request.request_id,
            module="voice",
            execution_id="voice-attempt-1",
            outcome="failed",
            now="2026-07-18T10:04:00Z",
        )
        self.assertEqual(duplicate.outcome, "deduplicated")
        self.assertEqual(duplicate.request.status, "partial")

        with self.assertRaises(DataRightsExecutionConflict):
            self.authority.record_execution(
                request_id=request.request_id,
                module="voice",
                execution_id="voice-attempt-1",
                outcome="completed",
            )

    def test_each_supported_terminal_outcome_is_preserved(self):
        for outcome in ("completed", "partial", "unsupported", "failed"):
            with self.subTest(outcome=outcome):
                request = self.create(command_id=f"cmd-{outcome}").request
                result = self.authority.record_execution(
                    request_id=request.request_id,
                    module="archive",
                    execution_id=f"archive-{outcome}",
                    outcome=outcome,
                )
                self.assertEqual(result.request.status, outcome)

    def test_public_receipt_contains_safe_hashes_only(self):
        request = self.create().request
        self.authority.record_execution(
            request_id=request.request_id,
            module="archive",
            execution_id="archive-attempt-1",
            outcome="completed",
            evidence_id="provider-secret-and-private-receipt",
        )
        receipt = self.authority.public_receipt(request.request_id)
        serialized = json.dumps(receipt, ensure_ascii=False, sort_keys=True)

        self.assertEqual(receipt["schemaVersion"], 1)
        self.assertEqual(receipt["status"], "completed")
        self.assertTrue(receipt["identityProofPresent"])
        self.assertEqual(receipt["executions"][0]["module"], "archive")
        self.assertNotIn("13800000000", serialized)
        self.assertNotIn("private account deletion explanation", serialized)
        self.assertNotIn("private-proof-token", serialized)
        self.assertNotIn("provider-secret-and-private-receipt", serialized)
        self.assertNotIn("user-1", serialized)


if __name__ == "__main__":
    unittest.main()
