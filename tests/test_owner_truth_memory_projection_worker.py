from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
import unittest
from uuid import uuid4

from app.async_effects.consumer_repository import InMemoryAsyncEffectConsumerRepository
from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectTarget
from app.async_effects.lease_repository import InMemoryAsyncEffectLeaseRepository
from app.async_effects.owner_truth_memory_projection_worker import (
    OwnerTruthMemoryProjectionWorkerRuntime,
)
from app.async_effects.target_admission import (
    InMemoryOwnerTruthMemoryProjectionTargetAdmissionRepository,
)
from app.core.config import Settings
from app.domain.owner_truth.memory_projection import OwnerTruthMemoryProjectionResult
from app.domain.owner_truth.search_documents import (
    OwnerTruthSearchDocumentProjection,
    OwnerTruthSearchDocumentProjectionRebuildResult,
)
from app.services.owner_truth_memory_projection_effects import (
    MEMORY_PROJECTION_REBUILD_EVENT_TYPE,
    MEMORY_PROJECTION_REBUILD_JOB_TYPE,
    MEMORY_PROJECTION_REBUILD_OPERATION_TYPE,
)


def _digest(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class _ProjectionRepository:
    def __init__(self, *, fail: bool = False, outcome: str = "rebuilt") -> None:
        self.fail = fail
        self.outcome = outcome
        self.contexts = []
        self.last_checkpoint: str | None = None

    def rebuild(self, *, context):
        self.contexts.append(context)
        if self.fail:
            raise RuntimeError("projection fixture failure")
        checkpoint = _digest({"vault": context.vault_id, "outcome": self.outcome})
        self.last_checkpoint = checkpoint
        return OwnerTruthMemoryProjectionResult(
            outcome=self.outcome,
            snapshot={
                "checkpoint": checkpoint,
                "entryCount": 1,
            },
        )


class _SearchDocumentProjectionRepository:
    def __init__(
        self,
        *,
        source: _ProjectionRepository,
        fail: bool = False,
        outcome: str = "rebuilt",
        checkpoint_override: str | None = None,
    ) -> None:
        self.source = source
        self.fail = fail
        self.outcome = outcome
        self.checkpoint_override = checkpoint_override
        self.contexts = []

    def rebuild(self, *, context):
        self.contexts.append(context)
        if self.fail:
            raise RuntimeError("search projection fixture failure")
        if self.outcome == "sourceRebuilding":
            return type("SearchProjectionResult", (), {"outcome": self.outcome, "projection": None})()
        checkpoint = self.checkpoint_override or self.source.last_checkpoint
        if checkpoint is None:
            raise RuntimeError("source projection fixture did not rebuild first")
        projection = OwnerTruthSearchDocumentProjection(
            vault_id=context.vault_id,
            owner_subject_id=context.owner_subject_id,
            authority_epoch=6,
            checkpoint=checkpoint,
            documents=(),
        )
        return OwnerTruthSearchDocumentProjectionRebuildResult(
            outcome=self.outcome,
            projection=projection,
        )


class _RecordingMetricRecorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def record_attempt(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        return {"sinkOutcome": "notConfigured"}


class _FailingMetricRecorder:
    def record_attempt(self, **_kwargs: object) -> dict[str, object]:
        raise RuntimeError("metric sink unavailable")


class _Store:
    def __init__(self, *, projection: _ProjectionRepository | None = None) -> None:
        self.lease_repository = InMemoryAsyncEffectLeaseRepository()
        self.consumer_repository = InMemoryAsyncEffectConsumerRepository()
        self.admission_repository = InMemoryOwnerTruthMemoryProjectionTargetAdmissionRepository()
        self.projection_repository = projection or _ProjectionRepository()
        self.search_projection_repository = _SearchDocumentProjectionRepository(
            source=self.projection_repository
        )
        self.uow_calls = 0

    def readiness_probe(self):
        return {"status": "ready"}

    @contextmanager
    def request_unit_of_work(self, **_kwargs):
        self.uow_calls += 1
        yield self

    def async_effect_lease_repository(self):
        return self.lease_repository

    def async_effect_consumer_repository(self):
        return self.consumer_repository

    def owner_truth_memory_projection_target_admission_repository(self):
        return self.admission_repository

    def owner_truth_memory_projection_repository(self):
        return self.projection_repository

    def owner_truth_memory_search_document_projection_repository(self):
        return self.search_projection_repository


class OwnerTruthMemoryProjectionWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = _Store()
        self.owner_subject_id = "owner-projection-worker"
        self.vault_id = "vault-projection-worker"
        self.memory_version_id = str(uuid4())
        self.content_hash = _digest("projection-worker-metadata-only")
        self.intent = AsyncEffectIntent(
            operation_type=MEMORY_PROJECTION_REBUILD_OPERATION_TYPE,
            target=AsyncEffectTarget(
                owner_subject_id=self.owner_subject_id,
                vault_id=self.vault_id,
                resource_type="memoryVersion",
                resource_id=self.memory_version_id,
                resource_version=2,
                purpose="compatibilityProjection",
                authority_epoch=6,
            ),
            payload_hash=self.content_hash,
            event_type=MEMORY_PROJECTION_REBUILD_EVENT_TYPE,
            job_type=MEMORY_PROJECTION_REBUILD_JOB_TYPE,
        )
        self.store.lease_repository.seed(self.intent)
        self.store.admission_repository.seed_vault(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            authority_epoch=6,
            status="active",
        )
        self.store.admission_repository.seed_memory_version(
            vault_id=self.vault_id,
            memory_version_id=self.memory_version_id,
            owner_subject_id=self.owner_subject_id,
            authority_epoch=6,
            state="active",
            source_version=4,
            version_number=2,
            is_current=True,
            content_hash=self.content_hash,
            source_owner_subject_id=self.owner_subject_id,
            source_authority_epoch=6,
            source_state="active",
            source_version_current=4,
        )

    def worker(
        self,
        *,
        enabled: bool = True,
        search_projection_enabled: bool = False,
        operation_metric_recorder=None,
    ) -> OwnerTruthMemoryProjectionWorkerRuntime:
        return OwnerTruthMemoryProjectionWorkerRuntime(
            settings=Settings(
                async_effect_v1_enabled=True,
                async_effect_worker_enabled=True,
                owner_truth_memory_projection_worker_enabled=enabled,
                owner_truth_memory_search_projection_worker_enabled=search_projection_enabled,
            ),
            store=self.store,
            worker_id="projection-worker-test",
            retry_seconds=5,
            operation_metric_recorder=operation_metric_recorder,
        )

    def test_default_disabled_worker_does_not_claim_a_projection_rebuild(self):
        result = self.worker(enabled=False).run_once()

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "ownerTruthMemoryProjectionWorkerDisabled")
        lease = self.store.lease_repository.claim_next(
            worker_id="verification-worker",
            lease_seconds=10,
            supported_job_types=[MEMORY_PROJECTION_REBUILD_JOB_TYPE],
        )
        self.assertIsNotNone(lease)

    def test_current_memory_projection_is_rebuilt_and_terminalized_atomically(self):
        result = self.worker().run_once()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["reason"], "memoryProjectionRebuilt")
        self.assertEqual(result["jobState"], "succeeded")
        self.assertEqual(result["operationState"], "completed")
        self.assertEqual(result["outboxState"], "dispatched")
        self.assertEqual(result["consumerInboxState"], "completed")
        self.assertEqual(result["projectionEntryCount"], 1)
        self.assertNotIn("searchProjectionOutcome", result)
        self.assertEqual(len(self.store.projection_repository.contexts), 1)
        self.assertEqual(self.store.search_projection_repository.contexts, [])
        self.assertEqual(
            self.store.lease_repository.attempt_state(self.intent.job_id, 1),
            "succeeded",
        )
        self.assertNotIn("content", json.dumps(result, sort_keys=True).lower())

    def test_postgres_readiness_contract_allows_a_current_rebuild(self):
        self.store.readiness_probe = lambda: {
            "databaseReason": "readWriteProbeSucceeded",
            "schemaReason": "migrationHeadVerified",
        }

        result = self.worker().run_once()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["reason"], "memoryProjectionRebuilt")

    def test_enabled_search_projection_rebuilds_only_after_current_memory_projection(self):
        result = self.worker(search_projection_enabled=True).run_once()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["projectionOutcome"], "rebuilt")
        self.assertEqual(result["searchProjectionOutcome"], "rebuilt")
        self.assertEqual(result["searchProjectionDocumentCount"], 0)
        self.assertEqual(len(self.store.projection_repository.contexts), 1)
        self.assertEqual(len(self.store.search_projection_repository.contexts), 1)
        self.assertEqual(
            self.store.search_projection_repository.contexts[0],
            self.store.projection_repository.contexts[0],
        )

    def test_search_projection_failure_releases_the_current_job_for_retry(self):
        self.store.search_projection_repository.fail = True

        result = self.worker(search_projection_enabled=True).run_once()

        self.assertEqual(result["status"], "retryWait")
        self.assertEqual(result["reason"], "memoryProjectionRebuildRetryableFailure")
        self.assertEqual(len(self.store.projection_repository.contexts), 1)
        self.assertEqual(len(self.store.search_projection_repository.contexts), 1)
        self.assertEqual(
            self.store.lease_repository.attempt_state(self.intent.job_id, 1),
            "retryableFailed",
        )
        self.assertEqual(self.store.consumer_repository._inbox, {})

    def test_non_ready_search_projection_result_never_terminalizes_the_job(self):
        self.store.search_projection_repository.outcome = "sourceRebuilding"

        result = self.worker(search_projection_enabled=True).run_once()

        self.assertEqual(result["status"], "retryWait")
        self.assertEqual(result["reason"], "memoryProjectionRebuildRetryableFailure")
        self.assertEqual(self.store.consumer_repository._inbox, {})

    def test_stale_search_projection_checkpoint_never_terminalizes_the_job(self):
        self.store.search_projection_repository.checkpoint_override = _digest("stale-search-checkpoint")

        result = self.worker(search_projection_enabled=True).run_once()

        self.assertEqual(result["status"], "retryWait")
        self.assertEqual(result["reason"], "memoryProjectionRebuildRetryableFailure")
        self.assertEqual(self.store.consumer_repository._inbox, {})

    def test_search_worker_flag_requires_the_private_repository_contract(self):
        self.store.owner_truth_memory_search_document_projection_repository = None

        result = self.worker(search_projection_enabled=True).run_once()

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "ownerTruthMemorySearchProjectionWorkerStoreUnsupported")
        self.assertEqual(self.store.projection_repository.contexts, [])

    def test_stale_authority_blocks_without_rebuilding_a_projection(self):
        self.store.admission_repository.seed_vault(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            authority_epoch=7,
            status="active",
        )

        result = self.worker().run_once()

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "authorityEpochChanged")
        self.assertEqual(result["jobState"], "blocked")
        self.assertEqual(result["consumerInboxState"], "skipped")
        self.assertEqual(self.store.projection_repository.contexts, [])
        self.assertEqual(self.store.search_projection_repository.contexts, [])
        self.assertEqual(
            self.store.lease_repository.attempt_state(self.intent.job_id, 1),
            "terminalFailed",
        )

    def test_projection_error_releases_only_the_current_job_for_retry(self):
        self.store.projection_repository.fail = True

        result = self.worker().run_once()

        self.assertEqual(result["status"], "retryWait")
        self.assertEqual(result["reason"], "memoryProjectionRebuildRetryableFailure")
        self.assertEqual(
            self.store.lease_repository.attempt_state(self.intent.job_id, 1),
            "retryableFailed",
        )
        self.assertEqual(self.store.consumer_repository._inbox, {})

    def test_claimed_job_records_value_free_worker_attempt_metric(self):
        recorder = _RecordingMetricRecorder()

        result = self.worker(operation_metric_recorder=recorder).run_once()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(recorder.calls), 1)
        call = recorder.calls[0]
        self.assertEqual(call["component_kind"], "worker")
        self.assertEqual(call["component_id"], "ownerTruthMemoryProjectionWorker")
        self.assertEqual(call["operation"], "ownerTruthMemoryProjection")
        self.assertEqual(call["outcome"], "succeeded")
        self.assertEqual(call["feedback_state"], "notApplicable")
        self.assertEqual(call["request_key"], result["jobId"])
        self.assertEqual(call["operation_key"], result["operationId"])
        self.assertNotIn(self.content_hash, json.dumps(call, sort_keys=True))

    def test_metric_failure_does_not_change_projection_rebuild_result(self):
        result = self.worker(operation_metric_recorder=_FailingMetricRecorder()).run_once()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["projectionOutcome"], "rebuilt")


if __name__ == "__main__":
    unittest.main()
