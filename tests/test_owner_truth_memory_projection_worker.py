from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
import json
from threading import Event, Thread
from time import sleep
import unittest
from uuid import uuid4

from app.async_effects.consumer_repository import InMemoryAsyncEffectConsumerRepository
from app.async_effects.business_message_projection_request_repository import (
    InMemoryBusinessMessageProjectionRequestRepository,
)
from app.async_effects.dead_letter_repository import InMemoryAsyncEffectDeadLetterRepository
from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectTarget
from app.async_effects.lease_repository import InMemoryAsyncEffectLeaseRepository
from app.async_effects.legacy_identity_inbox_bridge import (
    InMemoryLegacyInboxAccountResolver,
    LegacyAliasClaimState,
    LegacyInboxAccountBinding,
)
from app.async_effects.owner_truth_memory_projection_worker import (
    OwnerTruthMemoryProjectionWorkerRuntime,
)
from app.async_effects.target_admission import (
    InMemoryOwnerTruthMemoryProjectionTargetAdmissionRepository,
)
from app.async_effects.repository import InMemoryEffectKernelRepository
from app.core.config import Settings
from app.domain.owner_truth.memory_projection import OwnerTruthMemoryProjectionResult
from app.domain.owner_truth.projection_rights import (
    OwnerTruthProjectionRightsSnapshot,
    ProjectionRightsState,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.domain.owner_truth.search_documents import (
    OwnerTruthSearchDocumentProjection,
    OwnerTruthSearchDocumentProjectionRebuildResult,
)
from app.services.owner_truth_memory_projection_effects import (
    MEMORY_PROJECTION_REBUILD_EVENT_TYPE,
    MEMORY_PROJECTION_REBUILD_JOB_TYPE,
    MEMORY_PROJECTION_REBUILD_OPERATION_TYPE,
    build_memory_projection_rebuild_effect_intent_for_rights_revision,
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


class _BlockingProjectionRepository(_ProjectionRepository):
    """Test-only rebuild fixture that holds an external effect past its lease."""

    def __init__(self, *, started: Event, release: Event) -> None:
        super().__init__()
        self._started = started
        self._release = release

    def rebuild(self, *, context):
        self._started.set()
        if not self._release.wait(timeout=5.0):
            raise TimeoutError("test projection rebuild release was not signalled")
        return super().rebuild(context=context)


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
        self.dead_letter_repository = InMemoryAsyncEffectDeadLetterRepository()
        self.admission_repository = InMemoryOwnerTruthMemoryProjectionTargetAdmissionRepository()
        self.projection_repository = projection or _ProjectionRepository()
        self.search_projection_repository = _SearchDocumentProjectionRepository(
            source=self.projection_repository
        )
        self.message_effect_repository = InMemoryEffectKernelRepository()
        self.message_input_repository = InMemoryBusinessMessageProjectionRequestRepository()
        self.message_inbox_resolver: InMemoryLegacyInboxAccountResolver | None = None
        self.business_message_projection_enabled = False
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

    def async_effect_dead_letter_repository(self):
        return self.dead_letter_repository

    def owner_truth_memory_projection_target_admission_repository(self):
        return self.admission_repository

    def owner_truth_memory_projection_repository(self):
        return self.projection_repository

    def owner_truth_memory_search_document_projection_repository(self):
        return self.search_projection_repository

    def effect_kernel_repository(self):
        return self.message_effect_repository

    def async_effect_business_message_projection_request_repository(self):
        return self.message_input_repository

    def async_effect_legacy_inbox_account_resolver(self):
        if self.message_inbox_resolver is None:
            raise RuntimeError("message inbox fixture is not configured")
        return self.message_inbox_resolver


class OwnerTruthMemoryProjectionWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
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
        self.store = self._store_for_intent(self.intent)

    def _store_for_intent(
        self,
        intent: AsyncEffectIntent,
        *,
        projection: _ProjectionRepository | None = None,
    ) -> _Store:
        store = _Store(projection=projection)
        store.lease_repository.seed(intent)
        store.admission_repository.seed_vault(
            vault_id=intent.target.vault_id,
            owner_subject_id=intent.target.owner_subject_id,
            authority_epoch=int(intent.target.authority_epoch),
            status="active",
        )
        store.admission_repository.seed_memory_version(
            vault_id=intent.target.vault_id,
            memory_version_id=intent.target.resource_id,
            owner_subject_id=intent.target.owner_subject_id,
            authority_epoch=int(intent.target.authority_epoch),
            state="active",
            source_version=4,
            version_number=int(intent.target.resource_version),
            is_current=True,
            content_hash=self.content_hash,
            source_owner_subject_id=intent.target.owner_subject_id,
            source_authority_epoch=int(intent.target.authority_epoch),
            source_state="active",
            source_version_current=4,
        )
        store.message_inbox_resolver = InMemoryLegacyInboxAccountResolver(
            [
                LegacyInboxAccountBinding(
                    legacy_user_id="legacy-projection-worker",
                    legacy_alias_hash=_digest("legacy-projection-worker"),
                    subject_id=intent.target.owner_subject_id,
                    vault_id=intent.target.vault_id,
                    claim_state=LegacyAliasClaimState.VERIFIED,
                    identity_proof_subject_id=intent.target.owner_subject_id,
                    subject_state="active",
                    vault_owner_subject_id=intent.target.owner_subject_id,
                    vault_state="active",
                    account_access_state="active",
                    account_deletion_state="active",
                    account_auth_epoch=int(intent.target.authority_epoch),
                    bridge_row_version=1,
                )
            ]
        )
        return store

    def _reset_for_intent(
        self,
        intent: AsyncEffectIntent,
        *,
        projection: _ProjectionRepository | None = None,
    ) -> None:
        self.intent = intent
        self.store = self._store_for_intent(intent, projection=projection)

    def worker(
        self,
        *,
        enabled: bool = True,
        search_projection_enabled: bool = False,
        store: _Store | None = None,
        worker_id: str = "projection-worker-test",
        lease_seconds: int = 60,
        retry_seconds: int = 5,
        heartbeat_interval_seconds: float | None = None,
        operation_metric_recorder=None,
    ) -> OwnerTruthMemoryProjectionWorkerRuntime:
        return OwnerTruthMemoryProjectionWorkerRuntime(
            settings=Settings(
                async_effect_v1_enabled=True,
                async_effect_worker_enabled=True,
                owner_truth_memory_projection_worker_enabled=enabled,
                owner_truth_memory_search_projection_worker_enabled=search_projection_enabled,
            ),
            store=store or self.store,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            retry_seconds=retry_seconds,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
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
        self.store.business_message_projection_enabled = True
        result = self.worker().run_once()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["reason"], "memoryProjectionRebuilt")
        self.assertEqual(result["jobState"], "succeeded")
        self.assertEqual(result["operationState"], "completed")
        self.assertEqual(result["outboxState"], "dispatched")
        self.assertEqual(result["consumerInboxState"], "completed")
        self.assertEqual(result["projectionEntryCount"], 1)
        self.assertEqual(result["messageProjectionKind"], "projectionStatus")
        self.assertEqual(result["messageProjectionOutcome"], "accepted")
        self.assertEqual(result["messageProjectionInputOutcome"], "recorded")
        self.assertEqual(self.store.message_effect_repository.record_count(), 1)
        self.assertEqual(self.store.message_input_repository.request_count(), 1)
        self.assertNotIn("searchProjectionOutcome", result)
        self.assertEqual(len(self.store.projection_repository.contexts), 1)
        self.assertEqual(self.store.search_projection_repository.contexts, [])
        self.assertEqual(
            self.store.lease_repository.attempt_state(self.intent.job_id, 1),
            "succeeded",
        )
        self.assertNotIn("content", json.dumps(result, sort_keys=True).lower())

    def test_slow_projection_rebuild_heartbeats_lease_and_blocks_second_worker(self):
        started = Event()
        release = Event()
        self.store.projection_repository = _BlockingProjectionRepository(
            started=started,
            release=release,
        )
        first_worker = self.worker(
            worker_id="projection-worker-first",
            lease_seconds=1,
            heartbeat_interval_seconds=0.02,
        )
        first_result: dict[str, object] = {}
        first_thread = Thread(
            target=lambda: first_result.update(first_worker.run_once()),
            name="projection-first-worker-test",
        )
        first_thread.start()
        self.assertTrue(started.wait(timeout=1.0))

        # The rebuild outlives the initial lease. Its independent heartbeat
        # keeps the same effect owned, so a second worker cannot claim it.
        sleep(1.1)
        contender = self.worker(
            worker_id="projection-worker-contender",
            lease_seconds=1,
        ).run_once()
        self.assertEqual(contender["status"], "idle")

        release.set()
        first_thread.join(timeout=3.0)
        self.assertFalse(first_thread.is_alive())
        self.assertEqual(first_result["status"], "completed")
        self.assertEqual(
            self.store.lease_repository.attempt_state(self.intent.job_id, 1),
            "succeeded",
        )

    def test_lease_heartbeat_failure_fails_closed_without_success_receipt(self):
        started = Event()
        release = Event()
        heartbeat_attempted = Event()
        self.store.projection_repository = _BlockingProjectionRepository(
            started=started,
            release=release,
        )
        original_heartbeat = self.store.lease_repository.heartbeat

        def fail_heartbeat(*_args, **_kwargs) -> None:
            heartbeat_attempted.set()
            raise RuntimeError("test projection heartbeat transaction failed")

        self.store.lease_repository.heartbeat = fail_heartbeat
        worker = self.worker(
            lease_seconds=1,
            heartbeat_interval_seconds=0.01,
        )
        result: dict[str, object] = {}
        thread = Thread(
            target=lambda: result.update(worker.run_once()),
            name="projection-heartbeat-failure-test",
        )
        try:
            thread.start()
            self.assertTrue(started.wait(timeout=1.0))
            self.assertTrue(heartbeat_attempted.wait(timeout=1.0))
            release.set()
            thread.join(timeout=3.0)
        finally:
            self.store.lease_repository.heartbeat = original_heartbeat
            release.set()
            thread.join(timeout=3.0)

        self.assertFalse(thread.is_alive())
        self.assertEqual(result["status"], "lost")
        self.assertEqual(result["reason"], "memoryProjectionLeaseLost")
        self.assertNotEqual(
            self.store.lease_repository.attempt_state(self.intent.job_id, 1),
            "succeeded",
        )
        # In-memory fixtures do not model transaction rollback for the rebuilt
        # projection itself. The terminal lease and success receipt are still
        # forbidden once lease ownership is uncertain.
        self.assertEqual(self.store.consumer_repository._inbox, {})

    def test_lease_heartbeat_uses_bounded_third_by_default_and_allows_test_injection(self):
        self.assertAlmostEqual(self.worker(lease_seconds=3)._heartbeat_interval_seconds, 1.0)
        self.assertAlmostEqual(self.worker(lease_seconds=180)._heartbeat_interval_seconds, 30.0)
        self.assertAlmostEqual(
            self.worker(lease_seconds=1, heartbeat_interval_seconds=0.02)._heartbeat_interval_seconds,
            0.02,
        )

    def test_active_rights_revision_rebuilds_projection_without_a_memory_payload_target(self):
        store = _Store()
        context = OwnerTruthCommandContext(
            vault_id="vault-projection-rights-worker",
            owner_subject_id="owner-projection-rights-worker",
            actor_subject_id="owner-projection-rights-worker",
        )
        rights = OwnerTruthProjectionRightsSnapshot(
            vault_id=context.vault_id,
            owner_subject_id=context.owner_subject_id,
            authority_epoch=6,
            revision=1,
            state=ProjectionRightsState.ACTIVE,
            event_hash=_digest({"event": "projection-rights-worker-active"}),
        )
        intent = build_memory_projection_rebuild_effect_intent_for_rights_revision(
            context=context,
            rights=rights,
        )
        store.lease_repository.seed(intent)
        store.admission_repository.seed_vault(
            vault_id=context.vault_id,
            owner_subject_id=context.owner_subject_id,
            authority_epoch=6,
            status="active",
        )
        store.admission_repository.seed_projection_rights_revision(
            vault_id=context.vault_id,
            owner_subject_id=context.owner_subject_id,
            authority_epoch=6,
            revision=1,
            state="active",
            event_hash=rights.event_hash,
        )

        result = OwnerTruthMemoryProjectionWorkerRuntime(
            settings=Settings(
                async_effect_v1_enabled=True,
                async_effect_worker_enabled=True,
                owner_truth_memory_projection_worker_enabled=True,
            ),
            store=store,
            worker_id="projection-rights-worker-test",
            retry_seconds=5,
        ).run_once()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["reason"], "memoryProjectionRebuilt")
        self.assertEqual(result["projectionEntryCount"], 1)
        self.assertEqual(len(store.projection_repository.contexts), 1)
        self.assertEqual(
            store.lease_repository.attempt_state(intent.job_id, 1),
            "succeeded",
        )

    def test_revoked_rights_revision_terminalizes_without_rebuilding_projection(self):
        store = _Store()
        context = OwnerTruthCommandContext(
            vault_id="vault-projection-rights-worker-revoked",
            owner_subject_id="owner-projection-rights-worker-revoked",
            actor_subject_id="owner-projection-rights-worker-revoked",
        )
        rights = OwnerTruthProjectionRightsSnapshot(
            vault_id=context.vault_id,
            owner_subject_id=context.owner_subject_id,
            authority_epoch=6,
            revision=1,
            state=ProjectionRightsState.REVOKED,
            event_hash=_digest({"event": "projection-rights-worker-revoked"}),
        )
        intent = build_memory_projection_rebuild_effect_intent_for_rights_revision(
            context=context,
            rights=rights,
        )
        store.lease_repository.seed(intent)
        store.admission_repository.seed_vault(
            vault_id=context.vault_id,
            owner_subject_id=context.owner_subject_id,
            authority_epoch=6,
            status="active",
        )
        store.admission_repository.seed_projection_rights_revision(
            vault_id=context.vault_id,
            owner_subject_id=context.owner_subject_id,
            authority_epoch=6,
            revision=1,
            state="revoked",
            event_hash=rights.event_hash,
        )

        result = OwnerTruthMemoryProjectionWorkerRuntime(
            settings=Settings(
                async_effect_v1_enabled=True,
                async_effect_worker_enabled=True,
                owner_truth_memory_projection_worker_enabled=True,
            ),
            store=store,
            worker_id="projection-rights-worker-revoked-test",
            retry_seconds=5,
        ).run_once()

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "projectionRightsNotActive")
        self.assertEqual(store.projection_repository.contexts, [])
        self.assertEqual(
            store.lease_repository.attempt_state(intent.job_id, 1),
            "terminalFailed",
        )

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
        self._reset_for_intent(replace(self.intent, max_attempts=3))
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
        self._reset_for_intent(replace(self.intent, max_attempts=3))
        self.store.search_projection_repository.outcome = "sourceRebuilding"

        result = self.worker(search_projection_enabled=True).run_once()

        self.assertEqual(result["status"], "retryWait")
        self.assertEqual(result["reason"], "memoryProjectionRebuildRetryableFailure")
        self.assertEqual(self.store.consumer_repository._inbox, {})

    def test_stale_search_projection_checkpoint_never_terminalizes_the_job(self):
        self._reset_for_intent(replace(self.intent, max_attempts=3))
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

    def test_projection_error_at_default_attempt_limit_writes_terminal_receipt_and_dead_letter(self):
        self.store.projection_repository.fail = True

        result = self.worker().run_once()

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "memoryProjectionRetriesExhausted")
        self.assertEqual(result["projectionOutcome"], "failed")
        self.assertEqual(result["jobState"], "failed")
        self.assertEqual(result["businessOutcome"], "failed")
        self.assertEqual(result["deadLetterOutcome"], "admitted")
        self.assertEqual(result["deadLetterCause"], "maxAttemptsExceeded")
        self.assertEqual(result["deadLetterState"], "open")
        self.assertEqual(result["deadLetterNextAction"], "authorizedReplayRequired")
        self.assertEqual(
            self.store.lease_repository.attempt_state(self.intent.job_id, 1),
            "terminalFailed",
        )
        self.assertEqual(len(self.store.consumer_repository._inbox), 1)
        admission = self.store.dead_letter_repository.load(result["deadLetterId"])
        self.assertEqual(admission.intent, self.intent)
        self.assertEqual(admission.attempt, 1)
        self.assertEqual(admission.cause.value, "maxAttemptsExceeded")

    def test_projection_error_retries_until_explicit_attempt_limit(self):
        self._reset_for_intent(
            replace(self.intent, max_attempts=3),
            projection=_ProjectionRepository(fail=True),
        )
        worker = self.worker(retry_seconds=1)

        first = worker.run_once()
        sleep(1.05)
        second = worker.run_once()
        sleep(1.05)
        third = worker.run_once()

        self.assertEqual([first["status"], second["status"], third["status"]], ["retryWait", "retryWait", "failed"])
        self.assertEqual(third["reason"], "memoryProjectionRetriesExhausted")
        self.assertEqual(third["attempt"], 3)
        self.assertEqual(third["deadLetterCause"], "maxAttemptsExceeded")
        self.assertEqual(self.store.lease_repository.attempt_state(self.intent.job_id, 1), "retryableFailed")
        self.assertEqual(self.store.lease_repository.attempt_state(self.intent.job_id, 2), "retryableFailed")
        self.assertEqual(self.store.lease_repository.attempt_state(self.intent.job_id, 3), "terminalFailed")
        self.assertEqual(self.store.dead_letter_repository.record_count(), 1)

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
