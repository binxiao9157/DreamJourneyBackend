from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
from threading import Event, Thread
from time import sleep
import unittest

from app.async_effects.business_message_projection_effects import BusinessMessageProjectionRequest
from app.async_effects.business_message_projection_repository import (
    InboxAccountSnapshot,
    InMemoryBusinessMessageProjectionRepository,
)
from app.async_effects.business_message_projection_request_repository import (
    InMemoryBusinessMessageProjectionRequestRepository,
)
from app.async_effects.business_message_projection_worker import (
    BusinessMessageProjectionWorkerRuntime,
)
from app.async_effects.consumer_repository import (
    AsyncEffectSyntheticConsumerCommand,
    InMemoryAsyncEffectConsumerRepository,
)
from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectTarget
from app.async_effects.dead_letter_repository import InMemoryAsyncEffectDeadLetterRepository
from app.async_effects.lease_repository import InMemoryAsyncEffectLeaseRepository
from app.async_effects.legacy_identity_inbox_bridge import (
    InMemoryLegacyInboxAccountResolver,
    LegacyAliasClaimState,
    LegacyInboxAccountBinding,
)
from app.async_effects.message_notification_effects import (
    BusinessCompletionMessageSource,
    InAppMessageKind,
)
from app.core.config import Settings


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _request(
    *,
    max_attempts: int = 3,
    inbox_subject_id: str | None = None,
    inbox_vault_id: str | None = None,
) -> BusinessMessageProjectionRequest:
    source_intent = AsyncEffectIntent(
        operation_type="asyncEffect.synthetic.businessMessageProjectionWorker.fixture",
        target=AsyncEffectTarget(
            owner_subject_id="owner-message-worker-001",
            vault_id="vault-message-worker-001",
            resource_type="timeLetter",
            resource_id="letter-message-worker-001",
            resource_version=3,
            purpose="timeLetterDelivery",
            authority_epoch=4,
        ),
        payload_hash=_digest("business-message-worker-source"),
    )
    completion = InMemoryAsyncEffectConsumerRepository().consume(
        AsyncEffectSyntheticConsumerCommand(
            intent=source_intent,
            consumer_name="fixture.businessMessageProjectionWorker",
            business_target_key=source_intent.business_target_key,
            outcome="completed",
            reason_code="fixtureCompleted",
            result_ref_hash=_digest("business-message-worker-result"),
        )
    )
    source = BusinessCompletionMessageSource(
        intent=source_intent,
        completion=completion,
        message_kind=InAppMessageKind.TIME_LETTER,
        inbox_subject_id=inbox_subject_id,
        inbox_vault_id=inbox_vault_id,
    )
    return BusinessMessageProjectionRequest(
        source=source,
        inbox_account=InboxAccountSnapshot(
            inbox_subject_id=str(source.inbox_subject_id),
            inbox_vault_id=str(source.inbox_vault_id),
            account_epoch=4,
        ),
        max_attempts=max_attempts,
    )


class _Store:
    def __init__(self, *, projection_repository=None, inbox_resolver=None) -> None:
        self.lease_repository = InMemoryAsyncEffectLeaseRepository()
        self.consumer_repository = InMemoryAsyncEffectConsumerRepository()
        self.dead_letter_repository = InMemoryAsyncEffectDeadLetterRepository()
        self.request_repository = InMemoryBusinessMessageProjectionRequestRepository()
        self.projection_repository = projection_repository or InMemoryBusinessMessageProjectionRepository()
        self.inbox_resolver = inbox_resolver
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

    def async_effect_business_message_projection_request_repository(self):
        return self.request_repository

    def async_effect_business_message_projection_repository(self):
        return self.projection_repository

    def async_effect_legacy_inbox_account_resolver(self):
        return self.inbox_resolver


def _inbox_resolver(
    request: BusinessMessageProjectionRequest,
    **changes: object,
) -> InMemoryLegacyInboxAccountResolver:
    inbox = request.inbox_account
    values: dict[str, object] = {
        "legacy_user_id": "user-message-worker-001",
        "legacy_alias_hash": _digest("message-worker-alias"),
        "subject_id": inbox.inbox_subject_id,
        "vault_id": inbox.inbox_vault_id,
        "claim_state": LegacyAliasClaimState.VERIFIED,
        "identity_proof_subject_id": inbox.inbox_subject_id,
        "subject_state": "active",
        "vault_owner_subject_id": inbox.inbox_subject_id,
        "vault_state": "active",
        "account_access_state": "active",
        "account_deletion_state": "active",
        "account_auth_epoch": inbox.account_epoch,
        "bridge_row_version": 1,
    }
    values.update(changes)
    return InMemoryLegacyInboxAccountResolver([LegacyInboxAccountBinding(**values)])  # type: ignore[arg-type]


class _FailingProjectionRepository:
    def record(self, *_args, **_kwargs):
        raise RuntimeError("message projection fixture failure")


