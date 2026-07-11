import json
import unittest
from copy import deepcopy

from psycopg.types.json import Jsonb

from app.services.knowledge_privacy_maintenance import (
    KnowledgePrivacyMetadataError,
    canonical_receipt_payload_hash,
    canonicalize_persisted_knowledge_graph,
    canonicalize_persisted_knowledge_mutation,
)
from app.services.knowledge_store import (
    KB_OPERATION_GOVERNANCE,
    KB_OPERATION_MUTATION,
    knowledge_operation_payload_fingerprint,
)
from app.services.postgres_store import PostgresStore


RAW_TITLE = "RAW_PRIVATE_SOURCE_TITLE_SENTINEL"


def unwrap_jsonb(value):
    return value.obj if isinstance(value, Jsonb) else value


def source_ref():
    return {
        "kind": "conversationTurn",
        "id": "turn-1",
        "title": RAW_TITLE,
        "locator": "turn:1",
    }


def knowledge_entity():
    return {
        "id": "fact-1",
        "statement": "正文保持不变",
        "relatedPersonIds": ["person-1"],
        "privacyMetadata": {
            "scope": "generationAllowed",
            "sourceRefs": [source_ref()],
            "extraPolicy": "preserve",
        },
        "extraEntityField": {"preserve": True},
    }


def knowledge_graph():
    return {
        "people": [],
        "places": [],
        "events": [],
        "facts": [knowledge_entity()],
        "extraGraphField": {"preserve": True},
    }


def knowledge_mutation():
    return {
        "upserts": {
            "people": [],
            "places": [],
            "events": [],
            "facts": [knowledge_entity()],
        },
        "tombstones": [],
    }


class MaintenanceCursor:
    def __init__(self, connection):
        self.connection = connection
        self.result = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        params = params or ()
        self.connection.executed.append((normalized, params))
        if normalized == "SET LOCAL lock_timeout = '5s'":
            self.result = None
            return
        if normalized.startswith("SELECT pg_advisory_xact_lock"):
            self.result = {"locked": True}
            return
        if normalized.startswith("LOCK TABLE kb_snapshots"):
            self.result = None
            return
        if normalized.startswith("SELECT user_id FROM ( SELECT user_id FROM kb_snapshots"):
            users = set(self.connection.snapshots)
            users.update(row["user_id"] for row in self.connection.changes)
            users.update(row["user_id"] for row in self.connection.receipts)
            self.result = [{"user_id": user_id} for user_id in sorted(users)]
            return
        if normalized == "SELECT user_id, graph FROM kb_snapshots ORDER BY user_id":
            self.result = [
                {"user_id": user_id, "graph": deepcopy(graph)}
                for user_id, graph in sorted(self.connection.snapshots.items())
            ]
            return
        if normalized.startswith(
            "SELECT user_id, revision, graph, mutation FROM kb_changes"
        ):
            self.result = deepcopy(
                sorted(
                    self.connection.changes,
                    key=lambda row: (row["user_id"], row["revision"]),
                )
            )
            return
        if normalized.startswith(
            "SELECT user_id, operation_id, operation_kind, schema_version, payload_hash, result FROM kb_operation_receipts"
        ):
            self.result = deepcopy(
                sorted(
                    self.connection.receipts,
                    key=lambda row: (row["user_id"], row["operation_id"]),
                )
            )
            return
        if normalized.startswith("UPDATE kb_snapshots SET graph"):
            graph, user_id = params
            self._before_update()
            self.connection.snapshots[user_id] = deepcopy(unwrap_jsonb(graph))
            return
        if normalized.startswith("UPDATE kb_changes SET graph"):
            graph, user_id, revision = params
            self._before_update()
            self._change(user_id, revision)["graph"] = deepcopy(unwrap_jsonb(graph))
            return
        if normalized.startswith("UPDATE kb_changes SET mutation"):
            mutation, user_id, revision = params
            self._before_update()
            self._change(user_id, revision)["mutation"] = deepcopy(
                unwrap_jsonb(mutation)
            )
            return
        if normalized.startswith("UPDATE kb_operation_receipts SET result"):
            result, user_id, operation_id = params
            self._before_update()
            self._receipt(user_id, operation_id)["result"] = deepcopy(
                unwrap_jsonb(result)
            )
            return
        if normalized.startswith("UPDATE kb_operation_receipts SET payload_hash"):
            payload_hash, user_id, operation_id = params
            self._before_update()
            self._receipt(user_id, operation_id)["payload_hash"] = payload_hash
            return
        raise AssertionError(f"unexpected maintenance SQL: {normalized}")

    def fetchall(self):
        return self.result or []

    def _before_update(self):
        self.connection.update_count += 1
        if (
            self.connection.fail_after_updates is not None
            and self.connection.update_count > self.connection.fail_after_updates
        ):
            raise RuntimeError("simulated maintenance update failure")

    def _change(self, user_id, revision):
        return next(
            row
            for row in self.connection.changes
            if row["user_id"] == user_id and row["revision"] == revision
        )

    def _receipt(self, user_id, operation_id):
        return next(
            row
            for row in self.connection.receipts
            if row["user_id"] == user_id and row["operation_id"] == operation_id
        )


