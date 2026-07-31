from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
import unittest
from uuid import uuid4

from app.async_effects.consumer_repository import InMemoryAsyncEffectConsumerRepository
from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectTarget
from app.async_effects.lease_repository import InMemoryAsyncEffectLeaseRepository
from app.async_effects.owner_truth_candidate_extraction_worker import (
    OwnerTruthCandidateExtractionWorkerRuntime,
)
from app.async_effects.target_admission import InMemoryOwnerTruthSourceTargetAdmissionRepository
from app.core.config import Settings
from app.services.owner_truth_candidate_extraction import (
    InMemoryOwnerTruthCandidateExtractionRepository,
    OwnerTruthCandidateExtractionInput,
    PostgresOwnerTruthCandidateExtractionInputRepository,
)


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class _SourceInputRepository:
    def __init__(self, *, source_content_hash: str, source_text: str) -> None:
        self.source_content_hash = source_content_hash
        self.source_text = source_text
        self.fail = False
        self.intents: list[AsyncEffectIntent] = []

    def read_for_candidate_extraction(
        self,
        intent: AsyncEffectIntent,
    ) -> OwnerTruthCandidateExtractionInput:
        self.intents.append(intent)
        if self.fail:
            raise RuntimeError("candidate input fixture failure")
        return OwnerTruthCandidateExtractionInput(
            source_content_hash=self.source_content_hash,
            source_text=self.source_text,
        )


class _Store:
    def __init__(
        self,
        *,
        vault_id: str,
        owner_subject_id: str,
        source_id: str,
        source_content_hash: str,
        source_text: str,
        candidate_repository: InMemoryOwnerTruthCandidateExtractionRepository | None = None,
        candidate_extraction_allowed: bool = True,
    ) -> None:
        self.lease_repository = InMemoryAsyncEffectLeaseRepository()
        self.consumer_repository = InMemoryAsyncEffectConsumerRepository()
        self.admission_repository = InMemoryOwnerTruthSourceTargetAdmissionRepository()
        self.input_repository = _SourceInputRepository(
            source_content_hash=source_content_hash,
            source_text=source_text,
        )
        self.candidate_repository = (
            candidate_repository or InMemoryOwnerTruthCandidateExtractionRepository()
        )
        self.uow_calls = 0
        self.admission_repository.seed_vault(
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            authority_epoch=7,
            status="active",
        )
        self.admission_repository.seed_source(
            vault_id=vault_id,
            source_id=source_id,
            owner_subject_id=owner_subject_id,
            authority_epoch=7,
            source_version=1,
            state="active",
            candidate_extraction_allowed=candidate_extraction_allowed,
        )

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

    def owner_truth_source_target_admission_repository(self):
        return self.admission_repository

    def owner_truth_candidate_extraction_input_repository(self):
        return self.input_repository

    def owner_truth_candidate_extraction_repository(self):
        return self.candidate_repository


class _FailingExtractor:
    def extract(self, **_kwargs):
        raise RuntimeError("deterministic extractor fixture failure")


class _RecordingMetricRecorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def record_attempt(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        return {"sinkOutcome": "notConfigured"}


class _FailingMetricRecorder:
    def record_attempt(self, **_kwargs: object) -> dict[str, object]:
        raise RuntimeError("metric sink unavailable")


class _PostgresInputCursor:
    def __init__(self, row: dict[str, object]) -> None:
        self.row = row
        self.queries: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, *_args, **_kwargs):
        self.queries.append(str(query))

    def fetchone(self):
        return self.row


class _PostgresInputConnection:
    def __init__(self, row: dict[str, object]) -> None:
        self.cursor_instance = _PostgresInputCursor(row)

    def cursor(self, **_kwargs):
        return self.cursor_instance


class OwnerTruthCandidateExtractionWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.vault_id = "vault-candidate-worker"
        self.owner_subject_id = "owner-candidate-worker"
        self.source_id = str(uuid4())
        self.source_text = "我小时候常在河边听外公讲故事，也记得那条河很安静。"
        self.source_content_hash = _digest(self.source_text)
        self.intent = AsyncEffectIntent(
            operation_type="ownerTruth.source.created",
            target=AsyncEffectTarget(
                owner_subject_id=self.owner_subject_id,
                vault_id=self.vault_id,
                resource_type="source",
                resource_id=self.source_id,
                resource_version=1,
                purpose="candidateExtraction",
                authority_epoch=7,
            ),
            payload_hash=_digest("candidate-extraction-worker-command"),
        )
        self.store = self._new_store()
        self.store.lease_repository.seed(self.intent)

    def _new_store(
        self,
        *,
        candidate_repository: InMemoryOwnerTruthCandidateExtractionRepository | None = None,
        candidate_extraction_allowed: bool = True,
    ) -> _Store:
        return _Store(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            source_id=self.source_id,
            source_content_hash=self.source_content_hash,
            source_text=self.source_text,
            candidate_repository=candidate_repository,
            candidate_extraction_allowed=candidate_extraction_allowed,
        )

    def _worker(
        self,
        *,
        store: _Store | None = None,
        enabled: bool = True,
        extractor=None,
        operation_metric_recorder=None,
    ) -> OwnerTruthCandidateExtractionWorkerRuntime:
        return OwnerTruthCandidateExtractionWorkerRuntime(
            settings=Settings(
                async_effect_v1_enabled=True,
                async_effect_worker_enabled=True,
                owner_truth_candidate_extraction_worker_enabled=enabled,
            ),
            store=store or self.store,
            worker_id="candidate-extraction-worker-test",
            retry_seconds=5,
            extractor=extractor,
            operation_metric_recorder=operation_metric_recorder,
        )

    def test_default_disabled_worker_does_not_claim_a_candidate_extraction_job(self) -> None:
        result = self._worker(enabled=False).run_once()

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "ownerTruthCandidateExtractionWorkerDisabled")
        lease = self.store.lease_repository.claim_next(
            worker_id="verification-worker",
            lease_seconds=10,
            supported_job_types=["ownerTruth.source.created"],
        )
        self.assertIsNotNone(lease)

    def test_current_source_creates_one_restricted_pending_candidate_without_raw_worker_output(self) -> None:
        result = self._worker().run_once()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["reason"], "candidateExtractionProposalsPersisted")
        self.assertEqual(result["candidateCount"], 1)
        self.assertEqual(result["extractionStatus"], "succeeded")
        self.assertEqual(result["jobState"], "succeeded")
        self.assertEqual(result["consumerInboxState"], "completed")
        self.assertNotIn(self.source_text, json.dumps(result, ensure_ascii=False, sort_keys=True))

        snapshot = self.store.candidate_repository.snapshot()
        self.assertEqual(len(snapshot["extractions"]), 1)
        self.assertEqual(len(snapshot["candidates"]), 1)
        candidate = next(iter(snapshot["candidates"].values()))
        self.assertEqual(candidate["decisionStatus"], "pending")
        self.assertEqual(candidate["payload"]["candidateKind"], "experience")
        self.assertEqual(candidate["payload"]["perspectiveType"], "inferred")
        self.assertEqual(candidate["payload"]["epistemicStatus"], "uncertain")
        self.assertEqual(candidate["payload"]["sensitivity"], "restricted")
        self.assertEqual(candidate["payload"]["reviewMode"], "single")
        self.assertEqual(candidate["payload"]["evidenceRefs"][0]["span"], {"start": 0, "end": len(self.source_text)})

    def test_replay_deduplicates_the_immutable_extraction_and_candidate(self) -> None:
        first = self._worker().run_once()
        replay_store = self._new_store(candidate_repository=self.store.candidate_repository)
        replay_store.lease_repository.seed(self.intent)

        replayed = self._worker(store=replay_store).run_once()

        self.assertEqual(first["extractionId"], replayed["extractionId"])
        self.assertEqual(replayed["candidateCount"], 1)
        snapshot = self.store.candidate_repository.snapshot()
        self.assertEqual(len(snapshot["extractions"]), 1)
        self.assertEqual(len(snapshot["candidates"]), 1)

    def test_stale_revoked_and_deleted_sources_are_terminally_blocked(self) -> None:
        cases = (
            ("stale", "authorityEpochChanged", lambda store: store.admission_repository.seed_vault(
                vault_id=self.vault_id,
                owner_subject_id=self.owner_subject_id,
                authority_epoch=8,
                status="active",
            )),
            ("revoked", "vaultInactive", lambda store: store.admission_repository.seed_vault(
                vault_id=self.vault_id,
                owner_subject_id=self.owner_subject_id,
                authority_epoch=7,
                status="revoked",
            )),
            ("deleted", "sourceInactive", lambda store: store.admission_repository.seed_source(
                vault_id=self.vault_id,
                source_id=self.source_id,
                owner_subject_id=self.owner_subject_id,
                authority_epoch=7,
                source_version=1,
                state="deleted",
            )),
        )
        for name, reason, mutate in cases:
            with self.subTest(name=name):
                store = self._new_store()
                store.lease_repository.seed(self.intent)
                mutate(store)

                result = self._worker(store=store).run_once()

                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["reason"], reason)
                self.assertEqual(result["jobState"], "blocked")
                self.assertEqual(store.candidate_repository.snapshot()["extractions"], {})
                self.assertEqual(store.input_repository.intents, [])

    def test_default_off_source_is_terminally_blocked_before_input_or_candidate(self) -> None:
        store = self._new_store(candidate_extraction_allowed=False)
        store.lease_repository.seed(self.intent)

        result = self._worker(store=store).run_once()

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "sourceCandidateExtractionDisabled")
        self.assertEqual(result["jobState"], "blocked")
        self.assertEqual(store.candidate_repository.snapshot()["extractions"], {})
        self.assertEqual(store.candidate_repository.snapshot()["candidates"], {})
        self.assertEqual(store.input_repository.intents, [])

    def test_invalid_source_text_is_quarantined_without_a_candidate(self) -> None:
        self.store.input_repository.source_text = "   "

        result = self._worker().run_once()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["reason"], "candidateExtractionQuarantined")
        self.assertEqual(result["candidateCount"], 0)
        self.assertEqual(result["extractionStatus"], "quarantined")
        self.assertNotIn("sourceText", json.dumps(result, sort_keys=True))
        snapshot = self.store.candidate_repository.snapshot()
        self.assertEqual(len(snapshot["extractions"]), 1)
        self.assertEqual(snapshot["candidates"], {})

    def test_adapter_failure_releases_only_the_current_job_for_retry(self) -> None:
        result = self._worker(extractor=_FailingExtractor()).run_once()

        self.assertEqual(result["status"], "retryWait")
        self.assertEqual(result["reason"], "candidateExtractionRetryableFailure")
        self.assertEqual(
            self.store.lease_repository.attempt_state(self.intent.job_id, 1),
            "retryableFailed",
        )
        self.assertEqual(self.store.candidate_repository.snapshot()["extractions"], {})
        self.assertEqual(self.store.consumer_repository._inbox, {})

    def test_claimed_job_records_value_free_worker_attempt_metric(self) -> None:
        recorder = _RecordingMetricRecorder()

        result = self._worker(operation_metric_recorder=recorder).run_once()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(recorder.calls), 1)
        call = recorder.calls[0]
        self.assertEqual(call["component_kind"], "worker")
        self.assertEqual(call["component_id"], "ownerTruthCandidateExtractionWorker")
        self.assertEqual(call["operation"], "ownerTruthCandidateExtraction")
        self.assertEqual(call["outcome"], "succeeded")
        self.assertEqual(call["feedback_state"], "notApplicable")
        self.assertEqual(call["request_key"], result["jobId"])
        self.assertEqual(call["operation_key"], result["operationId"])
        self.assertNotIn(self.source_text, json.dumps(call, ensure_ascii=False, sort_keys=True))

    def test_metric_failure_does_not_change_private_extraction_result(self) -> None:
        result = self._worker(operation_metric_recorder=_FailingMetricRecorder()).run_once()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["candidateCount"], 1)

    def test_postgres_input_repository_reads_source_text_only_under_share_lock(self) -> None:
        connection = _PostgresInputConnection(
            {
                "source_version": 1,
                "content_hash": self.source_content_hash,
                "content_payload": {"text": self.source_text},
            }
        )

        source = PostgresOwnerTruthCandidateExtractionInputRepository(
            connection
        ).read_for_candidate_extraction(self.intent)

        self.assertEqual(source.source_content_hash, self.source_content_hash)
        self.assertEqual(source.source_text, self.source_text)
        self.assertIn("FOR SHARE", connection.cursor_instance.queries[0])


if __name__ == "__main__":
    unittest.main()