class _BlockingProjectionRepository:
    def __init__(self, *, started: Event, release: Event) -> None:
        self._delegate = InMemoryBusinessMessageProjectionRepository()
        self._started = started
        self._release = release

    def record(self, *args, **kwargs):
        self._started.set()
        if not self._release.wait(timeout=3.0):
            raise TimeoutError("message projection test fixture was not released")
        return self._delegate.record(*args, **kwargs)

    def record_count(self) -> int:
        return self._delegate.record_count()


class _RecordingMetricRecorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def record_attempt(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        return {"sinkOutcome": "notConfigured"}


class _FailingMetricRecorder:
    def record_attempt(self, **_kwargs: object) -> dict[str, object]:
        raise RuntimeError("metric sink unavailable")


class BusinessMessageProjectionWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.request = _request()
        self.store = self._store_for_request(self.request)

    @staticmethod
    def _store_for_request(
        request: BusinessMessageProjectionRequest,
        *,
        projection_repository=None,
        persist_request: bool = True,
    ) -> _Store:
        store = _Store(
            projection_repository=projection_repository,
            inbox_resolver=_inbox_resolver(request),
        )
        store.lease_repository.seed(request.effect_intent)
        if persist_request:
            store.request_repository.record(request)
        return store

    def _worker(
        self,
        *,
        store: _Store | None = None,
        enabled: bool = True,
        worker_id: str = "business-message-projection-worker-test",
        lease_seconds: int = 60,
        retry_seconds: int = 1,
        heartbeat_interval_seconds: float | None = None,
        operation_metric_recorder=None,
    ) -> BusinessMessageProjectionWorkerRuntime:
        return BusinessMessageProjectionWorkerRuntime(
            settings=Settings(
                async_effect_v1_enabled=True,
                async_effect_worker_enabled=True,
                business_message_projection_worker_enabled=enabled,
            ),
            store=store or self.store,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            retry_seconds=retry_seconds,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
            operation_metric_recorder=operation_metric_recorder,
        )

    def test_default_disabled_worker_does_not_claim_a_message_projection_job(self) -> None:
        result = self._worker(enabled=False).run_once()

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "businessMessageProjectionWorkerDisabled")
        lease = self.store.lease_repository.claim_next(
            worker_id="message-projection-verification-worker",
            lease_seconds=10,
            supported_job_types=[self.request.effect_intent.job_type],
        )
        self.assertIsNotNone(lease)

    def test_completed_worker_writes_only_one_metadata_shadow_and_typed_receipt(self) -> None:
        result = self._worker().run_once()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["reason"], "businessMessageProjectionRecorded")
        self.assertEqual(result["messageProjectionOutcome"], "recorded")
        self.assertEqual(result["jobState"], "succeeded")
        self.assertEqual(result["consumerOutcome"], "accepted")
        self.assertEqual(result["businessOutcome"], "completed")
        self.assertEqual(result["consumerInboxState"], "completed")
        self.assertEqual(self.store.projection_repository.record_count(), 1)
        self.assertNotIn("owner-message-worker-001", str(result))
        self.assertNotIn("vault-message-worker-001", str(result))

    def test_replay_after_prior_shadow_write_deduplicates_without_another_record(self) -> None:
        projection_repository = InMemoryBusinessMessageProjectionRepository()
        first_store = self._store_for_request(
            self.request,
            projection_repository=projection_repository,
        )
        first = self._worker(store=first_store).run_once()
        replay_store = self._store_for_request(
            self.request,
            projection_repository=projection_repository,
        )
        replay = self._worker(store=replay_store).run_once()

        self.assertEqual(first["messageProjectionOutcome"], "recorded")
        self.assertEqual(replay["messageProjectionOutcome"], "deduplicated")
        self.assertEqual(replay["reason"], "businessMessageProjectionDeduplicated")
        self.assertEqual(projection_repository.record_count(), 1)

    def test_missing_immutable_input_is_terminally_blocked(self) -> None:
        store = self._store_for_request(self.request, persist_request=False)

        result = self._worker(store=store).run_once()

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "businessMessageProjectionInputUnavailable")
        self.assertEqual(result["jobState"], "blocked")
        self.assertEqual(result["businessOutcome"], "blocked")
        self.assertEqual(store.projection_repository.record_count(), 0)

    def test_inactive_or_rotated_inbox_is_terminally_blocked_before_shadow_write(self) -> None:
        for resolver, reason in (
            (
                _inbox_resolver(
                    self.request,
                    account_access_state="suspended_restorable",
                ),
                "businessMessageProjectionInboxUnavailable",
            ),
            (
                _inbox_resolver(
                    self.request,
                    account_auth_epoch=self.request.inbox_account.account_epoch + 1,
                ),
                "businessMessageProjectionInboxSnapshotMismatch",
            ),
        ):
            with self.subTest(reason=reason):
                store = self._store_for_request(self.request)
                store.inbox_resolver = resolver

                result = self._worker(store=store).run_once()

                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["reason"], reason)
                self.assertEqual(result["jobState"], "blocked")
                self.assertEqual(result["businessOutcome"], "blocked")
                self.assertEqual(store.projection_repository.record_count(), 0)

    def test_cross_account_snapshot_is_blocked_until_a_dedicated_admission_worker_exists(self) -> None:
        request = _request(
            inbox_subject_id="family-message-worker-001",
            inbox_vault_id="vault-family-message-worker-001",
        )
        store = self._store_for_request(request)

        result = self._worker(store=store).run_once()

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "businessMessageProjectionCrossAccountUnsupported")
        self.assertEqual(result["jobState"], "blocked")
        self.assertEqual(store.projection_repository.record_count(), 0)

    def test_projection_failure_at_attempt_limit_creates_terminal_receipt_and_dead_letter(self) -> None:
        request = _request(max_attempts=1)
        store = self._store_for_request(
            request,
            projection_repository=_FailingProjectionRepository(),
        )

        result = self._worker(store=store).run_once()

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "businessMessageProjectionRetriesExhausted")
        self.assertEqual(result["jobState"], "failed")
        self.assertEqual(result["businessOutcome"], "failed")
        self.assertEqual(result["deadLetterCause"], "maxAttemptsExceeded")
        self.assertEqual(result["deadLetterState"], "open")
        self.assertEqual(store.lease_repository.attempt_state(request.effect_intent.job_id, 1), "terminalFailed")
        self.assertEqual(store.dead_letter_repository.record_count(), 1)

    def test_slow_shadow_write_heartbeats_lease_and_blocks_a_competing_worker(self) -> None:
        started = Event()
        release = Event()
        projection_repository = _BlockingProjectionRepository(started=started, release=release)
        store = self._store_for_request(self.request, projection_repository=projection_repository)
        first = self._worker(
            store=store,
            worker_id="business-message-projection-first-worker",
            lease_seconds=1,
            heartbeat_interval_seconds=0.02,
        )
        first_result: dict[str, object] = {}
        thread = Thread(target=lambda: first_result.update(first.run_once()))
        thread.start()
        self.assertTrue(started.wait(timeout=1.0))

        sleep(1.1)
        contender = self._worker(
            store=store,
            worker_id="business-message-projection-contender",
            lease_seconds=1,
        ).run_once()
        self.assertEqual(contender["status"], "idle")

        release.set()
        thread.join(timeout=3.0)
        self.assertFalse(thread.is_alive())
        self.assertEqual(first_result["status"], "completed")
        self.assertEqual(projection_repository.record_count(), 1)

    def test_heartbeat_failure_leaves_completion_unknown_without_a_typed_receipt(self) -> None:
        started = Event()
        release = Event()
        heartbeat_attempted = Event()
        projection_repository = _BlockingProjectionRepository(started=started, release=release)
        store = self._store_for_request(self.request, projection_repository=projection_repository)
        original_heartbeat = store.lease_repository.heartbeat
        heartbeat_calls = 0

        def fail_heartbeat(*_args, **_kwargs) -> None:
            nonlocal heartbeat_calls
            heartbeat_calls += 1
            if heartbeat_calls == 1:
                return original_heartbeat(*_args, **_kwargs)
            heartbeat_attempted.set()
            raise RuntimeError("message projection heartbeat fixture failure")

        store.lease_repository.heartbeat = fail_heartbeat
        worker = self._worker(
            store=store,
            lease_seconds=1,
            heartbeat_interval_seconds=0.01,
        )
        result: dict[str, object] = {}
        thread = Thread(target=lambda: result.update(worker.run_once()))
        try:
            thread.start()
            self.assertTrue(started.wait(timeout=1.0))
            self.assertTrue(heartbeat_attempted.wait(timeout=1.0))
            release.set()
            thread.join(timeout=3.0)
        finally:
            store.lease_repository.heartbeat = original_heartbeat
            release.set()
            thread.join(timeout=3.0)

        self.assertFalse(thread.is_alive())
        self.assertEqual(result["status"], "lost")
        self.assertEqual(result["reason"], "businessMessageProjectionLeaseLost")
        self.assertEqual(store.consumer_repository._inbox, {})
        # The shadow may already be durable when a heartbeat becomes unknown;
        # replay must deduplicate it instead of claiming no side effect occurred.
        self.assertEqual(projection_repository.record_count(), 1)

    def test_claimed_job_records_value_free_metric_and_metric_failure_is_non_fatal(self) -> None:
        recorder = _RecordingMetricRecorder()

        result = self._worker(operation_metric_recorder=recorder).run_once()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(recorder.calls), 1)
        call = recorder.calls[0]
        self.assertEqual(call["component_id"], "businessMessageProjectionWorker")
        self.assertEqual(call["operation"], "businessMessageProjection")
        self.assertEqual(call["outcome"], "succeeded")
        self.assertNotIn("owner-message-worker-001", str(call))

        another_store = self._store_for_request(self.request)
        metric_failure = self._worker(
            store=another_store,
            operation_metric_recorder=_FailingMetricRecorder(),
        ).run_once()
        self.assertEqual(metric_failure["status"], "completed")


if __name__ == "__main__":
    unittest.main()