class MaintenanceConnection:
    def __init__(self, *, snapshots, changes, receipts, fail_after_updates=None):
        self.snapshots = deepcopy(snapshots)
        self.changes = deepcopy(changes)
        self.receipts = deepcopy(receipts)
        self.fail_after_updates = fail_after_updates
        self.update_count = 0
        self.executed = []
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0
        self._capture_committed_state()

    def cursor(self, row_factory=None):
        return MaintenanceCursor(self)

    def commit(self):
        self.commits += 1
        self._capture_committed_state()

    def rollback(self):
        self.rollbacks += 1
        self.snapshots, self.changes, self.receipts = deepcopy(self._committed_state)

    def close(self):
        self.closes += 1

    def _capture_committed_state(self):
        self._committed_state = deepcopy(
            (self.snapshots, self.changes, self.receipts)
        )


class KnowledgePrivacyMaintenanceTests(unittest.TestCase):
    def test_pure_canonicalizers_preserve_identity_body_and_extra_fields(self):
        graph = knowledge_graph()
        mutation = knowledge_mutation()
        canonical_graph = canonicalize_persisted_knowledge_graph(graph)
        canonical_mutation = canonicalize_persisted_knowledge_mutation(mutation)

        self.assertEqual(graph["facts"][0]["statement"], "正文保持不变")
        self.assertEqual(
            graph["facts"][0]["privacyMetadata"]["sourceRefs"][0]["title"],
            RAW_TITLE,
        )
        canonical_entity = canonical_graph["facts"][0]
        self.assertEqual(canonical_entity["id"], "fact-1")
        self.assertEqual(canonical_entity["statement"], "正文保持不变")
        self.assertEqual(canonical_entity["relatedPersonIds"], ["person-1"])
        self.assertEqual(canonical_entity["extraEntityField"], {"preserve": True})
        self.assertEqual(canonical_graph["extraGraphField"], {"preserve": True})
        self.assertEqual(
            canonical_entity["privacyMetadata"]["sourceRefs"][0],
            {
                "kind": "conversationTurn",
                "id": "turn-1",
                "title": "对话来源",
                "locator": "turn:1",
            },
        )
        self.assertEqual(
            canonical_mutation["upserts"]["facts"][0]["extraEntityField"],
            {"preserve": True},
        )

    def test_invalid_source_refs_are_reported_instead_of_silently_removed(self):
        graph = knowledge_graph()
        graph["facts"][0]["privacyMetadata"]["sourceRefs"] = ["invalid"]
        mutation = knowledge_mutation()
        mutation["upserts"]["facts"][0]["privacyMetadata"]["sourceRefs"] = [
            "invalid"
        ]

        with self.assertRaises(KnowledgePrivacyMetadataError):
            canonicalize_persisted_knowledge_graph(graph)
        with self.assertRaises(KnowledgePrivacyMetadataError):
            canonicalize_persisted_knowledge_mutation(mutation)

    def test_postgres_maintenance_dry_run_apply_and_second_apply_are_safe(self):
        mutation = knowledge_mutation()
        mutation_receipt = {
            "user_id": "private-user-id",
            "operation_id": "mutation-op",
            "operation_kind": KB_OPERATION_MUTATION,
            "schema_version": 2,
            "payload_hash": "legacy-hash",
            "result": {
                "graph": knowledge_graph(),
                "mutationSchemaVersion": 2,
                "mutation": deepcopy(mutation),
            },
        }
        governance_receipt = {
            "user_id": "private-user-id",
            "operation_id": "governance-op",
            "operation_kind": KB_OPERATION_GOVERNANCE,
            "schema_version": 1,
            "payload_hash": "governance-hash-must-stay",
            "result": {"graph": knowledge_graph(), "mutation": None},
        }
        connection = MaintenanceConnection(
            snapshots={"private-user-id": knowledge_graph()},
            changes=[
                {
                    "user_id": "private-user-id",
                    "revision": 7,
                    "graph": knowledge_graph(),
                    "mutation": deepcopy(mutation),
                }
            ],
            receipts=[mutation_receipt, governance_receipt],
        )
        store = PostgresStore(connection_factory=lambda: connection)
        original_state = deepcopy(
            (connection.snapshots, connection.changes, connection.receipts)
        )

        dry_run = store.maintain_knowledge_privacy_metadata()
        self.assertEqual(
            (connection.snapshots, connection.changes, connection.receipts),
            original_state,
        )
        self.assertEqual(dry_run["mode"], "dryRun")
        self.assertEqual(dry_run["changed"]["snapshotGraphs"], 1)
        self.assertEqual(dry_run["changed"]["changeGraphs"], 1)
        self.assertEqual(dry_run["changed"]["changeMutations"], 1)
        self.assertEqual(dry_run["changed"]["receiptResults"], 2)
        self.assertEqual(dry_run["changed"]["receiptPayloadHashes"], 1)
        statements = [statement for statement, _ in connection.executed]
        self.assertEqual(statements[0], "SET LOCAL lock_timeout = '5s'")
        user_lock_index = next(
            index
            for index, statement in enumerate(statements)
            if statement.startswith("SELECT pg_advisory_xact_lock")
            and index > 0
        )
        table_lock_index = next(
            index
            for index, statement in enumerate(statements)
            if statement.startswith("LOCK TABLE kb_snapshots")
        )
        self.assertLess(user_lock_index, table_lock_index)
        report_text = json.dumps(dry_run, ensure_ascii=False)
        self.assertNotIn(RAW_TITLE, report_text)
        self.assertNotIn("private-user-id", report_text)

        applied = store.maintain_knowledge_privacy_metadata(apply=True)
        self.assertEqual(applied["mode"], "apply")
        serialized_state = json.dumps(
            (connection.snapshots, connection.changes, connection.receipts),
            ensure_ascii=False,
        )
        self.assertNotIn(RAW_TITLE, serialized_state)
        self.assertIn("正文保持不变", serialized_state)
        mutation_receipt_after = next(
            row
            for row in connection.receipts
            if row["operation_id"] == "mutation-op"
        )
        canonical_mutation = canonicalize_persisted_knowledge_mutation(mutation)
        self.assertEqual(
            mutation_receipt_after["payload_hash"],
            knowledge_operation_payload_fingerprint(
                KB_OPERATION_MUTATION,
                2,
                canonical_mutation,
            ),
        )
        governance_receipt_after = next(
            row
            for row in connection.receipts
            if row["operation_id"] == "governance-op"
        )
        self.assertEqual(
            governance_receipt_after["payload_hash"],
            "governance-hash-must-stay",
        )

        second_apply = store.maintain_knowledge_privacy_metadata(apply=True)
        self.assertTrue(all(value == 0 for value in second_apply["changed"].values()))

    def test_postgres_maintenance_rolls_back_all_updates_on_database_failure(self):
        mutation = knowledge_mutation()
        connection = MaintenanceConnection(
            snapshots={"u1": knowledge_graph()},
            changes=[
                {
                    "user_id": "u1",
                    "revision": 1,
                    "graph": knowledge_graph(),
                    "mutation": mutation,
                }
            ],
            receipts=[],
            fail_after_updates=1,
        )
        store = PostgresStore(connection_factory=lambda: connection)
        original_state = deepcopy((connection.snapshots, connection.changes))

        with self.assertRaisesRegex(RuntimeError, "simulated maintenance update failure"):
            store.maintain_knowledge_privacy_metadata(apply=True)

        self.assertEqual((connection.snapshots, connection.changes), original_state)
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)

    def test_apply_refuses_invalid_records_without_writing(self):
        invalid_graph = knowledge_graph()
        invalid_graph["facts"][0]["privacyMetadata"]["sourceRefs"] = ["invalid"]
        connection = MaintenanceConnection(
            snapshots={"u1": invalid_graph},
            changes=[],
            receipts=[],
        )
        store = PostgresStore(connection_factory=lambda: connection)

        dry_run = store.maintain_knowledge_privacy_metadata()
        self.assertEqual(dry_run["status"], "invalidRecords")
        self.assertEqual(dry_run["invalidRecordCount"], 1)
        self.assertEqual(connection.update_count, 0)

        with self.assertRaises(KnowledgePrivacyMetadataError):
            store.maintain_knowledge_privacy_metadata(apply=True)
        self.assertEqual(connection.update_count, 0)
        self.assertEqual(connection.rollbacks, 1)

    def test_non_mutation_receipt_hash_helper_does_not_guess(self):
        self.assertEqual(
            canonical_receipt_payload_hash(
                operation_kind=KB_OPERATION_GOVERNANCE,
                schema_version=2,
                canonical_result={"mutation": knowledge_mutation()},
                current_payload_hash="keep-me",
            ),
            "keep-me",
        )


if __name__ == "__main__":
    unittest.main()
