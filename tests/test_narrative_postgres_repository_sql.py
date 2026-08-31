from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import json
import unittest
from uuid import uuid4

from app.domain.narrative.contracts import (
    BookProjectState,
    BookProjectType,
    NarrativeMemoryRef,
    NarrativeNarratorType,
    NarrativeProjectRecord,
    NarrativeScope,
    NarrativeSnapshotRecord,
)
from app.services.narrative_project import PostgresNarrativeRepository


def _hash(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class _Cursor:
    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=()):
        params = tuple(params)
        self.assert_placeholder_count(sql, params)
        self.calls.append((sql, params))

    @staticmethod
    def assert_placeholder_count(sql, params):
        count = sql.count("%s")
        if count != len(params):
            raise AssertionError(f"SQL expected {count} parameters but received {len(params)}")

    def fetchone(self):
        return self.rows.pop(0)


class _Connection:
    def __init__(self, rows):
        self.cursor_value = _Cursor(rows)
        self.row_factory = None

    def cursor(self, *, row_factory=None):
        self.row_factory = row_factory
        return self.cursor_value


class NarrativePostgresRepositorySqlTests(unittest.TestCase):
    def setUp(self):
        self.instant = datetime(2026, 8, 31, tzinfo=timezone.utc)
        self.scope = NarrativeScope(
            vault_id="vault-1",
            owner_subject_id="owner-1",
            actor_subject_id="owner-1",
            subject_persona_id="owner-1",
            authority_epoch=2,
        )
        self.project = NarrativeProjectRecord(
            project_id=str(uuid4()),
            scope=self.scope,
            project_type=BookProjectType.SELF_AUTOBIOGRAPHY,
            narrator_type=NarrativeNarratorType.SELF_FIRST_PERSON,
            title="我的自传",
            state=BookProjectState.NOT_STARTED,
            project_version=0,
            privacy_state="private",
            created_at=self.instant.isoformat(),
            updated_at=self.instant.isoformat(),
            writing_context={},
        )

    def project_row(self, project=None):
        project = project or self.project
        return {
            "id": project.project_id,
            "vault_id": project.scope.vault_id,
            "owner_subject_id": project.scope.owner_subject_id,
            "subject_persona_id": project.scope.subject_persona_id,
            "authority_epoch": project.scope.authority_epoch,
            "project_type": project.project_type.value,
            "narrator_type": project.narrator_type.value,
            "title": project.title,
            "state": project.state.value,
            "optimistic_version": project.project_version,
            "privacy_state": project.privacy_state,
            "current_memory_snapshot_id": project.current_memory_snapshot_id,
            "current_golden_sample_id": project.current_golden_sample_id,
            "current_constitution_id": project.current_constitution_id,
            "current_outline_id": project.current_outline_id,
            "ignored_memory_fingerprint": project.ignored_memory_fingerprint,
            "writing_context": dict(project.writing_context),
            "paused_from_state": (
                project.paused_from_state.value if project.paused_from_state else None
            ),
            "created_at": self.instant,
            "updated_at": self.instant,
        }

    def test_project_insert_and_update_bind_all_versioned_context_fields(self):
        created_connection = _Connection([None, self.project_row()])
        created = PostgresNarrativeRepository(created_connection).create_or_get_project(self.project)
        self.assertIsNotNone(created_connection.row_factory)
        self.assertEqual(created.writing_context, {})

        paused = replace(
            created,
            state=BookProjectState.PAUSED,
            project_version=1,
            writing_context={"primaryReader": "family", "privacyScope": "private"},
            paused_from_state=BookProjectState.WRITING,
        )
        updated_connection = _Connection([self.project_row(paused)])
        updated = PostgresNarrativeRepository(updated_connection).save_project(
            paused,
            expected_version=0,
        )
        self.assertEqual(updated.paused_from_state, BookProjectState.WRITING)
        self.assertEqual(updated.writing_context["primaryReader"], "family")

    def test_snapshot_insert_binds_immutable_writing_context(self):
        content = {"event": "在北方求学"}
        memory = NarrativeMemoryRef(
            memory_id=str(uuid4()),
            memory_version_id=str(uuid4()),
            content_hash=_hash(content),
            content=content,
            memory_kind="experience",
            perspective_type="ownerRecalled",
            epistemic_status="ownerConfirmed",
            sensitivity="normal",
        )
        snapshot = NarrativeSnapshotRecord(
            snapshot_id=str(uuid4()),
            project_id=self.project.project_id,
            vault_id=self.scope.vault_id,
            authority_epoch=2,
            memory_refs=(memory,),
            source_fingerprint=_hash([memory.memory_version_id]),
            snapshot_hash=_hash({"memory": memory.memory_version_id, "reader": "family"}),
            created_by="owner-1",
            created_at=self.instant.isoformat(),
            writing_context={"primaryReader": "family", "privacyScope": "private"},
        )
        row = {
            "id": snapshot.snapshot_id,
            "project_id": snapshot.project_id,
            "vault_id": snapshot.vault_id,
            "authority_epoch": snapshot.authority_epoch,
            "memory_version_refs": [memory.public_contract(include_content=True)],
            "writing_context": dict(snapshot.writing_context),
            "source_fingerprint": snapshot.source_fingerprint,
            "snapshot_hash": snapshot.snapshot_hash,
            "created_by": snapshot.created_by,
            "created_at": self.instant,
        }
        connection = _Connection([row])
        stored = PostgresNarrativeRepository(connection).save_snapshot(snapshot)
        self.assertEqual(stored.writing_context["primaryReader"], "family")
        insert_sql = connection.cursor_value.calls[0][0]
        self.assertIn("DO NOTHING", insert_sql)
        self.assertNotIn("DO UPDATE", insert_sql)

    def test_snapshot_conflict_reuses_existing_immutable_row(self):
        content = {"event": "在北方求学"}
        memory = NarrativeMemoryRef(
            memory_id=str(uuid4()),
            memory_version_id=str(uuid4()),
            content_hash=_hash(content),
            content=content,
            memory_kind="experience",
            perspective_type="ownerRecalled",
            epistemic_status="ownerConfirmed",
            sensitivity="normal",
        )
        snapshot = NarrativeSnapshotRecord(
            snapshot_id=str(uuid4()),
            project_id=self.project.project_id,
            vault_id=self.scope.vault_id,
            authority_epoch=2,
            memory_refs=(memory,),
            source_fingerprint=_hash([memory.memory_version_id]),
            snapshot_hash=_hash({"memory": memory.memory_version_id}),
            created_by="owner-1",
            created_at=self.instant.isoformat(),
            writing_context={"privacyScope": "private"},
        )
        existing_row = {
            "id": str(uuid4()),
            "project_id": snapshot.project_id,
            "vault_id": snapshot.vault_id,
            "authority_epoch": snapshot.authority_epoch,
            "memory_version_refs": [memory.public_contract(include_content=True)],
            "writing_context": dict(snapshot.writing_context),
            "source_fingerprint": snapshot.source_fingerprint,
            "snapshot_hash": snapshot.snapshot_hash,
            "created_by": snapshot.created_by,
            "created_at": self.instant,
        }
        connection = _Connection([None, existing_row])

        stored = PostgresNarrativeRepository(connection).save_snapshot(snapshot)

        self.assertEqual(stored.snapshot_id, existing_row["id"])
        self.assertEqual(len(connection.cursor_value.calls), 2)
        select_sql, select_params = connection.cursor_value.calls[1]
        self.assertIn("WHERE project_id=%s AND snapshot_hash=%s", select_sql)
        self.assertEqual(select_params, (snapshot.project_id, snapshot.snapshot_hash))


if __name__ == "__main__":
    unittest.main()
