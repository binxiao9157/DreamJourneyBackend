from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
import json
from threading import Event, Thread
from time import sleep
import unittest
from uuid import uuid4

import httpx

from app.async_effects.consumer_repository import InMemoryAsyncEffectConsumerRepository
from app.async_effects.dead_letter_repository import InMemoryAsyncEffectDeadLetterRepository
from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectTarget
from app.async_effects.lease_repository import InMemoryAsyncEffectLeaseRepository
from app.async_effects.owner_truth_candidate_extraction_worker import (
    DeterministicOwnerTruthCandidateExtractor,
    ModelAssistedOwnerTruthLiveConversationExtractor,
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


def _facets(**values: list[dict[str, object]]) -> dict[str, object]:
    return {
        "people": values.get("people", []),
        "time": values.get("time", []),
        "places": values.get("places", []),
        "relationships": values.get("relationships", []),
        "emotions": values.get("emotions", []),
        "values": values.get("values", []),
        "personality": values.get("personality", []),
        "confidence": 0.9,
    }


class _SourceInputRepository:
    def __init__(
        self,
        *,
        source_content_hash: str,
        source_text: str,
        source_metadata: dict[str, object] | None = None,
    ) -> None:
        self.source_content_hash = source_content_hash
        self.source_text = source_text
        self.source_metadata = source_metadata or {}
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
            source_metadata=self.source_metadata,
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
        source_metadata: dict[str, object] | None = None,
        candidate_repository: InMemoryOwnerTruthCandidateExtractionRepository | None = None,
        candidate_extraction_allowed: bool = True,
    ) -> None:
        self.lease_repository = InMemoryAsyncEffectLeaseRepository()
        self.consumer_repository = InMemoryAsyncEffectConsumerRepository()
        self.dead_letter_repository = InMemoryAsyncEffectDeadLetterRepository()
        self.admission_repository = InMemoryOwnerTruthSourceTargetAdmissionRepository()
        self.input_repository = _SourceInputRepository(
            source_content_hash=source_content_hash,
            source_text=source_text,
            source_metadata=source_metadata,
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

    def async_effect_dead_letter_repository(self):
        return self.dead_letter_repository

    def owner_truth_source_target_admission_repository(self):
        return self.admission_repository

    def owner_truth_candidate_extraction_input_repository(self):
        return self.input_repository

    def owner_truth_candidate_extraction_repository(self):
        return self.candidate_repository


class _FailingExtractor:
    def extract(self, **_kwargs):
        raise RuntimeError("deterministic extractor fixture failure")


class _RecordingLiveMemoryOrganizer:
    model = "deepseek-live-memory-test"
    prompt_version = "owner-truth-live-memory-organization-test-v1"

    def __init__(self, memories: list[dict[str, object]]) -> None:
        self.memories = memories
        self.turns: list[dict[str, object]] | None = None
        self.calls: list[list[dict[str, object]]] = []

    def request_organization(self, *, turns):
        self.turns = list(turns)
        self.calls.append(list(turns))
        return {"memories": self.memories}


class _UnavailableLiveMemoryOrganizer:
    model = "deepseek-live-memory-test"
    prompt_version = "owner-truth-live-memory-organization-test-v1"

    def request_organization(self, *, turns):
        request = httpx.Request("POST", "https://provider.invalid/chat/completions")
        raise httpx.ConnectError("provider unavailable", request=request)


class _BlockingExtractor:
    def __init__(self, *, started: Event, release: Event) -> None:
        self._started = started
        self._release = release
        self._delegate = DeterministicOwnerTruthCandidateExtractor()

    def extract(self, **kwargs):
        self._started.set()
        if not self._release.wait(timeout=3.0):
            raise RuntimeError("candidate extraction test fixture timed out")
        return self._delegate.extract(**kwargs)


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
        source_metadata: dict[str, object] | None = None,
    ) -> _Store:
        return _Store(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            source_id=self.source_id,
            source_content_hash=self.source_content_hash,
            source_text=self.source_text,
            source_metadata=source_metadata,
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
        worker_id: str = "candidate-extraction-worker-test",
        lease_seconds: int = 60,
        retry_seconds: int = 5,
        heartbeat_interval_seconds: float | None = None,
    ) -> OwnerTruthCandidateExtractionWorkerRuntime:
        return OwnerTruthCandidateExtractionWorkerRuntime(
            settings=Settings(
                async_effect_v1_enabled=True,
                async_effect_worker_enabled=True,
                owner_truth_candidate_extraction_worker_enabled=enabled,
            ),
            store=store or self.store,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            retry_seconds=retry_seconds,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
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

    def test_owner_authored_source_creates_one_pending_first_person_candidate_without_raw_worker_output(self) -> None:
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
        self.assertEqual(candidate["payload"]["perspectiveType"], "firstPerson")
        self.assertEqual(candidate["payload"]["epistemicStatus"], "recalled")
        self.assertEqual(candidate["payload"]["sensitivity"], "standard")
        self.assertEqual(candidate["payload"]["reviewMode"], "single")
        self.assertEqual(candidate["payload"]["evidenceRefs"][0]["span"], {"start": 0, "end": len(self.source_text)})

    def test_live_digest_uses_only_user_evidence_and_excludes_assistant_suggestions(self) -> None:
        first_user_turn = "我小时候住在河边。河水很安静。"
        repeated_user_turn = "河水很安静，我常和外公去散步。"
        assistant_turn = "所以你在上海长大，对吗？"
        source_text = f"{first_user_turn}\n\n{repeated_user_turn}"
        source_hash = _digest(source_text)
        store = _Store(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            source_id=self.source_id,
            source_content_hash=source_hash,
            source_text=source_text,
            source_metadata={
                "sourcePolicy": "userEvidenceOnly",
                "conversationTurns": [
                    {"index": 1, "role": "user", "text": first_user_turn},
                    {"index": 2, "role": "assistant", "text": assistant_turn},
                    {"index": 3, "role": "user", "text": repeated_user_turn},
                ],
            },
        )
        store.lease_repository.seed(self.intent)

        result = self._worker(store=store).run_once()

        self.assertEqual(result["status"], "completed")
        candidate = next(iter(store.candidate_repository.snapshot()["candidates"].values()))
        summary = candidate["payload"]["content"]["summary"]
        self.assertIn("我小时候住在河边", summary)
        self.assertIn("我常和外公去散步", summary)
        self.assertNotIn("上海", summary)
        self.assertNotIn("对吗", summary)
        self.assertNotIn("\n", summary)

    def test_closed_live_conversation_uses_semantic_organization_for_pending_memories(self) -> None:
        first_user_turn = "我小时候住在河边，常和外公去散步。"
        assistant_turn = "那段经历让你学到了什么？"
        second_user_turn = "我觉得陪伴比讲道理更重要，也一直很怀念外公。"
        source_text = f"{first_user_turn}\n\n{second_user_turn}"
        organizer = _RecordingLiveMemoryOrganizer(
            [
                {
                    "memoryKind": "experience",
                    "summary": "我小时候常和外公在河边散步。",
                    "sourceTurnIndices": [1],
                    "facets": _facets(
                        people=[
                            {
                                "value": "外公",
                                "evidenceMode": "ownerStated",
                                "confidence": 1.0,
                                "sourceTurnIndices": [1],
                            }
                        ]
                    ),
                },
                {
                    "memoryKind": "knowledge",
                    "claim": "我认为陪伴比讲道理更重要。",
                    "sourceTurnIndices": [3],
                    "facets": _facets(
                        values=[
                            {
                                "value": "陪伴",
                                "evidenceMode": "ownerStated",
                                "confidence": 1.0,
                                "sourceTurnIndices": [3],
                            }
                        ]
                    ),
                },
                {
                    "memoryKind": "emotion",
                    "label": "我一直很怀念外公。",
                    "sourceTurnIndices": [3],
                    "facets": _facets(
                        emotions=[
                            {
                                "value": "怀念",
                                "evidenceMode": "ownerStated",
                                "confidence": 1.0,
                                "sourceTurnIndices": [3],
                            }
                        ]
                    ),
                },
            ]
        )
        extractor = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=Settings(owner_truth_live_memory_organization_enabled=True),
            organizer=organizer,
        )
        store = _Store(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            source_id=self.source_id,
            source_content_hash=_digest(source_text),
            source_text=source_text,
            source_metadata={
                "captureMode": "live",
                "sourcePolicy": "userEvidenceOnly",
                "conversationTurns": [
                    {
                        "index": 1,
                        "role": "user",
                        "text": first_user_turn,
                        "captureMode": "live",
                    },
                    {
                        "index": 2,
                        "role": "assistant",
                        "text": assistant_turn,
                        "captureMode": "live",
                    },
                    {
                        "index": 3,
                        "role": "user",
                        "text": second_user_turn,
                        "captureMode": "live",
                    },
                ],
            },
        )
        store.lease_repository.seed(self.intent)

        result = self._worker(store=store, extractor=extractor).run_once()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["candidateCount"], 3)
        self.assertEqual(
            organizer.turns,
            [
                {"index": 1, "role": "user", "text": first_user_turn},
                {"index": 2, "role": "assistant", "text": assistant_turn},
                {"index": 3, "role": "user", "text": second_user_turn},
            ],
        )
        candidates = list(store.candidate_repository.snapshot()["candidates"].values())
        payloads = {candidate["payload"]["candidateKind"]: candidate["payload"] for candidate in candidates}
        self.assertEqual(payloads["experience"]["content"]["summary"], "我小时候常和外公在河边散步。")
        self.assertEqual(payloads["knowledge"]["content"]["claim"], "我认为陪伴比讲道理更重要。")
        self.assertEqual(payloads["emotion"]["content"]["label"], "我一直很怀念外公。")
        self.assertTrue(
            all(payload["contentSchemaVersion"] == "owner-truth-v2" for payload in payloads.values())
        )
        self.assertEqual(
            payloads["experience"]["content"]["facets"]["people"][0]["value"],
            "外公",
        )
        self.assertTrue(all(payload["reviewMode"] == "single" for payload in payloads.values()))
        self.assertTrue(all(payload["confidence"] == 0.0 for payload in payloads.values()))

    def test_live_organization_transport_failure_falls_back_to_owner_evidence(self) -> None:
        owner_turn = "我记得外公总会在河边等我。"
        extractor = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=Settings(owner_truth_live_memory_organization_enabled=True),
            organizer=_UnavailableLiveMemoryOrganizer(),
        )

        command = extractor.extract(
            intent=self.intent,
            source=OwnerTruthCandidateExtractionInput(
                source_content_hash=_digest(owner_turn),
                source_text=owner_turn,
                source_metadata={
                    "captureMode": "live",
                    "sourcePolicy": "userEvidenceOnly",
                    "conversationTurns": [
                        {
                            "index": 1,
                            "role": "user",
                            "text": owner_turn,
                            "captureMode": "live",
                        }
                    ],
                },
            ),
        )

        self.assertEqual(command.extractor_id, "deterministicLiveConversationDigest")
        self.assertEqual(command.model_id, "deterministic-live-conversation-digest-v1")
        self.assertEqual(len(command.proposals), 1)
        self.assertEqual(command.proposals[0].content["summary"], owner_turn)

    def test_live_organization_cannot_use_an_assistant_turn_as_evidence(self) -> None:
        owner_turn = "我小时候住在河边。"
        assistant_turn = "所以你是在上海长大，对吗？"
        organizer = _RecordingLiveMemoryOrganizer(
            [
                {
                    "memoryKind": "experience",
                    "summary": "我在上海长大。",
                    "sourceTurnIndices": [2],
                    "facets": _facets(),
                }
            ]
        )
        extractor = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=Settings(owner_truth_live_memory_organization_enabled=True),
            organizer=organizer,
        )
        store = _Store(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            source_id=self.source_id,
            source_content_hash=_digest(owner_turn),
            source_text=owner_turn,
            source_metadata={
                "captureMode": "live",
                "sourcePolicy": "userEvidenceOnly",
                "conversationTurns": [
                    {"index": 1, "role": "user", "text": owner_turn, "captureMode": "live"},
                    {"index": 2, "role": "assistant", "text": assistant_turn, "captureMode": "live"},
                ],
            },
        )
        store.lease_repository.seed(self.intent)

        result = self._worker(store=store, extractor=extractor).run_once()

        self.assertEqual(result["status"], "failed")
        self.assertEqual(store.candidate_repository.snapshot()["candidates"], {})

    def test_long_live_transcript_sends_every_turn_across_bounded_requests(self) -> None:
        turns = [
            {"index": 1, "role": "user", "text": "第一段经历。", "captureMode": "live"},
            {"index": 2, "role": "assistant", "text": "后来呢？", "captureMode": "live"},
            {"index": 3, "role": "user", "text": "第二段经历。", "captureMode": "live"},
            {"index": 4, "role": "assistant", "text": "当时什么感受？", "captureMode": "live"},
            {"index": 5, "role": "user", "text": "我觉得很安心。", "captureMode": "live"},
        ]
        organizer = _RecordingLiveMemoryOrganizer([])
        organizer.maximum_turn_count = 2
        organizer.maximum_turn_characters = 100
        organizer.maximum_total_characters = 100
        extractor = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=Settings(owner_truth_live_memory_organization_enabled=True),
            organizer=organizer,
        )
        source_text = "\n\n".join(
            str(turn["text"]) for turn in turns if turn["role"] == "user"
        )
        command = extractor.extract(
            intent=self.intent,
            source=OwnerTruthCandidateExtractionInput(
                source_content_hash=_digest(source_text),
                source_text=source_text,
                source_metadata={
                    "captureMode": "live",
                    "sourcePolicy": "userEvidenceOnly",
                    "conversationTurns": turns,
                },
            ),
        )

        sent_texts = {str(turn["text"]) for call in organizer.calls for turn in call}
        self.assertEqual(sent_texts, {str(turn["text"]) for turn in turns})
        self.assertGreater(len(organizer.calls), 1)
        self.assertTrue(all(any(turn["role"] == "user" for turn in call) for call in organizer.calls))
        self.assertEqual(command.proposals, ())

    def test_one_oversized_live_turn_is_split_without_dropping_text(self) -> None:
        owner_turn = "甲乙丙丁" * 80
        organizer = _RecordingLiveMemoryOrganizer([])
        organizer.maximum_turn_count = 4
        organizer.maximum_turn_characters = 100
        organizer.maximum_total_characters = 200
        extractor = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=Settings(owner_truth_live_memory_organization_enabled=True),
            organizer=organizer,
        )

        command = extractor.extract(
            intent=self.intent,
            source=OwnerTruthCandidateExtractionInput(
                source_content_hash=_digest(owner_turn),
                source_text=owner_turn,
                source_metadata={
                    "captureMode": "live",
                    "sourcePolicy": "userEvidenceOnly",
                    "conversationTurns": [
                        {
                            "index": 1,
                            "role": "user",
                            "text": owner_turn,
                            "captureMode": "live",
                        }
                    ],
                },
            ),
        )

        sent_owner_text = "".join(
            str(turn["text"])
            for call in organizer.calls
            for turn in call
            if turn["role"] == "user"
        )
        self.assertEqual(sent_owner_text, owner_turn)
        self.assertGreater(len(organizer.calls), 1)
        self.assertTrue(all(len(call) == 1 for call in organizer.calls))
        self.assertEqual(command.proposals, ())

    def test_live_transcript_is_not_sent_when_organization_switch_is_off(self) -> None:
        owner_turn = "我记得外公总会在河边等我。"
        organizer = _RecordingLiveMemoryOrganizer([])
        extractor = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=Settings(owner_truth_live_memory_organization_enabled=False),
            organizer=organizer,
        )
        source = OwnerTruthCandidateExtractionInput(
            source_content_hash=_digest(owner_turn),
            source_text=owner_turn,
            source_metadata={
                "captureMode": "live",
                "sourcePolicy": "userEvidenceOnly",
                "conversationTurns": [
                    {
                        "index": 1,
                        "role": "user",
                        "text": owner_turn,
                        "captureMode": "live",
                    }
                ],
            },
        )

        with self.assertRaises(RuntimeError):
            extractor.extract(intent=self.intent, source=source)

        self.assertIsNone(organizer.turns)

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

    def test_adapter_failure_at_default_attempt_limit_persists_failed_extraction_and_dead_letter(self) -> None:
        result = self._worker(extractor=_FailingExtractor()).run_once()

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "candidateExtractionRetriesExhausted")
        self.assertEqual(result["extractionStatus"], "failed")
        self.assertEqual(result["candidateCount"], 0)
        self.assertEqual(result["jobState"], "failed")
        self.assertEqual(result["consumerOutcome"], "accepted")
        self.assertEqual(result["businessOutcome"], "failed")
        self.assertEqual(result["deadLetterOutcome"], "admitted")
        self.assertEqual(result["deadLetterCause"], "maxAttemptsExceeded")
        self.assertEqual(result["deadLetterState"], "open")
        self.assertEqual(result["deadLetterNextAction"], "authorizedReplayRequired")
        self.assertEqual(
            self.store.lease_repository.attempt_state(self.intent.job_id, 1),
            "terminalFailed",
        )
        snapshot = self.store.candidate_repository.snapshot()
        self.assertEqual(len(snapshot["extractions"]), 1)
        self.assertEqual(snapshot["candidates"], {})
        self.assertEqual(len(self.store.consumer_repository._inbox), 1)
        admission = self.store.dead_letter_repository.load(result["deadLetterId"])
        self.assertEqual(admission.intent, self.intent)
        self.assertEqual(admission.attempt, 1)
        self.assertEqual(admission.cause.value, "maxAttemptsExceeded")

    def test_adapter_failure_retries_until_the_explicit_attempt_limit(self) -> None:
        intent = replace(self.intent, max_attempts=3)
        store = self._new_store()
        store.lease_repository.seed(intent)
        worker = self._worker(
            store=store,
            extractor=_FailingExtractor(),
            retry_seconds=1,
        )

        first = worker.run_once()
        sleep(1.05)
        second = worker.run_once()
        sleep(1.05)
        third = worker.run_once()

        self.assertEqual([first["status"], second["status"], third["status"]], ["retryWait", "retryWait", "failed"])
        self.assertEqual(third["reason"], "candidateExtractionRetriesExhausted")
        self.assertEqual(third["attempt"], 3)
        self.assertEqual(third["deadLetterCause"], "maxAttemptsExceeded")
        self.assertEqual(store.lease_repository.attempt_state(intent.job_id, 1), "retryableFailed")
        self.assertEqual(store.lease_repository.attempt_state(intent.job_id, 2), "retryableFailed")
        self.assertEqual(store.lease_repository.attempt_state(intent.job_id, 3), "terminalFailed")
        self.assertEqual(store.dead_letter_repository.record_count(), 1)

    def test_slow_extractor_heartbeats_lease_and_blocks_second_worker(self) -> None:
        started = Event()
        release = Event()
        first_worker = self._worker(
            extractor=_BlockingExtractor(started=started, release=release),
            worker_id="candidate-extraction-first-worker",
            lease_seconds=1,
            heartbeat_interval_seconds=0.02,
        )
        first_result: dict[str, object] = {}
        first_thread = Thread(
            target=lambda: first_result.update(first_worker.run_once()),
            name="candidate-extraction-first-worker-test",
        )
        first_thread.start()
        self.assertTrue(started.wait(timeout=1.0))

        # The initial one-second lease has elapsed, but the independent
        # heartbeat prevents a competing worker from claiming the same job.
        sleep(1.1)
        contender = self._worker(
            worker_id="candidate-extraction-contender",
            lease_seconds=1,
        ).run_once()
        self.assertEqual(contender["status"], "idle")

        release.set()
        first_thread.join(timeout=3.0)
        self.assertFalse(first_thread.is_alive())
        self.assertEqual(first_result["status"], "completed")
        self.assertEqual(self.store.lease_repository.attempt_state(self.intent.job_id, 1), "succeeded")
        self.assertEqual(len(self.store.candidate_repository.snapshot()["candidates"]), 1)

    def test_lease_heartbeat_failure_discards_extraction_and_consumer_receipt(self) -> None:
        started = Event()
        release = Event()
        heartbeat_attempted = Event()
        original_heartbeat = self.store.lease_repository.heartbeat

        def fail_heartbeat(*_args, **_kwargs):
            heartbeat_attempted.set()
            raise RuntimeError("candidate extraction heartbeat test failure")

        self.store.lease_repository.heartbeat = fail_heartbeat
        worker = self._worker(
            extractor=_BlockingExtractor(started=started, release=release),
            lease_seconds=1,
            heartbeat_interval_seconds=0.01,
        )
        result: dict[str, object] = {}
        thread = Thread(
            target=lambda: result.update(worker.run_once()),
            name="candidate-extraction-heartbeat-failure-test",
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
        self.assertEqual(result["reason"], "candidateExtractionLeaseLost")
        self.assertNotEqual(self.store.lease_repository.attempt_state(self.intent.job_id, 1), "succeeded")
        self.assertEqual(self.store.candidate_repository.snapshot()["extractions"], {})
        self.assertEqual(self.store.candidate_repository.snapshot()["candidates"], {})
        self.assertEqual(self.store.consumer_repository._inbox, {})

    def test_lease_heartbeat_uses_bounded_third_by_default_and_allows_test_injection(self) -> None:
        self.assertAlmostEqual(self._worker(lease_seconds=3)._heartbeat_interval_seconds, 1.0)
        self.assertAlmostEqual(self._worker(lease_seconds=180)._heartbeat_interval_seconds, 30.0)
        self.assertAlmostEqual(
            self._worker(lease_seconds=1, heartbeat_interval_seconds=0.02)._heartbeat_interval_seconds,
            0.02,
        )

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
