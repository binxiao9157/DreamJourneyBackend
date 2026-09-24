from __future__ import annotations

from contextlib import contextmanager, redirect_stderr
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import json
import io
from threading import Event, Thread
from time import sleep
import unittest
from uuid import uuid4

import httpx

from app.async_effects.business_message_projection_request_repository import (
    InMemoryBusinessMessageProjectionRequestRepository,
)
from app.async_effects.consumer_repository import InMemoryAsyncEffectConsumerRepository
from app.async_effects.dead_letter_repository import InMemoryAsyncEffectDeadLetterRepository
from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectTarget
from app.async_effects.lease_repository import InMemoryAsyncEffectLeaseRepository
from app.async_effects.legacy_identity_inbox_bridge import (
    InMemoryLegacyInboxAccountResolver,
    LegacyAliasClaimState,
    LegacyInboxAccountBinding,
)
from app.async_effects.owner_truth_candidate_extraction_worker import (
    DeterministicOwnerTruthCandidateExtractor,
    ModelAssistedOwnerTruthLiveConversationExtractor,
    ModelAssistedOwnerTruthSourceExtractor,
    OwnerTruthCandidateExtractionWorkerRuntime,
    _log_stage_diagnostic,
    _worker_result_dedupe_key,
)
from app.async_effects.target_admission import InMemoryOwnerTruthSourceTargetAdmissionRepository
from app.async_effects.repository import InMemoryEffectKernelRepository
from app.core.config import Settings
from app.domain.owner_truth.candidate_decisions import (
    CandidateReviewAction,
    OwnerTruthCandidateReviewCommand,
    OwnerTruthCandidateSnapshot,
)
from app.domain.owner_truth.contracts import (
    CandidateDecision,
    EpistemicStatus,
    MemoryKind,
    PerspectiveType,
    SensitivityLevel,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.deepseek import (
    DeepSeekLiveMemoryOrganizationProxy,
    DeepSeekTextMemoryOrganizationProxy,
)
from app.services.owner_truth_candidate_extraction import (
    InMemoryOwnerTruthCandidateExtractionRepository,
    OwnerTruthCandidateExtractionInput,
    PostgresOwnerTruthCandidateExtractionInputRepository,
)
from app.services.owner_truth_live_memory_contract_errors import (
    LiveMemoryContractFailure,
)
from app.services.owner_truth_candidate_review import (
    InMemoryOwnerTruthCandidateReviewRepository,
    OwnerTruthCandidateReviewService,
)
from app.services.owner_truth_formal_memory import (
    InMemoryOwnerTruthFormalMemoryRepository,
    OwnerTruthFormalMemoryQuery,
    OwnerTruthFormalMemoryService,
)


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _canonical_digest(value: dict[str, object]) -> str:
    return _digest(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")))


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
        self.message_effect_repository = InMemoryEffectKernelRepository()
        self.message_input_repository = InMemoryBusinessMessageProjectionRequestRepository()
        self.business_message_projection_enabled = False
        self.message_inbox_resolver = InMemoryLegacyInboxAccountResolver(
            [
                LegacyInboxAccountBinding(
                    legacy_user_id="legacy-candidate-worker",
                    legacy_alias_hash=_digest("legacy-candidate-worker"),
                    subject_id=owner_subject_id,
                    vault_id=vault_id,
                    claim_state=LegacyAliasClaimState.VERIFIED,
                    identity_proof_subject_id=owner_subject_id,
                    subject_state="active",
                    vault_owner_subject_id=owner_subject_id,
                    vault_state="active",
                    account_access_state="active",
                    account_deletion_state="active",
                    account_auth_epoch=7,
                    bridge_row_version=1,
                )
            ]
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

    def effect_kernel_repository(self):
        return self.message_effect_repository

    def async_effect_business_message_projection_request_repository(self):
        return self.message_input_repository

    def async_effect_legacy_inbox_account_resolver(self):
        return self.message_inbox_resolver


class _ReviewStore:
    def __init__(
        self,
        repository: InMemoryOwnerTruthCandidateReviewRepository | None = None,
    ) -> None:
        self.repository = repository or InMemoryOwnerTruthCandidateReviewRepository()
        self.formal_repository = InMemoryOwnerTruthFormalMemoryRepository(self.repository)

    @contextmanager
    def request_unit_of_work(self, **_kwargs):
        yield self

    def owner_truth_candidate_review_repository(self):
        return self.repository

    def owner_truth_formal_memory_repository(self):
        return self.formal_repository


class _FailingExtractor:
    def extract(self, **_kwargs):
        raise RuntimeError("deterministic extractor fixture failure")


class _TransientFailingExtractor:
    def extract(self, **_kwargs):
        request = httpx.Request("POST", "https://provider.invalid/chat/completions")
        raise httpx.ConnectError("provider unavailable", request=request)


class _FailingInboxResolver:
    def resolve_active(self, *_args, **_kwargs):
        raise RuntimeError("owner inbox fixture unavailable")


class _RecordingLiveMemoryOrganizer:
    model = "deepseek-live-memory-test"
    prompt_version = "owner-truth-live-memory-organization-test-v1"
    support_prompt_version = "owner-truth-live-memory-support-test-v1"

    def __init__(
        self,
        memories: list[dict[str, object]],
        *,
        support_review: dict[str, object] | None = None,
    ) -> None:
        self.memories = memories
        self.support_review = support_review
        self.turns: list[dict[str, object]] | None = None
        self.calls: list[list[dict[str, object]]] = []
        self.support_calls: list[dict[str, object]] = []

    def request_organization(self, *, turns):
        self.turns = list(turns)
        self.calls.append(list(turns))
        return {"memories": self.memories}

    def request_support_review(self, *, turns, memories):
        self.support_calls.append({"turns": list(turns), "memories": list(memories)})
        if self.support_review is not None:
            return self.support_review
        supported_turns = {
            index
            for memory in memories
            for index in memory.get("sourceTurnIndices", [])
        }
        return {
            "schemaVersion": "owner-truth-live-memory-support-v1",
            "turnAssessments": [
                {
                    "turnIndex": turn["index"],
                    "speechAct": "assertion" if turn["index"] in supported_turns else "query",
                }
                for turn in turns
                if turn["role"] == "user"
            ],
            "memoryAssessments": [
                {
                    "memoryIndex": index,
                    "verdict": "supported",
                    "supportingTurnIndices": list(memory["sourceTurnIndices"]),
                }
                for index, memory in enumerate(memories)
            ],
            "omittedFactBearingTurnIndices": [],
        }


class _SequencedLiveMemoryOrganizer(_RecordingLiveMemoryOrganizer):
    def __init__(self, responses, *, support_review):
        super().__init__([], support_review=support_review)
        self.responses = list(responses)

    def request_organization(self, *, turns):
        self.turns = list(turns)
        self.calls.append(list(turns))
        response_index = len(self.calls) - 1
        if response_index >= len(self.responses):
            raise AssertionError("unexpected extra live organization chunk")
        return {"memories": self.responses[response_index]}


class _UnavailableLiveMemoryOrganizer:
    model = "deepseek-live-memory-test"
    prompt_version = "owner-truth-live-memory-organization-test-v1"
    support_prompt_version = "owner-truth-live-memory-support-test-v1"

    def request_organization(self, *, turns):
        request = httpx.Request("POST", "https://provider.invalid/chat/completions")
        raise httpx.ConnectError("provider unavailable", request=request)

    def request_support_review(self, *, turns, memories):
        raise AssertionError("support review must not run after organization transport failure")


class _InvalidLiveMemoryOrganizer:
    model = "deepseek-live-memory-test"
    prompt_version = "owner-truth-live-memory-organization-test-v1"
    support_prompt_version = "owner-truth-live-memory-support-test-v1"

    def request_organization(self, *, turns):
        raise ValueError("DeepSeek returned empty content")

    def request_support_review(self, *, turns, memories):
        raise AssertionError("support review must not run after invalid organization")


class _RecordingTextMemoryOrganizer:
    model = "deepseek-text-memory-test"
    prompt_version = "owner-truth-text-memory-organization-test-v1"

    def __init__(self, memories: list[dict[str, object]]) -> None:
        self.memories = memories
        self.text: str | None = None

    def request_organization(self, *, text: str):
        self.text = text
        return {"memories": self.memories}


class _RecordingFamilyScopedTextMemoryOrganizer(_RecordingTextMemoryOrganizer):
    def __init__(self, memories: list[dict[str, object]]) -> None:
        super().__init__(memories)
        self.family_text: str | None = None

    def request_family_organization(self, *, text: str):
        self.family_text = text
        return {"memories": self.memories}


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
        stage_diagnostic_recorder=None,
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
            stage_diagnostic_recorder=stage_diagnostic_recorder,
        )

    def test_loop_dedupes_only_jobless_idle_or_blocked_heartbeats(self) -> None:
        idle = {"status": "idle", "reason": "noEligibleCandidateExtractionJob"}
        blocked = {"status": "blocked", "reason": "workerDisabled"}
        first_job = {
            "status": "failed",
            "reason": "candidateExtractionRetriesExhausted",
            "jobId": "job-a",
            "attempt": 1,
        }
        second_job = {**first_job, "jobId": "job-b"}
        second_attempt = {**first_job, "attempt": 2}

        self.assertEqual(
            _worker_result_dedupe_key(idle),
            ("idle", "noEligibleCandidateExtractionJob"),
        )
        self.assertEqual(
            _worker_result_dedupe_key(blocked),
            ("blocked", "workerDisabled"),
        )
        self.assertIsNone(_worker_result_dedupe_key(first_job))
        self.assertIsNone(_worker_result_dedupe_key(second_job))
        self.assertIsNone(_worker_result_dedupe_key(second_attempt))

    def test_default_live_chain_records_actual_safe_stages_per_job(self) -> None:
        fact = "本次逐任务诊断测试代号是清风十六号。"
        source_id = str(uuid4())
        intent = replace(
            self.intent,
            target=replace(self.intent.target, resource_id=source_id),
            max_attempts=1,
        )
        store = _Store(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            source_id=source_id,
            source_content_hash=_digest(fact),
            source_text=fact,
            source_metadata={
                "captureMode": "live",
                "sourcePolicy": "userEvidenceOnly",
                "conversationTurns": [
                    {"index": 1, "role": "user", "text": fact, "captureMode": "live"},
                ],
            },
        )
        store.lease_repository.seed(intent)
        organization = {
            "memories": [{
                "memoryKind": "knowledge",
                "claim": "本次逐任务诊断测试代号是清风十六号。",
                "sourceTurnIndices": [1],
                "facets": _facets(),
            }]
        }
        support = {
            "schemaVersion": "owner-truth-live-memory-support-v1",
            "turnAssessments": [{"turnIndex": 1, "speechAct": "assertion"}],
            "memoryAssessments": [{
                "memoryIndex": 0,
                "verdict": "supported",
                "supportingTurnIndices": [1],
            }],
            "omittedFactBearingTurnIndices": [],
        }
        contents = iter([organization, support])

        def handle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                request=request,
                json={
                    "choices": [{
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(next(contents), ensure_ascii=False),
                        },
                    }],
                },
            )

        settings = Settings(
            deepseek_api_key="synthetic-test-key",
            owner_truth_live_memory_organization_enabled=True,
        )
        events: list[dict[str, object]] = []
        extractor = ModelAssistedOwnerTruthSourceExtractor(
            settings=settings,
            live_extractor=ModelAssistedOwnerTruthLiveConversationExtractor(
                settings=settings,
                organizer=DeepSeekLiveMemoryOrganizationProxy(
                    settings,
                    transport=httpx.MockTransport(handle),
                ),
            ),
        )

        result = self._worker(
            store=store,
            extractor=extractor,
            stage_diagnostic_recorder=lambda event: events.append(dict(event)),
        ).run_once()

        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(
            [event["stage"] for event in events],
            [
                "organizationInputBuilt",
                "organizationRequestStarted",
                "organizationResponseReceived",
                "organizationDecoded",
                "organizationValidated",
                "supportInputBuilt",
                "supportRequestStarted",
                "supportResponseReceived",
                "supportDecoded",
                "supportValidated",
                "proposalBuildStarted",
                "proposalBuildCompleted",
                "candidateCommitStarted",
                "candidateCommitSucceeded",
            ],
        )
        self.assertTrue(all(event["attempt"] == 1 for event in events))
        self.assertEqual(len({event["correlation"] for event in events}), 1)
        serialized = json.dumps(events, ensure_ascii=False, sort_keys=True)
        self.assertNotIn(fact, serialized)
        self.assertNotIn(source_id, serialized)
        self.assertNotIn(intent.job_id, serialized)

    def test_default_stage_sink_emits_safe_cli_diagnostic_without_logger_setup(self) -> None:
        output = io.StringIO()
        event = {
            "stage": "supportDecoded",
            "attempt": 2,
            "correlation": "diagnostic-correlation",
            "counts": {"memoryCount": 1},
        }

        with redirect_stderr(output):
            _log_stage_diagnostic(event)

        line = output.getvalue().strip()
        self.assertTrue(line)
        decoded = json.loads(line)
        self.assertEqual(decoded["event"], "ownerTruthCandidateExtractionStage")
        self.assertEqual(decoded["stage"], "supportDecoded")
        self.assertEqual(decoded["attempt"], 2)
        self.assertNotIn("content", decoded)

    def test_support_validated_stage_is_not_emitted_before_semantic_validation(self) -> None:
        fact = "本次语义阶段测试代号是清风十七号。"
        source_id = str(uuid4())
        intent = replace(
            self.intent,
            target=replace(self.intent.target, resource_id=source_id),
            max_attempts=1,
        )
        store = _Store(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            source_id=source_id,
            source_content_hash=_digest(fact),
            source_text=fact,
            source_metadata={
                "captureMode": "live",
                "sourcePolicy": "userEvidenceOnly",
                "conversationTurns": [
                    {"index": 1, "role": "user", "text": fact, "captureMode": "live"},
                ],
            },
        )
        store.lease_repository.seed(intent)
        contents = iter([
            {
                "memories": [{
                    "memoryKind": "knowledge",
                    "claim": fact,
                    "sourceTurnIndices": [1],
                    "facets": _facets(),
                }],
            },
            {
                "schemaVersion": "owner-truth-live-memory-support-v1",
                "turnAssessments": [{"turnIndex": 1, "speechAct": "assertion"}],
                "memoryAssessments": [{
                    "memoryIndex": 0,
                    "verdict": "uncertain",
                    "supportingTurnIndices": [1],
                }],
                "omittedFactBearingTurnIndices": [],
            },
        ])

        def handle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                request=request,
                json={
                    "choices": [{
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(next(contents), ensure_ascii=False),
                        },
                    }],
                },
            )

        settings = Settings(
            deepseek_api_key="synthetic-test-key",
            owner_truth_live_memory_organization_enabled=True,
        )
        events: list[dict[str, object]] = []
        extractor = ModelAssistedOwnerTruthSourceExtractor(
            settings=settings,
            live_extractor=ModelAssistedOwnerTruthLiveConversationExtractor(
                settings=settings,
                organizer=DeepSeekLiveMemoryOrganizationProxy(
                    settings,
                    transport=httpx.MockTransport(handle),
                ),
            ),
        )

        result = self._worker(
            store=store,
            extractor=extractor,
            stage_diagnostic_recorder=lambda event: events.append(dict(event)),
        ).run_once()

        self.assertEqual(result["status"], "failed", result)
        stages = [event["stage"] for event in events]
        self.assertIn("supportDecoded", stages)
        self.assertNotIn("supportValidated", stages)

    def test_stage_diagnostic_failure_does_not_change_live_result(self) -> None:
        def fail_diagnostic(_event) -> None:
            raise RuntimeError("controlled diagnostic sink failure")

        result = self._worker(
            stage_diagnostic_recorder=fail_diagnostic,
        ).run_once()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["candidateCount"], 1)

    def _run_default_live_http_contract_case(
        self,
        *,
        response_envelopes: list[object],
    ) -> tuple[dict[str, object], int, _Store]:
        fact = "本次合同边界测试代号是清泉十二号。"
        source_id = str(uuid4())
        intent = replace(
            self.intent,
            target=replace(self.intent.target, resource_id=source_id),
            max_attempts=1,
        )
        store = _Store(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            source_id=source_id,
            source_content_hash=_digest(fact),
            source_text=fact,
            source_metadata={
                "captureMode": "live",
                "sourcePolicy": "userEvidenceOnly",
                "conversationTurns": [
                    {
                        "index": 1,
                        "role": "user",
                        "text": fact,
                        "captureMode": "live",
                    }
                ],
            },
        )
        store.lease_repository.seed(intent)
        envelopes = iter(response_envelopes)
        request_count = 0

        def handle(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(200, request=request, json=next(envelopes))

        settings = Settings(
            deepseek_api_key="synthetic-test-key",
            owner_truth_live_memory_organization_enabled=True,
        )
        live_extractor = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=settings,
            organizer=DeepSeekLiveMemoryOrganizationProxy(
                settings,
                transport=httpx.MockTransport(handle),
            ),
        )
        result = self._worker(
            store=store,
            extractor=ModelAssistedOwnerTruthSourceExtractor(
                settings=settings,
                live_extractor=live_extractor,
            ),
        ).run_once()
        return result, request_count, store

    def _review_snapshots_from_extraction(
        self,
        store: _Store,
    ) -> tuple[OwnerTruthCandidateSnapshot, ...]:
        snapshot = store.candidate_repository.snapshot()
        extraction = next(iter(snapshot["extractions"].values()))
        policy_version = str(extraction["payload"]["policyVersion"])
        values = []
        for candidate_id, stored in snapshot["candidates"].items():
            payload = stored["payload"]
            values.append(
                OwnerTruthCandidateSnapshot(
                    candidate_id=candidate_id,
                    vault_id=self.vault_id,
                    owner_subject_id=self.owner_subject_id,
                    source_id=stored["sourceId"],
                    memory_kind=MemoryKind(payload["candidateKind"]),
                    perspective_type=PerspectiveType(payload["perspectiveType"]),
                    epistemic_status=EpistemicStatus(payload["epistemicStatus"]),
                    sensitivity=SensitivityLevel(payload["sensitivity"]),
                    decision=CandidateDecision.PENDING,
                    policy_version=policy_version,
                    authority_epoch=7,
                    row_version=1,
                    content_hash=stored["contentHash"],
                    content_schema_version=payload["contentSchemaVersion"],
                    payload=payload,
                )
            )
        return tuple(values)

    def _confirm_and_reopen_formal_memories(
        self,
        candidates: tuple[OwnerTruthCandidateSnapshot, ...],
    ) -> None:
        self.assertGreater(len(candidates), 0)
        repository = InMemoryOwnerTruthCandidateReviewRepository()
        store = _ReviewStore(repository)
        for candidate in candidates:
            repository.seed(candidate)
        context = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            actor_subject_id=self.owner_subject_id,
            policy_version=candidates[0].policy_version,
        )
        commands = []
        service = OwnerTruthCandidateReviewService(store)
        for index, candidate in enumerate(candidates, start=1):
            inbox_item = next(
                item
                for item in repository.list_pending(context=context)
                if item.candidate_id == candidate.candidate_id
            )
            proposal = inbox_item.proposed_change_set
            self.assertIsNotNone(proposal)
            command = OwnerTruthCandidateReviewCommand(
                command_id=f"live-local-confirm-{index}",
                candidate_id=candidate.candidate_id,
                expected_candidate_version=1,
                action=CandidateReviewAction.ACCEPT,
                corrected_value=None,
                corrected_value_schema_version=candidate.content_schema_version,
                reason_code="ownerReviewed",
                expected_memory_revision=int(proposal["baseMemoryRevision"]),
                expected_change_set_id=str(proposal["changeSetId"]),
                expected_proposal_hash=str(proposal["proposalHash"]),
            )
            commands.append(command)
            created = service.decide_and_activate(command=command, context=context)
            self.assertEqual(created.review.outcome, "created")
            self.assertEqual(created.memory_activation.outcome, "created")

        reopened_store = _ReviewStore(repository)
        reopened_review = OwnerTruthCandidateReviewService(reopened_store)
        for command in commands:
            replayed = reopened_review.decide_and_activate(
                command=command,
                context=context,
            )
            self.assertEqual(replayed.review.outcome, "deduplicated")
            self.assertEqual(replayed.memory_activation.outcome, "deduplicated")

        formal = OwnerTruthFormalMemoryService(reopened_store)
        page = formal.list(context=context, query=OwnerTruthFormalMemoryQuery(limit=20))
        self.assertEqual(len(page.items), len(candidates))
        for item in page.items:
            detail = formal.detail(context=context, memory_id=item.memory_id)
            self.assertEqual(detail.current_version.version_number, 1)
            self.assertEqual(detail.current_version.source_count, 1)
        review_snapshot = repository.snapshot()
        self.assertEqual(len(review_snapshot["memoryActivations"]), len(candidates))

    def _extract_live(self, *, organizer, user_text: str):
        extractor = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=Settings(owner_truth_live_memory_organization_enabled=True),
            organizer=organizer,
        )
        return extractor.extract(
            intent=self.intent,
            source=OwnerTruthCandidateExtractionInput(
                source_content_hash=_digest(user_text),
                source_text=user_text,
                source_metadata={
                    "captureMode": "live",
                    "sourcePolicy": "userEvidenceOnly",
                    "conversationTurns": [
                        {"index": 1, "role": "user", "text": user_text, "captureMode": "live"}
                    ],
                },
            ),
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
        self.store.business_message_projection_enabled = True
        result = self._worker().run_once()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["reason"], "candidateExtractionProposalsPersisted")
        self.assertEqual(result["candidateCount"], 1)
        self.assertEqual(result["extractionStatus"], "succeeded")
        self.assertEqual(result["jobState"], "succeeded")
        self.assertEqual(result["consumerInboxState"], "completed")
        self.assertEqual(result["messageProjectionKind"], "candidateReady")
        self.assertEqual(result["messageProjectionOutcome"], "accepted")
        self.assertEqual(result["messageProjectionInputOutcome"], "recorded")
        self.assertEqual(self.store.message_effect_repository.record_count(), 1)
        self.assertEqual(self.store.message_input_repository.request_count(), 1)
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

    def test_owner_text_organization_creates_typed_v5_candidates(self) -> None:
        organizer = _RecordingTextMemoryOrganizer(
            [
                {
                    "memoryKind": "experience",
                    "content": {
                        "event": "我小时候常和外公在河边散步。",
                        "time": {"start": None, "end": None, "precision": "unknown"},
                        "location": "河边",
                        "participants": ["外公"],
                        "actions": ["散步"],
                        "outcome": None,
                        "facets": _facets(
                            people=[
                                {
                                    "value": "外公",
                                    "evidenceMode": "ownerStated",
                                    "confidence": 1.0,
                                }
                            ],
                            places=[
                                {
                                    "value": "河边",
                                    "evidenceMode": "ownerStated",
                                    "confidence": 1.0,
                                }
                            ],
                        ),
                    },
                },
                {
                    "memoryKind": "emotion",
                    "content": {
                        "emotion": "怀念",
                        "expression": "我很怀念和外公一起散步的日子。",
                        "trigger": "想起河边散步",
                        "targetPersonaId": None,
                        "time": None,
                        "intensity": 0.8,
                        "facets": _facets(
                            emotions=[
                                {
                                    "value": "怀念",
                                    "evidenceMode": "ownerStated",
                                    "confidence": 1.0,
                                }
                            ]
                        ),
                    },
                },
            ]
        )
        extractor = ModelAssistedOwnerTruthSourceExtractor(
            settings=Settings(owner_truth_text_memory_organization_enabled=True),
            organizer=organizer,
        )

        command = extractor.extract(
            intent=self.intent,
            source=OwnerTruthCandidateExtractionInput(
                source_content_hash=self.source_content_hash,
                source_text=self.source_text,
                source_metadata={"origin": "memoryArchiveTextCapture"},
            ),
        )

        self.assertEqual(organizer.text, self.source_text)
        self.assertEqual(command.extractor_id, "deepSeekTextMemoryOrganizer")
        self.assertEqual(
            [item.memory_kind.value for item in command.proposals],
            ["experience", "emotion"],
        )
        self.assertTrue(
            all(item.payload_schema_version == "owner-truth-v5" for item in command.proposals)
        )
        self.assertEqual(command.proposals[0].content["event"], "我小时候常和外公在河边散步。")
        self.assertEqual(command.proposals[1].content["emotion"], "怀念")
        self.assertIn("lifeEvent", command.proposals[0].content["semantic"]["facets"])
        self.assertIn("emotion", command.proposals[1].content["semantic"]["facets"])
        self.assertEqual(command.proposals[0].content["provenance"]["mode"], "selfReport")
        self.assertEqual(command.proposals[1].content["factType"], "affect")

    def test_family_text_organization_preserves_server_provenance(self) -> None:
        organizer = _RecordingFamilyScopedTextMemoryOrganizer(
            [
                {
                    "memoryKind": "experience",
                    "subjectRole": "memorySubject",
                    "content": {
                        "event": "父亲以前在杭州时很喜欢吃东坡肉。",
                        "time": {"start": None, "end": None, "precision": "unknown"},
                        "location": "杭州",
                        "participants": ["父亲"],
                        "actions": [],
                        "outcome": None,
                        "facets": _facets(),
                    },
                }
            ]
        )
        extractor = ModelAssistedOwnerTruthSourceExtractor(
            settings=Settings(owner_truth_text_memory_organization_enabled=True),
            organizer=organizer,
        )

        command = extractor.extract(
            intent=self.intent,
            source=OwnerTruthCandidateExtractionInput(
                source_content_hash=self.source_content_hash,
                source_text="父亲以前在杭州时很喜欢吃东坡肉。",
                source_metadata={
                    "origin": "familyContributionReview",
                    "perspectiveType": "familyReport",
                    "epistemicStatus": "reported",
                    "familyContributionGrantId": "grant-1",
                },
            ),
        )

        self.assertEqual(command.proposals[0].perspective_type.value, "reported")
        self.assertEqual(command.proposals[0].epistemic_status.value, "reported")
        self.assertEqual(command.proposals[0].content["provenance"]["mode"], "familyReport")

    def test_family_text_organization_fails_closed_without_subject_role_support(self) -> None:
        extractor = ModelAssistedOwnerTruthSourceExtractor(
            settings=Settings(owner_truth_text_memory_organization_enabled=True),
            organizer=_RecordingTextMemoryOrganizer([]),
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "requires subject-role classification",
        ):
            extractor.extract(
                intent=self.intent,
                source=OwnerTruthCandidateExtractionInput(
                    source_content_hash=self.source_content_hash,
                    source_text="父亲以前在杭州工作，我听完后现在很难过。",
                    source_metadata={
                        "origin": "familyContributionReview",
                        "perspectiveType": "familyReport",
                        "epistemicStatus": "reported",
                    },
                ),
            )

    def test_family_text_organization_excludes_reporter_self_memories(self) -> None:
        organizer = _RecordingFamilyScopedTextMemoryOrganizer(
            [
                {
                    "memoryKind": "experience",
                    "subjectRole": "memorySubject",
                    "content": {
                        "event": "父亲以前在杭州时很喜欢吃东坡肉。",
                        "time": {"start": None, "end": None, "precision": "unknown"},
                        "location": "杭州",
                        "participants": ["父亲"],
                        "actions": [],
                        "outcome": None,
                        "facets": _facets(),
                    },
                },
                {
                    "memoryKind": "emotion",
                    "subjectRole": "reporterSelf",
                    "content": {
                        "emotion": "难过",
                        "expression": "女儿现在很难过。",
                        "trigger": None,
                        "targetPersonaId": None,
                        "time": None,
                        "intensity": None,
                        "facets": _facets(),
                    },
                },
            ]
        )
        extractor = ModelAssistedOwnerTruthSourceExtractor(
            settings=Settings(owner_truth_text_memory_organization_enabled=True),
            organizer=organizer,
        )

        command = extractor.extract(
            intent=self.intent,
            source=OwnerTruthCandidateExtractionInput(
                source_content_hash=self.source_content_hash,
                source_text="父亲以前在杭州时很喜欢吃东坡肉，我听完后现在很难过。",
                source_metadata={
                    "origin": "familyContributionReview",
                    "perspectiveType": "familyReport",
                    "epistemicStatus": "reported",
                    "memorySubjectId": "person-father",
                    "claimSubjectId": "person-father",
                    "speakerPersonId": "person-daughter",
                    "contributorAccountId": "account-daughter",
                },
            ),
        )

        self.assertIsNone(organizer.text)
        self.assertIsNotNone(organizer.family_text)
        self.assertEqual(len(command.proposals), 1)
        proposal = command.proposals[0]
        self.assertEqual(proposal.content["memorySubjectId"], "person-father")
        self.assertEqual(proposal.content["claimSubjectId"], "person-father")
        self.assertEqual(proposal.content["provenance"]["speakerPersonId"], "person-daughter")
        self.assertNotIn("难过", json.dumps(proposal.content, ensure_ascii=False))

    def test_family_text_provider_contract_requires_explicit_subject_roles(self) -> None:
        proxy = DeepSeekTextMemoryOrganizationProxy(
            Settings(deepseek_api_key="test-only-placeholder")
        )
        request = proxy.build_request(
            text="父亲以前在杭州工作，我听完后现在很难过。",
            extraction_scope="familyContribution",
        )
        prompt = request["json"]["messages"][1]["content"]
        self.assertIn("subjectRole", prompt)
        self.assertIn("reporterSelf", prompt)
        self.assertIn("不得混入档案本人候选", prompt)

        with self.assertRaisesRegex(ValueError, "invalid subject role"):
            proxy.parse_organization(
                json.dumps(
                    {
                        "memories": [
                            {
                                "memoryKind": "experience",
                                "content": {
                                    "event": "父亲以前在杭州工作。",
                                    "time": {
                                        "start": None,
                                        "end": None,
                                        "precision": "unknown",
                                    },
                                    "facets": _facets(),
                                },
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                require_subject_role=True,
            )

    def test_document_processing_uses_text_organizer_not_live_fallback(self) -> None:
        organizer = _RecordingTextMemoryOrganizer(
            [
                {
                    "memoryKind": "knowledge",
                    "content": {
                        "claim": "我在北京大学完成了计算机专业学习。",
                        "facets": _facets(places=[{"value": "北京大学", "evidenceMode": "ownerStated", "confidence": 1.0}]),
                    },
                }
            ]
        )
        extractor = ModelAssistedOwnerTruthSourceExtractor(
            settings=Settings(owner_truth_text_memory_organization_enabled=True),
            organizer=organizer,
        )

        command = extractor.extract(
            intent=self.intent,
            source=OwnerTruthCandidateExtractionInput(
                source_content_hash=self.source_content_hash,
                source_text="我在北京大学完成了计算机专业学习。",
                source_metadata={
                    "origin": "mediaSourceObjectProcessing",
                    "mediaKind": "document",
                },
            ),
        )

        self.assertEqual(organizer.text, "我在北京大学完成了计算机专业学习。")
        self.assertEqual(command.extractor_id, "deepSeekTextMemoryOrganizer")
        self.assertEqual(command.proposals[0].memory_kind.value, "knowledge")

    def test_owner_text_organization_switch_off_keeps_typed_fallback(self) -> None:
        organizer = _RecordingTextMemoryOrganizer([])
        extractor = ModelAssistedOwnerTruthSourceExtractor(
            settings=Settings(owner_truth_text_memory_organization_enabled=False),
            organizer=organizer,
        )

        command = extractor.extract(
            intent=self.intent,
            source=OwnerTruthCandidateExtractionInput(
                source_content_hash=self.source_content_hash,
                source_text=self.source_text,
                source_metadata={},
            ),
        )

        self.assertIsNone(organizer.text)
        self.assertEqual(command.extractor_id, "deterministicSourceEcho")
        self.assertEqual(command.proposals[0].content["summary"], self.source_text)
        self.assertEqual(command.proposals[0].payload_schema_version, "owner-truth-v5")
        self.assertEqual(command.proposals[0].content["provenance"]["mode"], "selfReport")

    def test_message_projection_failure_does_not_rollback_pending_candidate(self) -> None:
        self.store.business_message_projection_enabled = True
        self.store.message_inbox_resolver = _FailingInboxResolver()

        result = self._worker().run_once()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["jobState"], "succeeded")
        self.assertEqual(result["candidateCount"], 1)
        self.assertEqual(result["consumerInboxState"], "completed")
        self.assertEqual(result["messageProjectionKind"], "candidateReady")
        self.assertEqual(result["messageProjectionOutcome"], "unavailable")
        self.assertEqual(
            result["messageProjectionFailureReason"],
            "ownerBusinessMessageProjectionUnavailable",
        )
        snapshot = self.store.candidate_repository.snapshot()
        self.assertEqual(len(snapshot["candidates"]), 1)

    def test_trusted_image_understanding_source_creates_inferred_review_candidate(self) -> None:
        facets = _facets(
            people=[
                {
                    "value": "母亲",
                    "evidenceMode": "inferred",
                    "confidence": 0.92,
                }
            ],
            time=[
                {
                    "value": "2025 年春节",
                    "evidenceMode": "inferred",
                    "confidence": 0.8,
                }
            ],
            places=[
                {
                    "value": "杭州西湖",
                    "evidenceMode": "inferred",
                    "confidence": 0.86,
                }
            ],
        )
        source_text = "一家人在杭州西湖合影。\n2025 年春节"
        store = _Store(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            source_id=self.source_id,
            source_content_hash=_digest(source_text),
            source_text=source_text,
            source_metadata={
                "origin": "mediaSourceObjectProcessing",
                "mediaKind": "image",
                "processorId": "httpImageOCR",
                "processorVersion": "v2",
                "candidateFacets": facets,
                "candidateFacetsHash": _canonical_digest({"facets": facets}),
            },
        )
        store.lease_repository.seed(self.intent)

        result = self._worker(store=store).run_once()

        self.assertEqual(result["status"], "completed")
        candidate = next(iter(store.candidate_repository.snapshot()["candidates"].values()))
        payload = candidate["payload"]
        self.assertEqual(candidate["decisionStatus"], "pending")
        self.assertEqual(payload["perspectiveType"], "inferred")
        self.assertEqual(payload["epistemicStatus"], "inferred")
        self.assertEqual(payload["reviewMode"], "single")
        self.assertEqual(payload["content"]["facets"]["people"][0]["value"], "母亲")
        self.assertEqual(payload["content"]["facets"]["places"][0]["value"], "杭州西湖")

    def test_tampered_image_understanding_facets_fail_closed(self) -> None:
        facets = _facets(
            people=[
                {
                    "value": "母亲",
                    "evidenceMode": "inferred",
                    "confidence": 0.92,
                }
            ]
        )
        store = self._new_store(
            source_metadata={
                "origin": "mediaSourceObjectProcessing",
                "mediaKind": "image",
                "processorId": "httpImageOCR",
                "processorVersion": "v2",
                "candidateFacets": facets,
                "candidateFacetsHash": "0" * 64,
            }
        )
        store.lease_repository.seed(self.intent)

        result = self._worker(store=store).run_once()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["reason"], "candidateExtractionQuarantined")
        self.assertEqual(store.candidate_repository.snapshot()["candidates"], {})

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
            all(payload["contentSchemaVersion"] == "owner-truth-v5" for payload in payloads.values())
        )
        self.assertEqual(
            payloads["experience"]["content"]["facets"]["people"][0]["value"],
            "外公",
        )
        self.assertIn("lifeEvent", payloads["experience"]["content"]["semantic"]["facets"])
        self.assertEqual(payloads["experience"]["content"]["provenance"]["mode"], "selfReport")
        self.assertEqual(payloads["emotion"]["content"]["factType"], "affect")
        self.assertTrue(all(payload["reviewMode"] == "single" for payload in payloads.values()))
        self.assertTrue(all(payload["confidence"] == 0.0 for payload in payloads.values()))

    def test_b7_pure_questions_are_rejected_by_semantic_support_before_candidate_builder(self) -> None:
        questions = ["我的测试清单代号是什么？", "这次档案测试的代号是什么？"]
        memories = [
            {
                "memoryKind": "knowledge",
                "claim": "用户问过自己的测试清单代号是什么。",
                "sourceTurnIndices": [1],
                "facets": _facets(),
            },
            {
                "memoryKind": "knowledge",
                "claim": "用户问过这次档案测试的代号是什么。",
                "sourceTurnIndices": [3],
                "facets": _facets(),
            },
        ]
        organizer = _RecordingLiveMemoryOrganizer(
            memories,
            support_review={
                "schemaVersion": "owner-truth-live-memory-support-v1",
                "turnAssessments": [
                    {"turnIndex": 1, "speechAct": "query"},
                    {"turnIndex": 3, "speechAct": "query"},
                ],
                "memoryAssessments": [
                    {"memoryIndex": 0, "verdict": "unsupported", "supportingTurnIndices": []},
                    {"memoryIndex": 1, "verdict": "unsupported", "supportingTurnIndices": []},
                ],
                "omittedFactBearingTurnIndices": [],
            },
        )
        source_text = "\n\n".join(questions)
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
                    {"index": 1, "role": "user", "text": questions[0], "captureMode": "live"},
                    {"index": 2, "role": "assistant", "text": "代号是晨星。", "captureMode": "live"},
                    {"index": 3, "role": "user", "text": questions[1], "captureMode": "live"},
                ],
            },
        )
        store.lease_repository.seed(self.intent)
        extractor = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=Settings(owner_truth_live_memory_organization_enabled=True),
            organizer=organizer,
        )

        result = self._worker(store=store, extractor=extractor).run_once()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["reason"], "candidateExtractionCompletedNoChange")
        self.assertEqual(result["candidateCount"], 0)
        self.assertEqual(store.candidate_repository.snapshot()["candidates"], {})
        self.assertEqual(len(organizer.support_calls), 1)

    def test_b7_support_review_ignores_extra_assistant_turn_assessment(self) -> None:
        user_fact = "我于2016年从晨光大学计算机专业毕业。"
        organizer = _RecordingLiveMemoryOrganizer(
            [{
                "memoryKind": "experience",
                "summary": user_fact,
                "sourceTurnIndices": [2],
                "facets": _facets(),
            }],
            support_review={
                "schemaVersion": "owner-truth-live-memory-support-v1",
                "turnAssessments": [
                    {"turnIndex": 1, "speechAct": "assertion"},
                    {"turnIndex": 2, "speechAct": "assertion"},
                ],
                "memoryAssessments": [{
                    "memoryIndex": 0,
                    "verdict": "supported",
                    "supportingTurnIndices": [2],
                }],
                "omittedFactBearingTurnIndices": [],
            },
        )
        extractor = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=Settings(owner_truth_live_memory_organization_enabled=True),
            organizer=organizer,
        )

        command = extractor.extract(
            intent=self.intent,
            source=OwnerTruthCandidateExtractionInput(
                source_content_hash=_digest(user_fact),
                source_text=user_fact,
                source_metadata={
                    "captureMode": "live",
                    "sourcePolicy": "userEvidenceOnly",
                    "conversationTurns": [
                        {
                            "index": 1,
                            "role": "assistant",
                            "text": "请只讲一条用于隔离验证的合成经历。",
                            "captureMode": "live",
                        },
                        {
                            "index": 2,
                            "role": "user",
                            "text": user_fact,
                            "captureMode": "live",
                        },
                    ],
                },
            ),
        )

        self.assertEqual(len(command.proposals), 1)
        self.assertEqual(command.proposals[0].content["summary"], user_fact)

    def test_b7_assistant_answer_cannot_be_smuggled_through_a_user_query_index(self) -> None:
        query = "我的测试清单代号是什么？"
        organizer = _RecordingLiveMemoryOrganizer(
            [{
                "memoryKind": "knowledge",
                "claim": "测试清单代号是晨星。",
                "sourceTurnIndices": [1],
                "facets": _facets(),
            }],
            support_review={
                "schemaVersion": "owner-truth-live-memory-support-v1",
                "turnAssessments": [{"turnIndex": 1, "speechAct": "query"}],
                "memoryAssessments": [{
                    "memoryIndex": 0,
                    "verdict": "unsupported",
                    "supportingTurnIndices": [],
                }],
                "omittedFactBearingTurnIndices": [],
            },
        )
        extractor = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=Settings(owner_truth_live_memory_organization_enabled=True),
            organizer=organizer,
        )

        command = extractor.extract(
            intent=self.intent,
            source=OwnerTruthCandidateExtractionInput(
                source_content_hash=_digest(query),
                source_text=query,
                source_metadata={
                    "captureMode": "live",
                    "sourcePolicy": "userEvidenceOnly",
                    "conversationTurns": [
                        {"index": 1, "role": "user", "text": query, "captureMode": "live"},
                        {"index": 2, "role": "assistant", "text": "代号是晨星。", "captureMode": "live"},
                    ],
                },
            ),
        )

        self.assertEqual(command.proposals, ())

    def test_b7_whole_session_support_keeps_only_the_final_correction(self) -> None:
        first = "测试清单代号是晚霞。"
        correction = "我更正一下，测试清单代号是晨星。"
        memories = [
            {
                "memoryKind": "knowledge",
                "claim": "测试清单代号是晚霞。",
                "sourceTurnIndices": [1],
                "facets": _facets(),
            },
            {
                "memoryKind": "knowledge",
                "claim": "测试清单代号是晨星。",
                "sourceTurnIndices": [3],
                "facets": _facets(),
            },
        ]
        organizer = _RecordingLiveMemoryOrganizer(
            memories,
            support_review={
                "schemaVersion": "owner-truth-live-memory-support-v1",
                "turnAssessments": [
                    {"turnIndex": 1, "speechAct": "assertion"},
                    {"turnIndex": 3, "speechAct": "correction"},
                ],
                "memoryAssessments": [
                    {"memoryIndex": 0, "verdict": "superseded", "supportingTurnIndices": []},
                    {"memoryIndex": 1, "verdict": "supported", "supportingTurnIndices": [3]},
                ],
                "omittedFactBearingTurnIndices": [],
            },
        )
        extractor = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=Settings(owner_truth_live_memory_organization_enabled=True),
            organizer=organizer,
        )
        source_text = f"{first}\n\n{correction}"

        command = extractor.extract(
            intent=self.intent,
            source=OwnerTruthCandidateExtractionInput(
                source_content_hash=_digest(source_text),
                source_text=source_text,
                source_metadata={
                    "captureMode": "live",
                    "sourcePolicy": "userEvidenceOnly",
                    "conversationTurns": [
                        {"index": 1, "role": "user", "text": first, "captureMode": "live"},
                        {"index": 2, "role": "assistant", "text": "收到。", "captureMode": "live"},
                        {"index": 3, "role": "user", "text": correction, "captureMode": "live"},
                    ],
                },
            ),
        )

        self.assertEqual(len(command.proposals), 1)
        self.assertEqual(command.proposals[0].content["claim"], "测试清单代号是晨星。")
        self.assertEqual(command.proposals[0].evidence_span.start, len(first) + 2)

    def test_b7_empty_organization_cannot_hide_an_omitted_user_fact(self) -> None:
        fact = "我在 2018 年搬到了杭州。"
        organizer = _RecordingLiveMemoryOrganizer(
            [],
            support_review={
                "schemaVersion": "owner-truth-live-memory-support-v1",
                "turnAssessments": [{"turnIndex": 1, "speechAct": "assertion"}],
                "memoryAssessments": [],
                "omittedFactBearingTurnIndices": [1],
            },
        )
        extractor = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=Settings(owner_truth_live_memory_organization_enabled=True),
            organizer=organizer,
        )

        with self.assertRaises(LiveMemoryContractFailure) as raised:
            extractor.extract(
                intent=self.intent,
                source=OwnerTruthCandidateExtractionInput(
                    source_content_hash=_digest(fact),
                    source_text=fact,
                    source_metadata={
                        "captureMode": "live",
                        "sourcePolicy": "userEvidenceOnly",
                        "conversationTurns": [
                            {"index": 1, "role": "user", "text": fact, "captureMode": "live"}
                        ],
                    },
                ),
            )
        self.assertEqual(raised.exception.stage, "supportValidate")
        self.assertEqual(raised.exception.reason, "factOmitted")

    def test_b7_question_suffix_does_not_drop_an_explicit_correction(self) -> None:
        correction = "不是晚霞，测试清单代号是晨星，记住了吗？"
        organizer = _RecordingLiveMemoryOrganizer(
            [{
                "memoryKind": "knowledge",
                "claim": "测试清单代号是晨星。",
                "sourceTurnIndices": [1],
                "facets": _facets(),
            }],
            support_review={
                "schemaVersion": "owner-truth-live-memory-support-v1",
                "turnAssessments": [{"turnIndex": 1, "speechAct": "correction"}],
                "memoryAssessments": [{
                    "memoryIndex": 0,
                    "verdict": "supported",
                    "supportingTurnIndices": [1],
                }],
                "omittedFactBearingTurnIndices": [],
            },
        )
        extractor = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=Settings(owner_truth_live_memory_organization_enabled=True),
            organizer=organizer,
        )

        command = extractor.extract(
            intent=self.intent,
            source=OwnerTruthCandidateExtractionInput(
                source_content_hash=_digest(correction),
                source_text=correction,
                source_metadata={
                    "captureMode": "live",
                    "sourcePolicy": "userEvidenceOnly",
                    "conversationTurns": [
                        {"index": 1, "role": "user", "text": correction, "captureMode": "live"}
                    ],
                },
            ),
        )

        self.assertEqual(len(command.proposals), 1)
        self.assertEqual(command.proposals[0].content["claim"], "测试清单代号是晨星。")

    def test_b7_historical_quoted_question_keeps_only_the_supported_user_event(self) -> None:
        text = "我昨天问老师‘什么时候开学？’，后来去了图书馆。"
        organizer = _RecordingLiveMemoryOrganizer(
            [
                {
                    "memoryKind": "experience",
                    "summary": "我昨天去了图书馆。",
                    "sourceTurnIndices": [1],
                    "facets": _facets(),
                },
                {
                    "memoryKind": "knowledge",
                    "claim": "学校昨天开学。",
                    "sourceTurnIndices": [1],
                    "facets": _facets(),
                },
            ],
            support_review={
                "schemaVersion": "owner-truth-live-memory-support-v1",
                "turnAssessments": [{"turnIndex": 1, "speechAct": "assertion"}],
                "memoryAssessments": [
                    {"memoryIndex": 0, "verdict": "supported", "supportingTurnIndices": [1]},
                    {"memoryIndex": 1, "verdict": "unsupported", "supportingTurnIndices": []},
                ],
                "omittedFactBearingTurnIndices": [],
            },
        )

        command = self._extract_live(organizer=organizer, user_text=text)

        self.assertEqual(len(command.proposals), 1)
        self.assertEqual(command.proposals[0].content["summary"], "我昨天去了图书馆。")

    def test_b7_fact_and_query_in_one_turn_keeps_only_the_supported_fact(self) -> None:
        text = "我 2021 年搬到苏州。能帮我查一下学校吗？"
        organizer = _RecordingLiveMemoryOrganizer(
            [{
                "memoryKind": "experience",
                "summary": "我 2021 年搬到苏州。",
                "sourceTurnIndices": [1],
                "facets": _facets(),
            }],
            support_review={
                "schemaVersion": "owner-truth-live-memory-support-v1",
                "turnAssessments": [{"turnIndex": 1, "speechAct": "assertion"}],
                "memoryAssessments": [{
                    "memoryIndex": 0,
                    "verdict": "supported",
                    "supportingTurnIndices": [1],
                }],
                "omittedFactBearingTurnIndices": [],
            },
        )

        command = self._extract_live(organizer=organizer, user_text=text)

        self.assertEqual(len(command.proposals), 1)
        self.assertEqual(command.proposals[0].content["summary"], "我 2021 年搬到苏州。")

    def test_b7_ambiguous_assent_cannot_adopt_the_assistant_claim(self) -> None:
        text = "对。"
        organizer = _RecordingLiveMemoryOrganizer(
            [{
                "memoryKind": "knowledge",
                "claim": "我目前住在杭州。",
                "sourceTurnIndices": [3],
                "facets": _facets(),
            }],
            support_review={
                "schemaVersion": "owner-truth-live-memory-support-v1",
                "turnAssessments": [
                    {"turnIndex": 1, "speechAct": "query"},
                    {"turnIndex": 3, "speechAct": "ambiguous"},
                ],
                "memoryAssessments": [{
                    "memoryIndex": 0,
                    "verdict": "unsupported",
                    "supportingTurnIndices": [],
                }],
                "omittedFactBearingTurnIndices": [],
            },
        )
        extractor = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=Settings(owner_truth_live_memory_organization_enabled=True),
            organizer=organizer,
        )
        source_text = "我目前住在杭州吗？\n\n对。"

        command = extractor.extract(
            intent=self.intent,
            source=OwnerTruthCandidateExtractionInput(
                source_content_hash=_digest(source_text),
                source_text=source_text,
                source_metadata={
                    "captureMode": "live",
                    "sourcePolicy": "userEvidenceOnly",
                    "conversationTurns": [
                        {"index": 1, "role": "user", "text": "我目前住在杭州吗？", "captureMode": "live"},
                        {"index": 2, "role": "assistant", "text": "你目前住在杭州。", "captureMode": "live"},
                        {"index": 3, "role": "user", "text": text, "captureMode": "live"},
                    ],
                },
            ),
        )

        self.assertEqual(command.proposals, ())

    def test_b7_cross_chunk_correction_is_resolved_against_the_whole_session(self) -> None:
        old = "测试清单代号是晚霞。"
        correction = "我更正一下，测试清单代号是晨星。"
        old_memory = {
            "memoryKind": "knowledge",
            "claim": "测试清单代号是晚霞。",
            "sourceTurnIndices": [1],
            "facets": _facets(),
        }
        corrected_memory = {
            "memoryKind": "knowledge",
            "claim": "测试清单代号是晨星。",
            "sourceTurnIndices": [3],
            "facets": _facets(),
        }
        organizer = _SequencedLiveMemoryOrganizer(
            [[old_memory], [corrected_memory]],
            support_review={
                "schemaVersion": "owner-truth-live-memory-support-v1",
                "turnAssessments": [
                    {"turnIndex": 1, "speechAct": "assertion"},
                    {"turnIndex": 3, "speechAct": "correction"},
                ],
                "memoryAssessments": [
                    {"memoryIndex": 0, "verdict": "superseded", "supportingTurnIndices": []},
                    {"memoryIndex": 1, "verdict": "supported", "supportingTurnIndices": [3]},
                ],
                "omittedFactBearingTurnIndices": [],
            },
        )
        organizer.maximum_turn_count = 2
        extractor = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=Settings(owner_truth_live_memory_organization_enabled=True),
            organizer=organizer,
        )
        source_text = f"{old}\n\n{correction}"

        command = extractor.extract(
            intent=self.intent,
            source=OwnerTruthCandidateExtractionInput(
                source_content_hash=_digest(source_text),
                source_text=source_text,
                source_metadata={
                    "captureMode": "live",
                    "sourcePolicy": "userEvidenceOnly",
                    "conversationTurns": [
                        {"index": 1, "role": "user", "text": old, "captureMode": "live"},
                        {"index": 2, "role": "assistant", "text": "收到。", "captureMode": "live"},
                        {"index": 3, "role": "user", "text": correction, "captureMode": "live"},
                    ],
                },
            ),
        )

        self.assertEqual(len(organizer.calls), 2)
        self.assertEqual(len(command.proposals), 1)
        self.assertEqual(command.proposals[0].content["claim"], "测试清单代号是晨星。")

    def test_b7_uncertain_semantic_review_fails_closed_before_builder(self) -> None:
        query = "难道测试清单代号不是晨星吗？"
        organizer = _RecordingLiveMemoryOrganizer(
            [{
                "memoryKind": "knowledge",
                "claim": "测试清单代号是晨星。",
                "sourceTurnIndices": [1],
                "facets": _facets(),
            }],
            support_review={
                "schemaVersion": "owner-truth-live-memory-support-v1",
                "turnAssessments": [{"turnIndex": 1, "speechAct": "ambiguous"}],
                "memoryAssessments": [{
                    "memoryIndex": 0,
                    "verdict": "uncertain",
                    "supportingTurnIndices": [],
                }],
                "omittedFactBearingTurnIndices": [],
            },
        )
        extractor = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=Settings(owner_truth_live_memory_organization_enabled=True),
            organizer=organizer,
        )

        with self.assertRaises(LiveMemoryContractFailure) as raised:
            extractor.extract(
                intent=self.intent,
                source=OwnerTruthCandidateExtractionInput(
                    source_content_hash=_digest(query),
                    source_text=query,
                    source_metadata={
                        "captureMode": "live",
                        "sourcePolicy": "userEvidenceOnly",
                        "conversationTurns": [
                            {"index": 1, "role": "user", "text": query, "captureMode": "live"}
                        ],
                    },
                ),
            )
        self.assertEqual(raised.exception.stage, "supportValidate")
        self.assertEqual(raised.exception.reason, "semanticUncertain")

    def test_live_organization_accepts_assistant_opening_before_first_user_evidence(self) -> None:
        assistant_turn = "请只讲一条用于隔离验证的合成经历。"
        owner_turn = "我于2016年从晨光大学计算机专业毕业。"
        organizer = _RecordingLiveMemoryOrganizer(
            [
                {
                    "memoryKind": "experience",
                    "summary": owner_turn,
                    "sourceTurnIndices": [2],
                    "facets": _facets(),
                }
            ]
        )
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
                            "role": "assistant",
                            "text": assistant_turn,
                            "captureMode": "live",
                        },
                        {
                            "index": 2,
                            "role": "user",
                            "text": owner_turn,
                            "captureMode": "live",
                        },
                    ],
                },
            ),
        )

        self.assertEqual(len(command.proposals), 1)
        self.assertEqual(
            organizer.calls,
            [[
                {"index": 1, "role": "assistant", "text": assistant_turn},
                {"index": 2, "role": "user", "text": owner_turn},
            ]],
        )

    def test_live_organization_transport_failure_is_not_converted_to_a_candidate(self) -> None:
        owner_turn = "我记得外公总会在河边等我。"
        extractor = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=Settings(owner_truth_live_memory_organization_enabled=True),
            organizer=_UnavailableLiveMemoryOrganizer(),
        )

        with self.assertRaises(httpx.ConnectError):
            extractor.extract(
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

    def test_live_organization_invalid_response_is_not_converted_to_a_candidate(self) -> None:
        owner_turn = "我记得外公总会在河边等我。"
        extractor = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=Settings(owner_truth_live_memory_organization_enabled=True),
            organizer=_InvalidLiveMemoryOrganizer(),
        )

        with self.assertRaises(ValueError):
            extractor.extract(
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

    def test_live_session_with_no_new_fact_completes_without_a_false_review_notification(self) -> None:
        owner_turn = "今天只是聊了些已经确认过的往事，没有新的事实。"
        store = self._new_store(
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
            }
        )
        store.input_repository.source_text = owner_turn
        store.input_repository.source_content_hash = _digest(owner_turn)
        store.business_message_projection_enabled = True
        store.lease_repository.seed(self.intent)
        extractor = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=Settings(owner_truth_live_memory_organization_enabled=True),
            organizer=_RecordingLiveMemoryOrganizer([]),
        )

        result = self._worker(store=store, extractor=extractor).run_once()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["reason"], "candidateExtractionCompletedNoChange")
        self.assertEqual(result["candidateOutcome"], "completedNoChange")
        self.assertEqual(result["candidateCount"], 0)
        self.assertEqual(result["extractionStatus"], "succeeded")
        self.assertNotIn("messageProjectionKind", result)
        self.assertEqual(store.message_effect_repository.record_count(), 0)
        self.assertEqual(store.message_input_repository.request_count(), 0)
        snapshot = store.candidate_repository.snapshot()
        self.assertEqual(len(snapshot["extractions"]), 1)
        self.assertEqual(snapshot["candidates"], {})

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
        self.assertEqual(result["deadLetterCause"], "manualInterventionRequired")
        self.assertEqual(result["failureCode"], "candidateExtraction.runtime.blocked")
        self.assertFalse(result["retryable"])
        self.assertEqual(result["deadLetterState"], "open")
        self.assertEqual(result["deadLetterNextAction"], "manualInterventionRequired")
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
        self.assertEqual(admission.cause.value, "manualInterventionRequired")

    def test_adapter_failure_retries_until_the_explicit_attempt_limit(self) -> None:
        intent = replace(self.intent, max_attempts=3)
        store = self._new_store()
        store.lease_repository.seed(intent)
        worker = self._worker(
            store=store,
            extractor=_TransientFailingExtractor(),
            retry_seconds=1,
        )

        first = worker.run_once()
        sleep(1.05)
        second = worker.run_once()
        sleep(1.05)
        third = worker.run_once()

        self.assertEqual([first["status"], second["status"], third["status"]], ["retryWait", "retryWait", "failed"])
        self.assertEqual(third["reason"], "candidateExtractionRetriesExhausted")
        self.assertEqual(third["failureCode"], "candidateExtraction.providerRequest.transport")
        self.assertEqual(third["attempt"], 3)
        self.assertEqual(third["deadLetterCause"], "maxAttemptsExceeded")
        self.assertEqual(store.lease_repository.attempt_state(intent.job_id, 1), "retryableFailed")
        self.assertEqual(store.lease_repository.attempt_state(intent.job_id, 2), "retryableFailed")
        self.assertEqual(store.lease_repository.attempt_state(intent.job_id, 3), "terminalFailed")
        self.assertEqual(store.dead_letter_repository.record_count(), 1)

    def test_provider_authorization_failure_is_terminal_and_keeps_safe_status(self) -> None:
        request = httpx.Request("POST", "https://provider.invalid/v1/organize")
        response = httpx.Response(401, request=request, text="private provider response")

        class AuthorizationRejectedExtractor:
            def extract(self, *, intent, source):
                raise httpx.HTTPStatusError(
                    "private provider response",
                    request=request,
                    response=response,
                )

        result = self._worker(extractor=AuthorizationRejectedExtractor()).run_once()

        self.assertEqual(result["status"], "failed")
        self.assertEqual(
            result["failureCode"],
            "candidateExtraction.providerAuthorization.rejected",
        )
        self.assertEqual(result["providerStatus"], 401)
        self.assertFalse(result["retryable"])
        self.assertNotIn("private provider response", json.dumps(result, sort_keys=True))
        attempt = self.store.lease_repository._attempts[(self.intent.job_id, 1)]
        self.assertEqual(
            attempt["errorCode"],
            "candidateExtraction.providerAuthorization.rejected",
        )
        self.assertEqual(
            attempt["terminalReasonCode"],
            "candidateExtractionRetriesExhausted",
        )

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

    def test_non_live_source_read_failure_keeps_legacy_classification(self) -> None:
        original_read = self.store.input_repository.read_for_candidate_extraction
        calls = 0

        def fail_read(intent):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ValueError("synthetic ordinary source failure")
            return original_read(intent)

        self.store.input_repository.read_for_candidate_extraction = fail_read

        result = self._worker().run_once()

        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(result["failureStage"], "responseValidation")
        self.assertEqual(
            result["failureCode"],
            "candidateExtraction.responseContract.invalid",
        )

    def test_non_live_candidate_commit_failure_keeps_legacy_classification(self) -> None:
        original_persist = self.store.candidate_repository.persist
        calls = 0

        def fail_persist(record):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ValueError("synthetic ordinary candidate commit failure")
            return original_persist(record)

        self.store.candidate_repository.persist = fail_persist

        result = self._worker().run_once()

        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(result["failureStage"], "responseValidation")
        self.assertEqual(
            result["failureCode"],
            "candidateExtraction.responseContract.invalid",
        )

    def test_live_http_contract_failure_reuses_original_job_and_recovers_once(self) -> None:
        intent = replace(self.intent, max_attempts=3)
        first_user_turn = "我小学时参加过一次校园合唱演出。"
        second_user_turn = "那次演出的测试代号是松塔七号。"
        source_text = f"{first_user_turn}\n\n{second_user_turn}"
        source_metadata = {
            "captureMode": "live",
            "sourcePolicy": "userEvidenceOnly",
            "conversationTurns": [
                {"index": 1, "role": "user", "text": first_user_turn, "captureMode": "live"},
                {"index": 2, "role": "assistant", "text": "我会按原话记录。", "captureMode": "live"},
                {"index": 3, "role": "user", "text": second_user_turn, "captureMode": "live"},
            ],
        }
        store = _Store(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            source_id=self.source_id,
            source_content_hash=_digest(source_text),
            source_text=source_text,
            source_metadata=source_metadata,
        )
        store.lease_repository.seed(intent)
        facets = {
            "people": [], "time": [], "places": [], "relationships": [],
            "emotions": [], "values": [], "personality": [], "habits": [],
            "goals": [], "identity": [], "reflections": [], "confidence": 0.9,
        }
        organization = {
            "memories": [{
                "memoryKind": "experience",
                "summary": "我小学时参加过一次校园合唱演出，测试代号是松塔七号。",
                "sourceTurnIndices": [1, 3],
                "facets": facets,
            }]
        }
        support = {
            "schemaVersion": "owner-truth-live-memory-support-v1",
            "turnAssessments": [
                {"turnIndex": 1, "speechAct": "assertion"},
                {"turnIndex": 3, "speechAct": "assertion"},
            ],
            "memoryAssessments": [{
                "memoryIndex": 0,
                "verdict": "supported",
                "supportingTurnIndices": [1, 3],
            }],
            "omittedFactBearingTurnIndices": [],
        }
        response_contents = iter([
            "not-json",
            json.dumps(organization, ensure_ascii=False),
            json.dumps(support, ensure_ascii=False),
        ])
        request_bodies: list[dict[str, object]] = []

        def handle(request: httpx.Request) -> httpx.Response:
            request_bodies.append(json.loads(request.content.decode("utf-8")))
            return httpx.Response(
                200,
                request=request,
                json={"choices": [{"message": {"content": next(response_contents)}}]},
            )

        settings = Settings(
            deepseek_api_key="synthetic-test-key",
            owner_truth_live_memory_organization_enabled=True,
        )
        transport = httpx.MockTransport(handle)
        extractor = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=settings,
            organizer=DeepSeekLiveMemoryOrganizationProxy(
                settings,
                transport=transport,
            ),
        )
        worker = self._worker(
            store=store,
            extractor=ModelAssistedOwnerTruthSourceExtractor(
                settings=settings,
                live_extractor=extractor,
            ),
            retry_seconds=1,
        )
        first = worker.run_once()
        self.assertEqual(first["status"], "retryWait", first)
        self.assertEqual(first["failureStage"], "organizationDecode")
        self.assertEqual(first["failureCode"], "candidateExtraction.live.organizationDecode.invalidJson")
        self.assertEqual(store.candidate_repository.snapshot()["candidates"], {})

        store.lease_repository._jobs[intent.job_id]["availableAt"] = datetime.now(
            timezone.utc
        )
        second = worker.run_once()

        self.assertEqual(second["status"], "completed")
        self.assertEqual(second["candidateCount"], 1)
        self.assertEqual(second["jobId"], first["jobId"])
        self.assertEqual(second["attempt"], 2)
        self.assertEqual(len(request_bodies), 3)
        first_prompt = request_bodies[0]["messages"][1]["content"]
        retry_prompt = request_bodies[1]["messages"][1]["content"]
        support_prompt = request_bodies[2]["messages"][1]["content"]
        self.assertNotIn("上次安全合同反馈", first_prompt)
        self.assertIn("上次安全合同反馈", retry_prompt)
        self.assertIn("上次安全合同反馈", support_prompt)
        self.assertEqual(len(store.candidate_repository.snapshot()["candidates"]), 1)

    def test_live_contract_repair_is_not_granted_after_attempt_two(self) -> None:
        intent = replace(self.intent, max_attempts=3)
        source_text = "我曾在学校参加合唱演出。"
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
                    {"index": 1, "role": "user", "text": source_text, "captureMode": "live"},
                ],
            },
        )
        store.lease_repository.seed(intent)
        request_count = 0

        def handle(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(
                200,
                request=request,
                json={"choices": [{"message": {"content": "not-json"}}]},
            )

        settings = Settings(
            deepseek_api_key="synthetic-test-key",
            owner_truth_live_memory_organization_enabled=True,
        )
        extractor = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=settings,
            organizer=DeepSeekLiveMemoryOrganizationProxy(
                settings,
                transport=httpx.MockTransport(handle),
            ),
        )
        worker = self._worker(
            store=store,
            extractor=ModelAssistedOwnerTruthSourceExtractor(
                settings=settings,
                live_extractor=extractor,
            ),
            retry_seconds=1,
        )

        first = worker.run_once()
        store.lease_repository._jobs[intent.job_id]["availableAt"] = datetime.now(
            timezone.utc
        )
        second = worker.run_once()

        self.assertEqual(first["status"], "retryWait")
        self.assertEqual(second["status"], "failed")
        self.assertEqual(second["attempt"], 2)
        self.assertEqual(
            second["failureCode"],
            "candidateExtraction.live.organizationDecode.invalidJson",
        )
        self.assertEqual(request_count, 2)
        self.assertEqual(store.candidate_repository.snapshot()["candidates"], {})
        self.assertEqual(
            store.lease_repository.attempt_state(intent.job_id, 2),
            "terminalFailed",
        )

    def test_live_support_contract_failure_regenerates_full_source_once(self) -> None:
        intent = replace(self.intent, max_attempts=3)
        fact = "我在大学时参加过校园广播站，测试代号是青禾八号。"
        metadata = {
            "captureMode": "live",
            "sourcePolicy": "userEvidenceOnly",
            "conversationTurns": [
                {"index": 1, "role": "user", "text": fact, "captureMode": "live"},
            ],
        }
        store = _Store(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            source_id=self.source_id,
            source_content_hash=_digest(fact),
            source_text=fact,
            source_metadata=metadata,
        )
        store.lease_repository.seed(intent)
        organization = {
            "memories": [{
                "memoryKind": "knowledge",
                "claim": "测试代号是青禾八号。",
                "sourceTurnIndices": [1],
                "facets": _facets(),
            }]
        }
        invalid_support = {
            "schemaVersion": "owner-truth-live-memory-support-v1",
            "turnAssessments": [{"turnIndex": 1, "speechAct": "assertion"}],
            "memoryAssessments": [{
                "memoryIndex": 0,
                "verdict": "supported",
                "supportingTurnIndices": [1],
            }],
            "omittedFactBearingTurnIndices": [1],
        }
        valid_support = {
            **invalid_support,
            "omittedFactBearingTurnIndices": [],
        }
        contents = iter([
            json.dumps(organization, ensure_ascii=False),
            json.dumps(invalid_support, ensure_ascii=False),
            json.dumps(organization, ensure_ascii=False),
            json.dumps(valid_support, ensure_ascii=False),
        ])
        prompts: list[str] = []

        def handle(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode("utf-8"))
            prompts.append(body["messages"][1]["content"])
            return httpx.Response(
                200,
                request=request,
                json={"choices": [{"message": {"content": next(contents)}}]},
            )

        settings = Settings(
            deepseek_api_key="synthetic-test-key",
            owner_truth_live_memory_organization_enabled=True,
        )
        extractor = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=settings,
            organizer=DeepSeekLiveMemoryOrganizationProxy(
                settings,
                transport=httpx.MockTransport(handle),
            ),
        )
        worker = self._worker(
            store=store,
            extractor=ModelAssistedOwnerTruthSourceExtractor(
                settings=settings,
                live_extractor=extractor,
            ),
            retry_seconds=1,
        )

        first = worker.run_once()
        self.assertEqual(first["status"], "retryWait", first)
        self.assertEqual(first["failureStage"], "supportValidate")
        self.assertEqual(first["failureCode"], "candidateExtraction.live.supportValidate.factOmitted")
        self.assertEqual(store.candidate_repository.snapshot()["candidates"], {})

        store.lease_repository._jobs[intent.job_id]["availableAt"] = datetime.now(
            timezone.utc
        )
        second = worker.run_once()
        self.assertEqual(second["status"], "completed", second)
        self.assertEqual(second["attempt"], 2)
        self.assertEqual(second["candidateCount"], 1)
        self.assertEqual(len(prompts), 4)
        self.assertNotIn("上次安全合同反馈", prompts[0])
        self.assertIn("上次安全合同反馈", prompts[2])
        self.assertIn("上次安全合同反馈", prompts[3])

    def test_live_contract_failure_does_not_raise_original_max_attempts(self) -> None:
        intent = replace(self.intent, max_attempts=1)
        source_text = "我曾参加过学校合唱团。"
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
                    {"index": 1, "role": "user", "text": source_text, "captureMode": "live"},
                ],
            },
        )
        store.lease_repository.seed(intent)
        request_count = 0

        def handle(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(
                200,
                request=request,
                json={"choices": [{"message": {"content": "not-json"}}]},
            )

        settings = Settings(
            deepseek_api_key="synthetic-test-key",
            owner_truth_live_memory_organization_enabled=True,
        )
        extractor = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=settings,
            organizer=DeepSeekLiveMemoryOrganizationProxy(
                settings,
                transport=httpx.MockTransport(handle),
            ),
        )

        result = self._worker(
            store=store,
            extractor=ModelAssistedOwnerTruthSourceExtractor(
                settings=settings,
                live_extractor=extractor,
            ),
        ).run_once()

        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(result["attempt"], 1)
        self.assertEqual(request_count, 1)
        self.assertEqual(store.candidate_repository.snapshot()["candidates"], {})

    def test_live_contract_feedback_survives_transient_attempt_without_new_budget(self) -> None:
        intent = replace(self.intent, max_attempts=3)
        fact = "本次恢复链测试代号是松涛十号。"
        store = _Store(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            source_id=self.source_id,
            source_content_hash=_digest(fact),
            source_text=fact,
            source_metadata={
                "captureMode": "live",
                "sourcePolicy": "userEvidenceOnly",
                "conversationTurns": [
                    {"index": 1, "role": "user", "text": fact, "captureMode": "live"},
                ],
            },
        )
        store.lease_repository.seed(intent)
        organization = {
            "memories": [{
                "memoryKind": "knowledge",
                "claim": "本次恢复链测试代号是松涛十号。",
                "sourceTurnIndices": [1],
                "facets": _facets(),
            }]
        }
        support = {
            "schemaVersion": "owner-truth-live-memory-support-v1",
            "turnAssessments": [{"turnIndex": 1, "speechAct": "assertion"}],
            "memoryAssessments": [{
                "memoryIndex": 0,
                "verdict": "supported",
                "supportingTurnIndices": [1],
            }],
            "omittedFactBearingTurnIndices": [],
        }
        outcomes: list[object] = [
            "not-json",
            httpx.ConnectError("controlled transport interruption"),
            json.dumps(organization, ensure_ascii=False),
            json.dumps(support, ensure_ascii=False),
        ]
        prompts: list[str] = []

        def handle(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode("utf-8"))
            prompts.append(body["messages"][1]["content"])
            outcome = outcomes.pop(0)
            if isinstance(outcome, httpx.ConnectError):
                raise httpx.ConnectError(str(outcome), request=request)
            return httpx.Response(
                200,
                request=request,
                json={"choices": [{"message": {"content": outcome}}]},
            )

        settings = Settings(
            deepseek_api_key="synthetic-test-key",
            owner_truth_live_memory_organization_enabled=True,
        )
        extractor = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=settings,
            organizer=DeepSeekLiveMemoryOrganizationProxy(
                settings,
                transport=httpx.MockTransport(handle),
            ),
        )
        worker = self._worker(
            store=store,
            extractor=ModelAssistedOwnerTruthSourceExtractor(
                settings=settings,
                live_extractor=extractor,
            ),
            retry_seconds=1,
        )

        first = worker.run_once()
        self.assertEqual(first["status"], "retryWait")
        store.lease_repository._jobs[intent.job_id]["availableAt"] = datetime.now(timezone.utc)
        second = worker.run_once()
        self.assertEqual(second["status"], "retryWait")
        store.lease_repository._jobs[intent.job_id]["availableAt"] = datetime.now(timezone.utc)
        third = worker.run_once()

        self.assertEqual(third["status"], "completed", third)
        self.assertEqual(third["attempt"], 3)
        self.assertEqual(third["candidateCount"], 1)
        self.assertEqual(len(prompts), 4)
        self.assertNotIn("上次安全合同反馈", prompts[0])
        self.assertIn("上次安全合同反馈", prompts[1])
        self.assertIn("上次安全合同反馈", prompts[2])
        self.assertIn("上次安全合同反馈", prompts[3])

    def test_live_transient_then_contract_does_not_gain_contract_retry_budget(self) -> None:
        intent = replace(self.intent, max_attempts=3)
        fact = "本次反向预算测试代号是青竹十三号。"
        store = _Store(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            source_id=self.source_id,
            source_content_hash=_digest(fact),
            source_text=fact,
            source_metadata={
                "captureMode": "live",
                "sourcePolicy": "userEvidenceOnly",
                "conversationTurns": [
                    {"index": 1, "role": "user", "text": fact, "captureMode": "live"},
                ],
            },
        )
        store.lease_repository.seed(intent)
        outcomes: list[object] = [
            httpx.ConnectError("controlled transport interruption"),
            "not-json",
        ]
        prompts: list[str] = []

        def handle(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode("utf-8"))
            prompts.append(body["messages"][1]["content"])
            outcome = outcomes.pop(0)
            if isinstance(outcome, httpx.ConnectError):
                raise httpx.ConnectError(str(outcome), request=request)
            return httpx.Response(
                200,
                request=request,
                json={"choices": [{"message": {"content": outcome}}]},
            )

        settings = Settings(
            deepseek_api_key="synthetic-test-key",
            owner_truth_live_memory_organization_enabled=True,
        )
        live_extractor = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=settings,
            organizer=DeepSeekLiveMemoryOrganizationProxy(
                settings,
                transport=httpx.MockTransport(handle),
            ),
        )
        worker = self._worker(
            store=store,
            extractor=ModelAssistedOwnerTruthSourceExtractor(
                settings=settings,
                live_extractor=live_extractor,
            ),
            retry_seconds=1,
        )

        first = worker.run_once()
        self.assertEqual(first["status"], "retryWait")
        store.lease_repository._jobs[intent.job_id]["availableAt"] = datetime.now(
            timezone.utc
        )
        second = worker.run_once()

        self.assertEqual(second["status"], "failed", second)
        self.assertEqual(second["attempt"], 2)
        self.assertEqual(
            second["failureCode"],
            "candidateExtraction.live.organizationDecode.invalidJson",
        )
        self.assertEqual(len(prompts), 2)
        self.assertNotIn("上次安全合同反馈", prompts[0])
        self.assertNotIn("上次安全合同反馈", prompts[1])
        self.assertEqual(store.candidate_repository.snapshot()["candidates"], {})

    def test_live_missing_configuration_makes_zero_http_requests(self) -> None:
        source_text = "配置缺失测试代号是清川十一号。"
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
                    {"index": 1, "role": "user", "text": source_text, "captureMode": "live"},
                ],
            },
        )
        store.lease_repository.seed(self.intent)
        request_count = 0

        def handle(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(500, request=request)

        settings = Settings(
            deepseek_api_key="",
            owner_truth_live_memory_organization_enabled=True,
        )
        extractor = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=settings,
            organizer=DeepSeekLiveMemoryOrganizationProxy(
                settings,
                transport=httpx.MockTransport(handle),
            ),
        )

        result = self._worker(store=store, extractor=extractor).run_once()

        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(result["failureStage"], "organizationInput")
        self.assertEqual(
            result["failureCode"],
            "candidateExtraction.live.organizationInput.configurationMissing",
        )
        self.assertEqual(request_count, 0)

    def test_default_live_http_contract_rejects_malformed_envelopes_and_truncation(
        self,
    ) -> None:
        cases = (
            ("arrayEnvelope", [], "httpEnvelopeInvalid"),
            ("badChoiceType", {"choices": [42]}, "httpEnvelopeInvalid"),
            (
                "nonTextContent",
                {"choices": [{"message": {"content": ['{"memories":[]}']}}]},
                "contentTypeInvalid",
            ),
            (
                "truncated",
                {
                    "choices": [
                        {
                            "finish_reason": "length",
                            "message": {"content": '{"memories":[]}'},
                        }
                    ]
                },
                "outputTruncated",
            ),
            (
                "finishReasonList",
                {
                    "choices": [{
                        "finish_reason": ["stop"],
                        "message": {"content": '{"memories":[]}'},
                    }],
                },
                "finishReasonTypeInvalid",
            ),
            (
                "finishReasonObject",
                {
                    "choices": [{
                        "finish_reason": {"value": "stop"},
                        "message": {"content": '{"memories":[]}'},
                    }],
                },
                "finishReasonTypeInvalid",
            ),
        )
        for name, envelope, reason in cases:
            with self.subTest(name=name):
                result, request_count, store = self._run_default_live_http_contract_case(
                    response_envelopes=[envelope]
                )
                self.assertEqual(result["status"], "failed", result)
                self.assertEqual(result["failureStage"], "organizationDecode")
                self.assertEqual(
                    result["failureCode"],
                    f"candidateExtraction.live.organizationDecode.{reason}",
                )
                self.assertEqual(request_count, 1)
                self.assertEqual(store.candidate_repository.snapshot()["candidates"], {})

    def test_default_live_support_rejects_boolean_evidence_indices(self) -> None:
        organization = {
            "memories": [
                {
                    "memoryKind": "knowledge",
                    "claim": "本次合同边界测试代号是清泉十二号。",
                    "sourceTurnIndices": [1],
                    "facets": _facets(),
                }
            ]
        }
        support = {
            "schemaVersion": "owner-truth-live-memory-support-v1",
            "turnAssessments": [{"turnIndex": True, "speechAct": "assertion"}],
            "memoryAssessments": [
                {
                    "memoryIndex": 0,
                    "verdict": "supported",
                    "supportingTurnIndices": [True],
                }
            ],
            "omittedFactBearingTurnIndices": [],
        }
        result, request_count, store = self._run_default_live_http_contract_case(
            response_envelopes=[
                {"choices": [{"message": {"content": json.dumps(organization)}}]},
                {"choices": [{"message": {"content": json.dumps(support)}}]},
            ]
        )
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(result["failureStage"], "supportValidate")
        self.assertEqual(
            result["failureCode"],
            "candidateExtraction.live.supportValidate.evidenceInvalid",
        )
        self.assertEqual(request_count, 2)
        self.assertEqual(store.candidate_repository.snapshot()["candidates"], {})

    def test_live_short_and_long_http_candidates_confirm_into_formal_memory(self) -> None:
        short_turns = [
            {"index": 1, "role": "user", "text": "我小学时参加过合唱团。", "captureMode": "live"},
            {"index": 2, "role": "assistant", "text": "我会按原话记录。", "captureMode": "live"},
            {"index": 3, "role": "user", "text": "本次短场代号是青檐九号。", "captureMode": "live"},
        ]
        long_turns = []
        for index in range(1, 32):
            if index % 2 == 0:
                long_turns.append({
                    "index": index,
                    "role": "assistant",
                    "text": "这是助手上下文，不作为用户事实。",
                    "captureMode": "live",
                })
            else:
                text = {
                    1: "我大学时参加过校广播站。",
                    15: "长场中段代号是溪云十五号。",
                    31: "长场结束代号是远峰三十一号。",
                }.get(index, f"这是第 {index} 轮普通问题吗？")
                long_turns.append({
                    "index": index,
                    "role": "user",
                    "text": text,
                    "captureMode": "live",
                })

        scenarios = (
            (
                "short",
                short_turns,
                [
                    {
                        "memoryKind": "experience",
                        "summary": "我小学时参加过合唱团，本次短场代号是青檐九号。",
                        "sourceTurnIndices": [1, 3],
                        "facets": _facets(),
                    }
                ],
                {1: "assertion", 3: "assertion"},
            ),
            (
                "long",
                long_turns,
                [
                    {
                        "memoryKind": "experience",
                        "summary": "我大学时参加过校广播站。",
                        "sourceTurnIndices": [1],
                        "facets": _facets(),
                    },
                    {
                        "memoryKind": "knowledge",
                        "claim": "长场中段代号是溪云十五号。",
                        "sourceTurnIndices": [15],
                        "facets": _facets(),
                    },
                    {
                        "memoryKind": "knowledge",
                        "claim": "长场结束代号是远峰三十一号。",
                        "sourceTurnIndices": [31],
                        "facets": _facets(),
                    },
                ],
                {
                    index: ("assertion" if index in {1, 15, 31} else "query")
                    for index in range(1, 32, 2)
                },
            ),
        )

        for name, turns, memories, speech_acts in scenarios:
            with self.subTest(name=name):
                user_turns = [turn for turn in turns if turn["role"] == "user"]
                source_text = "\n\n".join(str(turn["text"]) for turn in user_turns)
                source_id = str(uuid4())
                intent = replace(
                    self.intent,
                    target=replace(self.intent.target, resource_id=source_id),
                )
                store = _Store(
                    vault_id=self.vault_id,
                    owner_subject_id=self.owner_subject_id,
                    source_id=source_id,
                    source_content_hash=_digest(source_text),
                    source_text=source_text,
                    source_metadata={
                        "captureMode": "live",
                        "sourcePolicy": "userEvidenceOnly",
                        "conversationTurns": turns,
                    },
                )
                store.lease_repository.seed(intent)
                support = {
                    "schemaVersion": "owner-truth-live-memory-support-v1",
                    "turnAssessments": [
                        {
                            "turnIndex": int(turn["index"]),
                            "speechAct": speech_acts[int(turn["index"])],
                        }
                        for turn in user_turns
                    ],
                    "memoryAssessments": [
                        {
                            "memoryIndex": memory_index,
                            "verdict": "supported",
                            "supportingTurnIndices": memory["sourceTurnIndices"],
                        }
                        for memory_index, memory in enumerate(memories)
                    ],
                    "omittedFactBearingTurnIndices": [],
                }
                contents = iter([
                    json.dumps({"memories": memories}, ensure_ascii=False),
                    json.dumps(support, ensure_ascii=False),
                ])
                request_count = 0

                def handle(request: httpx.Request) -> httpx.Response:
                    nonlocal request_count
                    request_count += 1
                    return httpx.Response(
                        200,
                        request=request,
                        json={"choices": [{"message": {"content": next(contents)}}]},
                    )

                settings = Settings(
                    deepseek_api_key="synthetic-test-key",
                    owner_truth_live_memory_organization_enabled=True,
                )
                extractor = ModelAssistedOwnerTruthLiveConversationExtractor(
                    settings=settings,
                    organizer=DeepSeekLiveMemoryOrganizationProxy(
                        settings,
                        transport=httpx.MockTransport(handle),
                    ),
                )
                result = self._worker(store=store, extractor=extractor).run_once()

                self.assertEqual(result["status"], "completed", result)
                self.assertEqual(result["candidateCount"], len(memories))
                self.assertEqual(request_count, 2)
                candidates = self._review_snapshots_from_extraction(store)
                self.assertEqual(len(candidates), len(memories))
                self._confirm_and_reopen_formal_memories(candidates)

    def test_live_long_shape_sends_every_turn_to_organization_and_support(self) -> None:
        turns: list[dict[str, object]] = []
        user_indices: list[int] = []
        for index in range(1, 32):
            if index % 2 == 1:
                user_indices.append(index)
                text = {
                    1: "我小学时参加过合唱团。",
                    15: "中段测试代号是青枝十五号。",
                    31: "结束测试代号是远帆三十一号。",
                }.get(index, f"这是第 {index} 轮普通问题吗？")
                role = "user"
            else:
                text = "这是不作为事实证据的助手上下文。"
                role = "assistant"
            turns.append(
                {
                    "index": index,
                    "role": role,
                    "text": text,
                    "captureMode": "live",
                }
            )
        memories = [
            {
                "memoryKind": "experience",
                "summary": "我小学时参加过合唱团。",
                "sourceTurnIndices": [1],
                "facets": _facets(),
            },
            {
                "memoryKind": "knowledge",
                "claim": "中段测试代号是青枝十五号。",
                "sourceTurnIndices": [15],
                "facets": _facets(),
            },
            {
                "memoryKind": "knowledge",
                "claim": "结束测试代号是远帆三十一号。",
                "sourceTurnIndices": [31],
                "facets": _facets(),
            },
        ]
        support = {
            "schemaVersion": "owner-truth-live-memory-support-v1",
            "turnAssessments": [
                {
                    "turnIndex": index,
                    "speechAct": "assertion" if index in {1, 15, 31} else "query",
                }
                for index in user_indices
            ],
            "memoryAssessments": [
                {
                    "memoryIndex": memory_index,
                    "verdict": "supported",
                    "supportingTurnIndices": [source_index],
                }
                for memory_index, source_index in enumerate((1, 15, 31))
            ],
            "omittedFactBearingTurnIndices": [],
        }
        contents = iter(
            [
                json.dumps({"memories": memories}, ensure_ascii=False),
                json.dumps(support, ensure_ascii=False),
            ]
        )
        requests: list[dict[str, object]] = []

        def handle(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content.decode("utf-8")))
            return httpx.Response(
                200,
                request=request,
                json={"choices": [{"message": {"content": next(contents)}}]},
            )

        settings = Settings(
            deepseek_api_key="synthetic-test-key",
            owner_truth_live_memory_organization_enabled=True,
        )
        extractor = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=settings,
            organizer=DeepSeekLiveMemoryOrganizationProxy(
                settings,
                transport=httpx.MockTransport(handle),
            ),
        )
        source_text = "\n\n".join(
            str(turn["text"]) for turn in turns if turn["role"] == "user"
        )

        result = extractor.extract(
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

        self.assertEqual(len(result.proposals), 3)
        self.assertEqual(len(requests), 2)
        organization_prompt = requests[0]["messages"][1]["content"]
        support_prompt = requests[1]["messages"][1]["content"]
        for index in user_indices:
            marker = f'"index":{index}'
            self.assertIn(marker, organization_prompt)
            self.assertIn(marker, support_prompt)

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
