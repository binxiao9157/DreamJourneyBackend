from __future__ import annotations

from collections import deque
from hashlib import sha256
import unittest
from uuid import uuid4

from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectTarget
from app.async_effects.target_admission import (
    InMemoryOwnerTruthMemoryProjectionTargetAdmissionRepository,
    InMemoryOwnerTruthSourceTargetAdmissionRepository,
    PostgresOwnerTruthMemoryProjectionTargetAdmissionRepository,
    PostgresOwnerTruthSourceTargetAdmissionRepository,
)
from app.services.owner_truth_memory_projection_effects import (
    MEMORY_PROJECTION_REBUILD_EVENT_TYPE,
    MEMORY_PROJECTION_REBUILD_JOB_TYPE,
    MEMORY_PROJECTION_REBUILD_OPERATION_TYPE,
    MEMORY_PROJECTION_RIGHTS_REBUILD_EVENT_TYPE,
    MEMORY_PROJECTION_RIGHTS_REBUILD_OPERATION_TYPE,
)


def digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class _PostgresCursor:
    def __init__(self, rows):
        self._rows = deque(rows)
        self._current = None
        self.executions = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, params):
        self.executions.append((statement, params))
        self._current = self._rows.popleft()

    def fetchone(self):
        return self._current


class _PostgresConnection:
    def __init__(self, cursor):
        self.cursor_instance = cursor
        self.row_factory = None

    def cursor(self, *, row_factory=None):
        self.row_factory = row_factory
        return self.cursor_instance


class AsyncEffectTargetAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryOwnerTruthSourceTargetAdmissionRepository()
        self.owner_subject_id = "owner-target-admission"
        self.vault_id = "vault-target-admission"
        self.source_id = str(uuid4())
        self.repository.seed_vault(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            authority_epoch=7,
            status="active",
        )
        self.repository.seed_source(
            vault_id=self.vault_id,
            source_id=self.source_id,
            owner_subject_id=self.owner_subject_id,
            authority_epoch=7,
            source_version=3,
            state="active",
        )

    def intent(
        self,
        *,
        operation_type: str = "ownerTruth.source.created",
        authority_epoch: int = 7,
        resource_version: int = 3,
        resource_type: str = "source",
        purpose: str = "candidateExtraction",
    ) -> AsyncEffectIntent:
        return AsyncEffectIntent(
            operation_type=operation_type,
            target=AsyncEffectTarget(
                owner_subject_id=self.owner_subject_id,
                vault_id=self.vault_id,
                resource_type=resource_type,
                resource_id=self.source_id,
                resource_version=resource_version,
                purpose=purpose,
                authority_epoch=authority_epoch,
            ),
            payload_hash=digest("target-admission-metadata-only"),
        )

    def test_active_source_with_current_owner_epoch_and_version_is_admitted(self):
        result = self.repository.admit_owner_truth_source(self.intent())

        self.assertTrue(result.allowed)
        self.assertEqual(result.outcome, "admitted")
        self.assertEqual(result.reason_code, "targetAuthorized")
        self.assertEqual(result.authority_epoch, 7)
        self.assertEqual(result.resource_version, 3)

    def test_stale_vault_epoch_is_blocked_before_any_consumer_completion(self):
        self.repository.seed_vault(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            authority_epoch=8,
            status="active",
        )

        result = self.repository.admit_owner_truth_source(self.intent())

        self.assertFalse(result.allowed)
        self.assertEqual(result.outcome, "blocked")
        self.assertEqual(result.reason_code, "authorityEpochChanged")

    def test_changed_source_version_is_blocked(self):
        self.repository.seed_source(
            vault_id=self.vault_id,
            source_id=self.source_id,
            owner_subject_id=self.owner_subject_id,
            authority_epoch=7,
            source_version=4,
            state="active",
        )

        result = self.repository.admit_owner_truth_source(self.intent())

        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_code, "sourceVersionChanged")

    def test_inactive_source_is_blocked_without_exposing_source_content(self):
        self.repository.seed_source(
            vault_id=self.vault_id,
            source_id=self.source_id,
            owner_subject_id=self.owner_subject_id,
            authority_epoch=7,
            source_version=3,
            state="redacted",
        )

        result = self.repository.admit_owner_truth_source(self.intent())

        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_code, "sourceInactive")
        self.assertFalse(hasattr(result, "content"))
        self.assertFalse(hasattr(result, "metadata"))

    def test_default_off_source_is_blocked_before_candidate_extraction(self):
        self.repository.seed_source(
            vault_id=self.vault_id,
            source_id=self.source_id,
            owner_subject_id=self.owner_subject_id,
            authority_epoch=7,
            source_version=3,
            state="active",
            candidate_extraction_allowed=False,
        )

        result = self.repository.admit_owner_truth_source(self.intent())

        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_code, "sourceCandidateExtractionDisabled")
        self.assertFalse(hasattr(result, "metadata"))

    def test_postgres_default_off_source_is_blocked_before_candidate_extraction(self):
        cursor = _PostgresCursor(
            [
                {
                    "owner_subject_id": self.owner_subject_id,
                    "authority_epoch": 7,
                    "status": "active",
                },
                {
                    "owner_subject_id": self.owner_subject_id,
                    "authority_epoch": 7,
                    "source_version": 3,
                    "state": "active",
                    "metadata": {"candidateExtraction": "defaultOff"},
                },
            ]
        )
        repository = PostgresOwnerTruthSourceTargetAdmissionRepository(
            _PostgresConnection(cursor)
        )

        result = repository.admit_owner_truth_source(self.intent())

        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_code, "sourceCandidateExtractionDisabled")
        self.assertEqual(len(cursor.executions), 2)
        self.assertIn("metadata", cursor.executions[1][0])

    def test_wrong_operation_or_target_shape_is_fail_closed(self):
        operation_result = self.repository.admit_owner_truth_source(
            self.intent(operation_type="timeLetter.delivery")
        )
        target_result = self.repository.admit_owner_truth_source(
            self.intent(resource_type="syntheticEffect", purpose="consumerFoundation")
        )

        self.assertEqual(operation_result.reason_code, "unsupportedOperation")
        self.assertEqual(target_result.reason_code, "unsupportedTarget")


class MemoryProjectionTargetAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryOwnerTruthMemoryProjectionTargetAdmissionRepository()
        self.owner_subject_id = "owner-projection-admission"
        self.vault_id = "vault-projection-admission"
        self.memory_version_id = str(uuid4())
        self.source_id = str(uuid4())
        self.content_hash = digest("projection-admission-metadata-only")
        self.repository.seed_vault(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            authority_epoch=5,
            status="active",
        )
        self.repository.seed_memory_version(
            vault_id=self.vault_id,
            memory_version_id=self.memory_version_id,
            owner_subject_id=self.owner_subject_id,
            authority_epoch=5,
            state="active",
            source_version=2,
            version_number=3,
            is_current=True,
            content_hash=self.content_hash,
            source_owner_subject_id=self.owner_subject_id,
            source_authority_epoch=5,
            source_state="active",
            source_version_current=2,
        )

    def intent(
        self,
        *,
        authority_epoch: int = 5,
        resource_version: int = 3,
        payload_hash: str | None = None,
    ) -> AsyncEffectIntent:
        return AsyncEffectIntent(
            operation_type=MEMORY_PROJECTION_REBUILD_OPERATION_TYPE,
            target=AsyncEffectTarget(
                owner_subject_id=self.owner_subject_id,
                vault_id=self.vault_id,
                resource_type="memoryVersion",
                resource_id=self.memory_version_id,
                resource_version=resource_version,
                purpose="compatibilityProjection",
                authority_epoch=authority_epoch,
            ),
            payload_hash=payload_hash or self.content_hash,
            event_type=MEMORY_PROJECTION_REBUILD_EVENT_TYPE,
            job_type=MEMORY_PROJECTION_REBUILD_JOB_TYPE,
        )

    def test_current_memory_version_with_current_source_is_admitted(self):
        result = self.repository.admit_owner_truth_memory_projection(self.intent())

        self.assertTrue(result.allowed)
        self.assertEqual(result.reason_code, "targetAuthorized")
        self.assertEqual(result.resource_version, 3)

    def test_changed_memory_content_hash_is_blocked_without_returning_content(self):
        result = self.repository.admit_owner_truth_memory_projection(
            self.intent(payload_hash=digest("different-content"))
        )

        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_code, "memoryContentHashChanged")
        self.assertFalse(hasattr(result, "content"))

    def test_stale_or_noncurrent_memory_version_is_blocked(self):
        self.repository.seed_memory_version(
            vault_id=self.vault_id,
            memory_version_id=self.memory_version_id,
            owner_subject_id=self.owner_subject_id,
            authority_epoch=5,
            state="active",
            source_version=2,
            version_number=3,
            is_current=False,
            content_hash=self.content_hash,
            source_owner_subject_id=self.owner_subject_id,
            source_authority_epoch=5,
            source_state="active",
            source_version_current=2,
        )

        result = self.repository.admit_owner_truth_memory_projection(self.intent())

        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_code, "memoryVersionNotCurrent")

    def test_postgres_admission_locks_and_evaluates_the_current_target(self):
        cursor = _PostgresCursor(
            [
                {
                    "owner_subject_id": self.owner_subject_id,
                    "authority_epoch": 5,
                    "status": "active",
                },
                {
                    "memory_owner_subject_id": self.owner_subject_id,
                    "memory_authority_epoch": 5,
                    "memory_status": "active",
                    "version_source_id": self.source_id,
                    "version_source_version": 2,
                    "version_number": 3,
                    "is_current": True,
                    "content_hash": self.content_hash,
                },
                {
                    "owner_subject_id": self.owner_subject_id,
                    "authority_epoch": 5,
                    "state": "active",
                    "source_version": 2,
                },
            ]
        )
        repository = PostgresOwnerTruthMemoryProjectionTargetAdmissionRepository(
            _PostgresConnection(cursor)
        )

        result = repository.admit_owner_truth_memory_projection(self.intent())

        self.assertTrue(result.allowed)
        self.assertEqual(result.reason_code, "targetAuthorized")
        self.assertEqual(len(cursor.executions), 3)


class ProjectionRightsRebuildTargetAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.owner_subject_id = "owner-projection-rights-admission"
        self.vault_id = "vault-projection-rights-admission"
        self.authority_epoch = 5
        self.revision = 7
        self.event_hash = digest("projection-rights-admission-event")

    def intent(
        self,
        *,
        resource_version: int | None = None,
        payload_hash: str | None = None,
    ) -> AsyncEffectIntent:
        revision = self.revision if resource_version is None else resource_version
        return AsyncEffectIntent(
            operation_type=MEMORY_PROJECTION_RIGHTS_REBUILD_OPERATION_TYPE,
            target=AsyncEffectTarget(
                owner_subject_id=self.owner_subject_id,
                vault_id=self.vault_id,
                resource_type="projectionRightsRevision",
                resource_id=f"projectionRightsRevision:{revision}",
                resource_version=revision,
                purpose="compatibilityProjectionRights",
                authority_epoch=self.authority_epoch,
            ),
            payload_hash=payload_hash or self.event_hash,
            event_type=MEMORY_PROJECTION_RIGHTS_REBUILD_EVENT_TYPE,
            job_type=MEMORY_PROJECTION_REBUILD_JOB_TYPE,
        )

    def _repository(self, rights_row):
        cursor = _PostgresCursor(
            [
                {
                    "owner_subject_id": self.owner_subject_id,
                    "authority_epoch": self.authority_epoch,
                    "status": "active",
                },
                rights_row,
            ]
        )
        return PostgresOwnerTruthMemoryProjectionTargetAdmissionRepository(
            _PostgresConnection(cursor)
        ), cursor

    def _current_rights_row(self, **overrides):
        row = {
            "owner_subject_id": self.owner_subject_id,
            "authority_epoch": self.authority_epoch,
            "revision": self.revision,
            "rights_state": "active",
            "event_hash": self.event_hash,
        }
        row.update(overrides)
        return row

    def test_postgres_admits_only_the_current_active_rights_revision(self):
        repository, cursor = self._repository(self._current_rights_row())

        result = repository.admit_owner_truth_projection_rights_rebuild(self.intent())

        self.assertTrue(result.allowed)
        self.assertEqual(result.reason_code, "targetAuthorized")
        self.assertEqual(result.authority_epoch, self.authority_epoch)
        self.assertEqual(result.resource_version, self.revision)
        self.assertEqual(len(cursor.executions), 2)
        self.assertIn("projection_rights_events", cursor.executions[1][0])
        self.assertIn("FOR SHARE", cursor.executions[1][0])
        self.assertEqual(
            cursor.executions[1][1],
            (self.vault_id, self.authority_epoch),
        )

    def test_postgres_blocks_a_newer_rights_revision(self):
        repository, _cursor = self._repository(
            self._current_rights_row(revision=self.revision + 1)
        )

        result = repository.admit_owner_truth_projection_rights_rebuild(self.intent())

        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_code, "projectionRightsRevisionChanged")

    def test_postgres_blocks_a_revoked_rights_revision(self):
        repository, _cursor = self._repository(
            self._current_rights_row(rights_state="revoked")
        )

        result = repository.admit_owner_truth_projection_rights_rebuild(self.intent())

        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_code, "projectionRightsNotActive")

    def test_postgres_blocks_a_replayed_rights_event_hash(self):
        repository, _cursor = self._repository(
            self._current_rights_row(event_hash=digest("different-rights-event"))
        )

        result = repository.admit_owner_truth_projection_rights_rebuild(self.intent())

        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_code, "projectionRightsEventChanged")


if __name__ == "__main__":
    unittest.main()
