"""Controlled admission from an acknowledged interview batch to Source effects.

This is deliberately a narrow composition boundary. The private conversation
repository owns messages and review batches; the existing Source writer owns
immutable Sources and the async-effect kernel owns future extraction work.
This service composes them in one Unit of Work without creating Candidate
decisions, DecisionReceipts, MemoryVersions, public routes, or provider calls.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import json
from threading import RLock
from typing import Any, Callable, ContextManager, Mapping, Protocol

from app.async_effects.contracts import EffectReceiptSummary
from app.domain.owner_truth.contracts import OwnerTruthContractError, SourceKind, require_uuid
from app.domain.owner_truth.interview_candidate_proposal import (
    AdmitInterviewReviewBatchForCandidateProposalCommand,
    OwnerTruthInterviewCandidateProposalAccessDenied,
    OwnerTruthInterviewCandidateProposalConflict,
    OwnerTruthInterviewCandidateProposalError,
    OwnerTruthInterviewCandidateProposalPreparation,
    OwnerTruthInterviewCandidateProposalResult,
    OwnerTruthInterviewCandidateProposalVersionConflict,
    OwnerTruthInterviewCandidateProposalWriteRecord,
)
from app.domain.owner_truth.source_commands import (
    CreateTextSourceCommand,
    OwnerTruthCommandAuthorizationCapture,
    OwnerTruthCommandContext,
    OwnerTruthSourceCommandResult,
    OwnerTruthSourceWriteRecord,
)
from app.services.owner_truth_source import build_source_created_effect_intent


FORMAL_INTERVIEW_CANDIDATE_PROPOSAL_FEATURE = "ownerTruthCandidateReview"


class OwnerTruthInterviewCandidateProposalRepository(Protocol):
    def prepare_admission(
        self,
        record: OwnerTruthInterviewCandidateProposalWriteRecord,
    ) -> OwnerTruthInterviewCandidateProposalPreparation | OwnerTruthInterviewCandidateProposalResult:
        ...

    def persist_admission(
        self,
        *,
        record: OwnerTruthInterviewCandidateProposalWriteRecord,
        preparation: OwnerTruthInterviewCandidateProposalPreparation,
        source: OwnerTruthSourceCommandResult,
        effect: EffectReceiptSummary,
    ) -> OwnerTruthInterviewCandidateProposalResult:
        ...

    def read_status(
        self,
        *,
        review_batch_id: str,
        context: OwnerTruthCommandContext,
    ) -> "OwnerTruthInterviewCandidateProposalStatus":
        ...


class OwnerTruthInterviewCandidateProposalStore(Protocol):
    def request_unit_of_work(
        self,
        *,
        correlation_id: str,
        command_id: str,
    ) -> ContextManager[Any]:
        ...

    def owner_truth_interview_candidate_proposal_repository(
        self,
    ) -> OwnerTruthInterviewCandidateProposalRepository:
        ...

    def create_owner_truth_source(
        self,
        record: OwnerTruthSourceWriteRecord,
    ) -> OwnerTruthSourceCommandResult:
        ...

    def effect_kernel_repository(self) -> Any:
        ...


@dataclass(frozen=True)
class OwnerTruthInterviewCandidateProposalStatus:
    """Value-free progress for one review batch's default-off proposal lane.

    This status describes only the durable admission boundary.  It must not be
    mistaken for extraction execution: no worker or Provider is enabled by
    this G0 slice, so a live admitted batch remains ``requested`` and cannot
    yet expose Candidate review content. A Source that has since become
    inactive or no longer matches its immutable provenance is reported as
    invalidated rather than as executable work.
    """

    review_batch_id: str
    review_batch_state: str
    candidate_proposal_status: str
    source_status: str
    candidate_extraction_status: str
    effect_execution_status: str
    candidate_review_status: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "review_batch_id",
            require_uuid(self.review_batch_id, field="review_batch_id"),
        )
        for field in (
            "review_batch_state",
            "candidate_proposal_status",
            "source_status",
            "candidate_extraction_status",
            "effect_execution_status",
            "candidate_review_status",
        ):
            value = str(getattr(self, field) or "").strip()
            if not value:
                raise OwnerTruthInterviewCandidateProposalError(
                    f"{field} is required"
                )
            object.__setattr__(self, field, value)

    def public_summary(self) -> dict[str, Any]:
        """Return a deliberately value-free QA diagnostic envelope."""

        return {
            "schemaVersion": "owner-truth-interview-candidate-proposal-status-v1",
            "reviewBatch": {
                "reviewBatchId": self.review_batch_id,
                "state": self.review_batch_state,
            },
            "candidateProposal": {"status": self.candidate_proposal_status},
            "source": {"status": self.source_status},
            "candidateExtraction": {"status": self.candidate_extraction_status},
            "effectExecution": {"status": self.effect_execution_status},
            "candidateReview": {"status": self.candidate_review_status},
        }


@dataclass(frozen=True)
class OwnerTruthInterviewCandidateProposalExtractionStatus:
    """Value-free read model for one admitted Source's extraction state.

    ``latest_status`` follows the immutable ExtractionResult timeline. Pending
    Candidates intentionally come from the newest successful result, so they
    can remain reviewable even when a later retry failed or was quarantined.
    This mirrors the existing Candidate review composition without exposing
    extraction, Candidate, or Source identifiers.
    """

    latest_status: str | None
    has_pending_candidates: bool

    def __post_init__(self) -> None:
        if self.latest_status not in {None, "succeeded", "failed", "quarantined"}:
            raise OwnerTruthInterviewCandidateProposalError(
                "unsupported candidate extraction status"
            )
        object.__setattr__(self, "has_pending_candidates", bool(self.has_pending_candidates))


def _assert_owner_context(context: OwnerTruthCommandContext) -> None:
    if not isinstance(context, OwnerTruthCommandContext):
        raise OwnerTruthInterviewCandidateProposalAccessDenied(
            "owner truth command context is required"
        )
    if context.actor_subject_id != context.owner_subject_id:
        raise OwnerTruthInterviewCandidateProposalAccessDenied(
            "only the Vault Owner may admit an interview review batch"
        )


def _admitted_source_is_live(
    *,
    batch: "_InMemoryReviewBatchStatus",
    admission: Mapping[str, Any],
    source: Mapping[str, Any] | None,
) -> bool:
    """Revalidate a staged Source without returning its private content.

    Status reads are not an execution permission, but they must not tell an
    Owner that a redacted, stale, or provenance-mismatched Source is still
    safely waiting for extraction. The same conditions are enforced by the
    candidate-review reader and execution-time target admission.
    """

    if not isinstance(source, Mapping):
        return False
    try:
        metadata = source.get("metadata")
        return (
            str(source.get("ownerSubjectId") or "") == batch.owner_subject_id
            and int(source.get("sourceVersion")) == int(admission["sourceVersion"])
            and str(source.get("state") or "") == "active"
            and int(source.get("authorityEpoch")) == batch.authority_epoch
            and str(source.get("sourceKind") or "") == "conversation"
            and str(source.get("contentHash") or "")
            == str(admission["sourceContentHash"])
            and isinstance(metadata, Mapping)
            and str(metadata.get("origin") or "")
            == "interviewReviewBatchCandidateProposal"
            and str(metadata.get("reviewBatchId") or "") == batch.review_batch_id
        )
    except (KeyError, TypeError, ValueError):
        return False


class OwnerTruthInterviewCandidateProposalService:
    """Admit one acknowledged batch into the default-off Source effect lane."""

    def __init__(self, store: OwnerTruthInterviewCandidateProposalStore):
        self._store = store

    def admit_review_batch(
        self,
        *,
        command: AdmitInterviewReviewBatchForCandidateProposalCommand,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthInterviewCandidateProposalResult:
        _assert_owner_context(context)
        record = command.write_record(context=context)
        with self._store.request_unit_of_work(
            correlation_id=f"owner-truth-interview-candidate-proposal-{record.admission_id}",
            command_id=record.command_id_hash,
        ):
            repository = self._store.owner_truth_interview_candidate_proposal_repository()
            prepared = repository.prepare_admission(record)
            if isinstance(prepared, OwnerTruthInterviewCandidateProposalResult):
                return prepared

            source_command = CreateTextSourceCommand(
                command_id=record.source_command_id,
                source_id=record.source_id,
                expected_version=0,
                text=prepared.source_text,
                metadata=prepared.source_metadata,
                source_kind=SourceKind.CONVERSATION,
            )
            source_record = source_command.write_record(context=context)
            source = self._store.create_owner_truth_source(source_record)
            effect = self._store.effect_kernel_repository().accept(
                build_source_created_effect_intent(record=source_record, source=source)
            )
            return repository.persist_admission(
                record=record,
                preparation=prepared,
                source=source,
                effect=effect,
            )


class OwnerTruthInterviewCandidateProposalStatusService:
    """Read one batch's proposal staging state without executing extraction."""

    def __init__(self, store: OwnerTruthInterviewCandidateProposalStore):
        self._store = store

    def read_status(
        self,
        *,
        review_batch_id: str,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthInterviewCandidateProposalStatus:
        _assert_owner_context(context)
        normalized_review_batch_id = require_uuid(
            review_batch_id,
            field="review_batch_id",
        )
        with self._store.request_unit_of_work(
            correlation_id=(
                "owner-truth-interview-candidate-proposal-status:"
                f"{context.vault_id}:{normalized_review_batch_id}"
            ),
            command_id=f"read:{normalized_review_batch_id}",
        ):
            return self._store.owner_truth_interview_candidate_proposal_repository().read_status(
                review_batch_id=normalized_review_batch_id,
                context=context,
            )


@dataclass(frozen=True)
class _InMemoryReviewBatch:
    review_batch_id: str
    vault_id: str
    owner_subject_id: str
    thread_id: str
    session_id: str
    state: str
    row_version: int
    authority_epoch: int
    owner_turn_start_count: int
    owner_turn_end_count: int
    through_message_sequence: int
    owner_messages: tuple[tuple[int, str], ...]
    conversation_turns: tuple[Mapping[str, Any], ...] = ()


def _conversation_capture_mode(turns: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]]) -> str:
    """Return Live only when every recovered turn carries explicit Live consent."""

    modes = {
        str(turn.get("captureMode") or "naturalInput")
        for turn in turns
        if isinstance(turn, Mapping)
    }
    return "live" if modes == {"live"} else "naturalInput"


@dataclass(frozen=True)
class _InMemoryReviewBatchStatus:
    review_batch_id: str
    vault_id: str
    owner_subject_id: str
    state: str
    row_version: int
    authority_epoch: int


class InMemoryOwnerTruthInterviewCandidateProposalRepository:
    """G0 semantic double for acknowledged-batch admission tests.

    Direct service tests can seed one frozen batch explicitly.  The application
    store instead supplies an internal snapshot reconstructed from the real
    in-memory conversation aggregate.  This keeps the HTTP QA path subject to
    the same acknowledged-batch, owner, epoch, and frozen-message-window
    boundary as the Postgres implementation rather than letting a route seed
    an unrelated fixture.
    """

    def __init__(
        self,
        *,
        review_batch_snapshot_lookup: Callable[
            [OwnerTruthInterviewCandidateProposalWriteRecord], Mapping[str, Any] | None
        ]
        | None = None,
        review_batch_status_lookup: Callable[..., Mapping[str, Any] | None] | None = None,
        source_status_lookup: Callable[..., Mapping[str, Any] | None] | None = None,
        extraction_status_lookup: Callable[
            ..., OwnerTruthInterviewCandidateProposalExtractionStatus | None
        ]
        | None = None,
    ) -> None:
        self._lock = RLock()
        self._batches: dict[tuple[str, str], _InMemoryReviewBatch] = {}
        self._admissions_by_command: dict[tuple[str, str], dict[str, Any]] = {}
        self._admissions_by_batch: dict[tuple[str, str], dict[str, Any]] = {}
        self._review_batch_snapshot_lookup = review_batch_snapshot_lookup
        self._review_batch_status_lookup = review_batch_status_lookup
        self._source_status_lookup = source_status_lookup
        self._extraction_status_lookup = extraction_status_lookup

    def seed_review_batch(
        self,
        *,
        review_batch_id: str,
        vault_id: str,
        owner_subject_id: str,
        thread_id: str,
        session_id: str,
        owner_messages: tuple[tuple[int, str], ...],
        state: str = "acknowledged",
        row_version: int = 2,
        authority_epoch: int = 0,
    ) -> None:
        if not owner_messages:
            raise ValueError("owner_messages are required")
        ordered_messages = tuple(sorted(owner_messages, key=lambda item: item[0]))
        self._batches[(vault_id, review_batch_id)] = _InMemoryReviewBatch(
            review_batch_id=review_batch_id,
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            thread_id=thread_id,
            session_id=session_id,
            state=state,
            row_version=row_version,
            authority_epoch=authority_epoch,
            owner_turn_start_count=1,
            owner_turn_end_count=len(ordered_messages),
            through_message_sequence=ordered_messages[-1][0],
            owner_messages=ordered_messages,
            conversation_turns=tuple(
                {"index": sequence, "role": "user", "text": text}
                for sequence, text in ordered_messages
            ),
        )

    def prepare_admission(
        self,
        record: OwnerTruthInterviewCandidateProposalWriteRecord,
    ) -> OwnerTruthInterviewCandidateProposalPreparation | OwnerTruthInterviewCandidateProposalResult:
        with self._lock:
            existing = self._admissions_by_command.get((record.vault_id, record.command_id_hash))
            if existing is not None:
                self._assert_existing_matches(existing, record)
                return self._result_from_item(existing, outcome="deduplicated")

            batch = self._batches.get((record.vault_id, record.review_batch_id))
            if batch is None and self._review_batch_snapshot_lookup is not None:
                snapshot = self._review_batch_snapshot_lookup(record)
                if snapshot is not None:
                    batch = self._batch_from_snapshot(snapshot)
            if batch is None or batch.owner_subject_id != record.owner_subject_id:
                raise OwnerTruthInterviewCandidateProposalAccessDenied(
                    "review batch does not belong to this active Owner Vault"
                )
            if batch.state != "acknowledged":
                raise OwnerTruthInterviewCandidateProposalConflict(
                    "review batch must be acknowledged before candidate proposal admission"
                )
            if batch.row_version != record.expected_review_batch_version:
                raise OwnerTruthInterviewCandidateProposalVersionConflict(
                    expected_version=record.expected_review_batch_version,
                    current_version=batch.row_version,
                )
            if (record.vault_id, record.review_batch_id) in self._admissions_by_batch:
                raise OwnerTruthInterviewCandidateProposalConflict(
                    "review batch already has one candidate proposal admission"
                )
            return self._prepare_from_batch(record=record, batch=batch)

    def persist_admission(
        self,
        *,
        record: OwnerTruthInterviewCandidateProposalWriteRecord,
        preparation: OwnerTruthInterviewCandidateProposalPreparation,
        source: OwnerTruthSourceCommandResult,
        effect: EffectReceiptSummary,
    ) -> OwnerTruthInterviewCandidateProposalResult:
        with self._lock:
            existing = self._admissions_by_command.get((record.vault_id, record.command_id_hash))
            if existing is not None:
                self._assert_existing_matches(existing, record)
                return self._result_from_item(existing, outcome="deduplicated")
            if (record.vault_id, record.review_batch_id) in self._admissions_by_batch:
                raise OwnerTruthInterviewCandidateProposalConflict(
                    "review batch already has one candidate proposal admission"
                )
            if source.source_id != record.source_id or source.source_version != 1:
                raise OwnerTruthInterviewCandidateProposalConflict(
                    "candidate proposal admission source does not match the review batch record"
                )
            item = {
                "admissionId": record.admission_id,
                "commandIdHash": record.command_id_hash,
                "payloadHash": record.payload_hash,
                "reviewBatchId": record.review_batch_id,
                "sourceContentHash": source.content_hash,
                "sourceId": source.source_id,
                "sourceVersion": source.source_version,
                "effectOperationId": effect.operation_id,
                "ownerMessageCount": preparation.owner_message_count,
                "actorSubjectId": record.actor_subject_id,
                "policyVersion": record.policy_version,
                "authorizationCapture": record.authorization_capture,
            }
            self._admissions_by_command[(record.vault_id, record.command_id_hash)] = item
            self._admissions_by_batch[(record.vault_id, record.review_batch_id)] = item
            return self._result_from_item(item, outcome="created")

    def read_status(
        self,
        *,
        review_batch_id: str,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthInterviewCandidateProposalStatus:
        _assert_owner_context(context)
        normalized_batch_id = require_uuid(review_batch_id, field="review_batch_id")
        with self._lock:
            batch = self._status_batch(
                vault_id=context.vault_id,
                owner_subject_id=context.owner_subject_id,
                review_batch_id=normalized_batch_id,
            )
            admission = self._admissions_by_batch.get(
                (context.vault_id, normalized_batch_id)
            )
            source_is_live = True
            if admission is not None and self._source_status_lookup is not None:
                source_is_live = _admitted_source_is_live(
                    batch=batch,
                    admission=admission,
                    source=self._source_status_lookup(
                        vault_id=context.vault_id,
                        source_id=str(admission["sourceId"]),
                    ),
                )
            extraction_status = None
            if (
                admission is not None
                and source_is_live
                and self._extraction_status_lookup is not None
            ):
                extraction_status = self._extraction_status_lookup(
                    vault_id=context.vault_id,
                    owner_subject_id=context.owner_subject_id,
                    source_id=str(admission["sourceId"]),
                    source_version=int(admission["sourceVersion"]),
                    authority_epoch=batch.authority_epoch,
                )
            return self._status_from_batch(
                batch=batch,
                admitted=admission is not None,
                source_is_live=source_is_live,
                extraction_status=extraction_status,
            )

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {
                "admissionsByBatch": deepcopy(self._admissions_by_batch),
                "admissionsByCommand": deepcopy(self._admissions_by_command),
            }

    def _status_batch(
        self,
        *,
        vault_id: str,
        owner_subject_id: str,
        review_batch_id: str,
    ) -> _InMemoryReviewBatchStatus:
        batch = self._batches.get((vault_id, review_batch_id))
        if batch is not None:
            if batch.owner_subject_id != owner_subject_id:
                raise OwnerTruthInterviewCandidateProposalAccessDenied(
                    "review batch does not belong to this active Owner Vault"
                )
            return _InMemoryReviewBatchStatus(
                review_batch_id=batch.review_batch_id,
                vault_id=batch.vault_id,
                owner_subject_id=batch.owner_subject_id,
                state=batch.state,
                row_version=batch.row_version,
                authority_epoch=batch.authority_epoch,
            )
        if self._review_batch_status_lookup is None:
            raise OwnerTruthInterviewCandidateProposalAccessDenied(
                "review batch does not belong to this active Owner Vault"
            )
        snapshot = self._review_batch_status_lookup(
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            review_batch_id=review_batch_id,
        )
        if snapshot is None:
            raise OwnerTruthInterviewCandidateProposalAccessDenied(
                "review batch does not belong to this active Owner Vault"
            )
        return self._status_batch_from_snapshot(snapshot)

    @staticmethod
    def _status_batch_from_snapshot(
        snapshot: Mapping[str, Any],
    ) -> _InMemoryReviewBatchStatus:
        if not isinstance(snapshot, Mapping):
            raise OwnerTruthInterviewCandidateProposalConflict(
                "review batch status is not recoverable"
            )
        try:
            return _InMemoryReviewBatchStatus(
                review_batch_id=require_uuid(
                    snapshot["reviewBatchId"],
                    field="review_batch_id",
                ),
                vault_id=str(snapshot["vaultId"]),
                owner_subject_id=str(snapshot["ownerSubjectId"]),
                state=str(snapshot["state"]),
                row_version=int(snapshot["rowVersion"]),
                authority_epoch=int(snapshot["authorityEpoch"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise OwnerTruthInterviewCandidateProposalConflict(
                "review batch status is not recoverable"
            ) from error

    @staticmethod
    def _status_from_batch(
        *,
        batch: _InMemoryReviewBatchStatus,
        admitted: bool,
        source_is_live: bool = True,
        extraction_status: OwnerTruthInterviewCandidateProposalExtractionStatus | None = None,
    ) -> OwnerTruthInterviewCandidateProposalStatus:
        if batch.state == "pendingAcknowledgement":
            return OwnerTruthInterviewCandidateProposalStatus(
                review_batch_id=batch.review_batch_id,
                review_batch_state=batch.state,
                candidate_proposal_status="pendingAcknowledgement",
                source_status="notAdmitted",
                candidate_extraction_status="notRequested",
                effect_execution_status="disabled",
                candidate_review_status="notReady",
            )
        if batch.state != "acknowledged":
            raise OwnerTruthInterviewCandidateProposalConflict(
                "review batch status is not supported for candidate proposal admission"
            )
        if not admitted:
            return OwnerTruthInterviewCandidateProposalStatus(
                review_batch_id=batch.review_batch_id,
                review_batch_state=batch.state,
                candidate_proposal_status="readyForAdmission",
                source_status="notAdmitted",
                candidate_extraction_status="notRequested",
                effect_execution_status="disabled",
                candidate_review_status="notReady",
            )
        if not source_is_live:
            return OwnerTruthInterviewCandidateProposalStatus(
                review_batch_id=batch.review_batch_id,
                review_batch_state=batch.state,
                candidate_proposal_status="invalidated",
                source_status="inactive",
                candidate_extraction_status="blocked",
                effect_execution_status="disabled",
                candidate_review_status="notReady",
            )
        if extraction_status is not None and extraction_status.latest_status is not None:
            candidate_review_status = (
                "reviewReady"
                if extraction_status.has_pending_candidates
                else {
                    "succeeded": "noCandidates",
                    "failed": "extractionFailed",
                    "quarantined": "extractionQuarantined",
                }[extraction_status.latest_status]
            )
            return OwnerTruthInterviewCandidateProposalStatus(
                review_batch_id=batch.review_batch_id,
                review_batch_state=batch.state,
                candidate_proposal_status="admitted",
                source_status="admitted",
                candidate_extraction_status=extraction_status.latest_status,
                # The default Source-effect worker remains disabled. A durable
                # QA result may still be present through the exact synthetic
                # admission boundary; do not imply Provider execution here.
                effect_execution_status="disabled",
                candidate_review_status=candidate_review_status,
            )
        return OwnerTruthInterviewCandidateProposalStatus(
            review_batch_id=batch.review_batch_id,
            review_batch_state=batch.state,
            candidate_proposal_status="admitted",
            source_status="admitted",
            candidate_extraction_status="requested",
            effect_execution_status="disabled",
            candidate_review_status="notReady",
        )

    @staticmethod
    def _batch_from_snapshot(snapshot: Mapping[str, Any]) -> _InMemoryReviewBatch:
        """Normalise an internal conversation aggregate snapshot defensively."""

        if not isinstance(snapshot, Mapping):
            raise OwnerTruthInterviewCandidateProposalConflict(
                "review batch snapshot is not recoverable"
            )
        raw_messages = snapshot.get("ownerMessages")
        if not isinstance(raw_messages, (tuple, list)) or not raw_messages:
            raise OwnerTruthInterviewCandidateProposalConflict(
                "review batch owner message window is not recoverable"
            )
        try:
            owner_messages = tuple(
                sorted(
                    (
                        (int(item[0]), str(item[1]).strip())
                        for item in raw_messages
                        if isinstance(item, (tuple, list)) and len(item) == 2
                    ),
                    key=lambda item: item[0],
                )
            )
            raw_turns = snapshot.get("conversationTurns") or ()
            conversation_turns = tuple(
                {
                    "index": int(turn["index"]),
                    "role": str(turn["role"]),
                    "text": str(turn["text"]),
                    "captureMode": str(turn.get("captureMode") or "naturalInput"),
                }
                for turn in raw_turns
            )
            batch = _InMemoryReviewBatch(
                review_batch_id=str(snapshot["reviewBatchId"]),
                vault_id=str(snapshot["vaultId"]),
                owner_subject_id=str(snapshot["ownerSubjectId"]),
                thread_id=str(snapshot["threadId"]),
                session_id=str(snapshot["sessionId"]),
                state=str(snapshot["state"]),
                row_version=int(snapshot["rowVersion"]),
                authority_epoch=int(snapshot["authorityEpoch"]),
                owner_turn_start_count=int(snapshot["ownerTurnStartCount"]),
                owner_turn_end_count=int(snapshot["ownerTurnEndCount"]),
                through_message_sequence=int(snapshot["throughMessageSequence"]),
                owner_messages=owner_messages,
                conversation_turns=conversation_turns,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise OwnerTruthInterviewCandidateProposalConflict(
                "review batch snapshot is not recoverable"
            ) from error
        if (
            not batch.owner_messages
            or any(sequence < 1 or not text for sequence, text in batch.owner_messages)
            or batch.row_version < 1
            or batch.owner_turn_start_count < 1
            or batch.owner_turn_end_count < batch.owner_turn_start_count
            or batch.through_message_sequence < batch.owner_messages[-1][0]
            or any(
                turn.get("role") not in {"user", "assistant"}
                or int(turn.get("index") or 0) < 1
                or not str(turn.get("text") or "").strip()
                for turn in batch.conversation_turns
            )
        ):
            raise OwnerTruthInterviewCandidateProposalConflict(
                "review batch owner message window is not recoverable"
            )
        return batch

    @staticmethod
    def _prepare_from_batch(
        *,
        record: OwnerTruthInterviewCandidateProposalWriteRecord,
        batch: _InMemoryReviewBatch,
    ) -> OwnerTruthInterviewCandidateProposalPreparation:
        source_text = "\n\n".join(text.strip() for _, text in batch.owner_messages)
        return OwnerTruthInterviewCandidateProposalPreparation(
            review_batch_id=batch.review_batch_id,
            thread_id=batch.thread_id,
            session_id=batch.session_id,
            source_text=source_text,
            source_metadata={
                "origin": "interviewReviewBatchCandidateProposal",
                "candidateProposalAdmissionId": record.admission_id,
                "reviewBatchId": batch.review_batch_id,
                "threadId": batch.thread_id,
                "sessionId": batch.session_id,
                "ownerTurnStartCount": batch.owner_turn_start_count,
                "ownerTurnEndCount": batch.owner_turn_end_count,
                "throughMessageSequence": batch.through_message_sequence,
                "ownerMessageCount": len(batch.owner_messages),
                "conversationTurns": list(batch.conversation_turns),
                "captureMode": _conversation_capture_mode(list(batch.conversation_turns)),
                "sourcePolicy": "userEvidenceOnly",
            },
            owner_message_count=len(batch.owner_messages),
            first_message_sequence=batch.owner_messages[0][0],
            last_message_sequence=batch.owner_messages[-1][0],
        )

    @staticmethod
    def _assert_existing_matches(
        item: Mapping[str, Any], record: OwnerTruthInterviewCandidateProposalWriteRecord) -> None:
        expected = {
            "admissionId": record.admission_id,
            "commandIdHash": record.command_id_hash,
            "payloadHash": record.payload_hash,
            "reviewBatchId": record.review_batch_id,
            "sourceId": record.source_id,
            "actorSubjectId": record.actor_subject_id,
            "policyVersion": record.policy_version,
        }
        if any(str(item[key]) != str(value) for key, value in expected.items()):
            raise OwnerTruthInterviewCandidateProposalConflict(
                "commandId cannot be reused with a different review batch candidate proposal admission"
            )
        _assert_replay_authorization_capture(
            existing=item.get("authorizationCapture"),
            expected=record.authorization_capture,
        )

    @staticmethod
    def _result_from_item(
        item: Mapping[str, Any],
        *,
        outcome: str,
    ) -> OwnerTruthInterviewCandidateProposalResult:
        return OwnerTruthInterviewCandidateProposalResult(
            outcome=outcome,
            admission_id=str(item["admissionId"]),
            review_batch_id=str(item["reviewBatchId"]),
            source_id=str(item["sourceId"]),
            source_version=int(item["sourceVersion"]),
            source_content_hash=str(item["sourceContentHash"]),
            effect_operation_id=str(item["effectOperationId"]),
            owner_message_count=int(item["ownerMessageCount"]),
        )


class PostgresOwnerTruthInterviewCandidateProposalRepository:
    """Postgres persistence for one acknowledged-batch Source/effect admission."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def prepare_admission(
        self,
        record: OwnerTruthInterviewCandidateProposalWriteRecord,
    ) -> OwnerTruthInterviewCandidateProposalPreparation | OwnerTruthInterviewCandidateProposalResult:
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked",
                (f"owner-truth-interview-candidate-proposal-command:{record.vault_id}:{record.command_id_hash}",),
            )
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked",
                (f"owner-truth-interview-candidate-proposal-batch:{record.vault_id}:{record.review_batch_id}",),
            )
            vault = self._locked_active_vault(cursor, record=record)
            existing = self._existing_by_command(cursor, record=record)
            if existing is not None:
                return self._result_from_row(existing, outcome="deduplicated")

            batch = self._locked_review_batch(cursor, record=record)
            if (
                str(batch["owner_subject_id"]) != record.owner_subject_id
                or int(batch["authority_epoch"]) != int(vault["authority_epoch"])
            ):
                raise OwnerTruthInterviewCandidateProposalAccessDenied(
                    "review batch does not belong to this active Owner Vault"
                )
            if str(batch["state"]) != "acknowledged":
                raise OwnerTruthInterviewCandidateProposalConflict(
                    "review batch must be acknowledged before candidate proposal admission"
                )
            current_version = int(batch["row_version"])
            if current_version != record.expected_review_batch_version:
                raise OwnerTruthInterviewCandidateProposalVersionConflict(
                    expected_version=record.expected_review_batch_version,
                    current_version=current_version,
                )
            existing_batch = self._existing_by_batch(cursor, record=record)
            if existing_batch is not None:
                raise OwnerTruthInterviewCandidateProposalConflict(
                    "review batch already has one candidate proposal admission"
                )

            messages = self._owner_messages_for_batch(cursor, batch=batch, record=record)
            expected_count = int(batch["captured_candidate_batch_turn_count"])
            if len(messages) != expected_count:
                raise OwnerTruthInterviewCandidateProposalConflict(
                    "review batch owner message window is no longer recoverable"
                )
            source_text = "\n\n".join(message["text"] for message in messages)
            conversation_turns = self._conversation_turns_for_window(
                cursor,
                batch=batch,
                record=record,
                first_message_sequence=int(messages[0]["sequence_number"]),
                last_message_sequence=int(messages[-1]["sequence_number"]),
            )
            return OwnerTruthInterviewCandidateProposalPreparation(
                review_batch_id=record.review_batch_id,
                thread_id=str(batch["thread_id"]),
                session_id=str(batch["session_id"]),
                source_text=source_text,
                source_metadata={
                    "origin": "interviewReviewBatchCandidateProposal",
                    "candidateProposalAdmissionId": record.admission_id,
                    "reviewBatchId": record.review_batch_id,
                    "threadId": str(batch["thread_id"]),
                    "sessionId": str(batch["session_id"]),
                    "ownerTurnStartCount": int(batch["owner_turn_start_count"]),
                    "ownerTurnEndCount": int(batch["owner_turn_end_count"]),
                    "throughMessageSequence": int(batch["through_message_sequence"]),
                    "ownerMessageCount": len(messages),
                    "conversationTurns": conversation_turns,
                    "captureMode": _conversation_capture_mode(conversation_turns),
                    "sourcePolicy": "userEvidenceOnly",
                },
                owner_message_count=len(messages),
                first_message_sequence=int(messages[0]["sequence_number"]),
                last_message_sequence=int(messages[-1]["sequence_number"]),
            )

    def persist_admission(
        self,
        *,
        record: OwnerTruthInterviewCandidateProposalWriteRecord,
        preparation: OwnerTruthInterviewCandidateProposalPreparation,
        source: OwnerTruthSourceCommandResult,
        effect: EffectReceiptSummary,
    ) -> OwnerTruthInterviewCandidateProposalResult:
        if preparation.review_batch_id != record.review_batch_id:
            raise OwnerTruthInterviewCandidateProposalConflict(
                "prepared review batch does not match candidate proposal admission"
            )
        if source.source_id != record.source_id or source.source_version != 1:
            raise OwnerTruthInterviewCandidateProposalConflict(
                "candidate proposal admission Source does not match the requested review batch"
            )
        with self._cursor() as cursor:
            existing = self._existing_by_command(cursor, record=record)
            if existing is not None:
                return self._result_from_row(existing, outcome="deduplicated")
            existing_batch = self._existing_by_batch(cursor, record=record)
            if existing_batch is not None:
                raise OwnerTruthInterviewCandidateProposalConflict(
                    "review batch already has one candidate proposal admission"
                )
            cursor.execute(
                """
                INSERT INTO owner_truth.interview_review_batch_candidate_admissions (
                    id, vault_id, owner_subject_id, review_batch_id,
                    source_id, source_version, source_content_hash,
                    effect_operation_id, command_id_hash, payload_hash,
                    actor_subject_id, policy_version, authorization_evidence, authority_epoch,
                    owner_message_count, first_message_sequence, last_message_sequence
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, review_batch_id, source_id, source_version,
                    source_content_hash, effect_operation_id, owner_message_count
                """,
                (
                    record.admission_id,
                    record.vault_id,
                    record.owner_subject_id,
                    record.review_batch_id,
                    source.source_id,
                    source.source_version,
                    source.content_hash,
                    effect.operation_id,
                    record.command_id_hash,
                    record.payload_hash,
                    record.actor_subject_id,
                    record.policy_version,
                    _authorization_capture_json(record.authorization_capture),
                    source.authority_epoch,
                    preparation.owner_message_count,
                    preparation.first_message_sequence,
                    preparation.last_message_sequence,
                ),
            )
            row = cursor.fetchone()
            if row is None:  # pragma: no cover - INSERT RETURNING must produce a row
                raise RuntimeError("review batch candidate proposal admission insert did not produce a row")
            return self._result_from_row(row, outcome="created")

    def read_status(
        self,
        *,
        review_batch_id: str,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthInterviewCandidateProposalStatus:
        _assert_owner_context(context)
        normalized_batch_id = require_uuid(review_batch_id, field="review_batch_id")
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT b.id AS review_batch_id, b.owner_subject_id AS batch_owner_subject_id,
                    b.state AS review_batch_state,
                    b.authority_epoch AS batch_authority_epoch,
                    v.owner_subject_id AS vault_owner_subject_id,
                    v.status AS vault_status,
                    v.authority_epoch AS vault_authority_epoch,
                    a.id AS admission_id,
                    a.authority_epoch AS admission_authority_epoch,
                    a.source_id AS admission_source_id,
                    a.source_version AS admission_source_version,
                    a.source_content_hash AS admission_source_content_hash,
                    s.owner_subject_id AS source_owner_subject_id,
                    s.source_version AS source_version,
                    s.state AS source_state,
                    s.authority_epoch AS source_authority_epoch,
                    s.source_kind AS source_kind,
                    s.content_hash AS source_content_hash,
                    s.metadata AS source_metadata
                FROM owner_truth.interview_review_batches AS b
                JOIN owner_truth.vaults AS v ON v.vault_id = b.vault_id
                LEFT JOIN owner_truth.interview_review_batch_candidate_admissions AS a
                  ON a.vault_id = b.vault_id AND a.review_batch_id = b.id
                LEFT JOIN owner_truth.sources AS s
                  ON s.vault_id = a.vault_id AND s.id = a.source_id
                WHERE b.vault_id = %s AND b.id = %s
                """,
                (context.vault_id, normalized_batch_id),
            )
            row = cursor.fetchone()
        if (
            row is None
            or str(row["vault_owner_subject_id"]) != context.owner_subject_id
            or str(row["vault_status"]) != "active"
            or str(row["batch_owner_subject_id"]) != context.owner_subject_id
        ):
            raise OwnerTruthInterviewCandidateProposalAccessDenied(
                "review batch does not belong to this active Owner Vault"
            )
        if int(row["batch_authority_epoch"]) != int(row["vault_authority_epoch"]):
            raise OwnerTruthInterviewCandidateProposalConflict(
                "review batch authority is no longer current"
            )
        status = _InMemoryReviewBatchStatus(
            review_batch_id=str(row["review_batch_id"]),
            vault_id=context.vault_id,
            owner_subject_id=context.owner_subject_id,
            state=str(row["review_batch_state"]),
            row_version=1,
            authority_epoch=int(row["vault_authority_epoch"]),
        )
        admitted = row["admission_id"] is not None
        if admitted and int(row["admission_authority_epoch"]) != int(
            row["vault_authority_epoch"]
        ):
            raise OwnerTruthInterviewCandidateProposalConflict(
                "candidate proposal admission authority is no longer current"
            )
        source_is_live = True
        if admitted:
            source_is_live = _admitted_source_is_live(
                batch=status,
                admission={
                    "sourceVersion": row["admission_source_version"],
                    "sourceContentHash": row["admission_source_content_hash"],
                },
                source={
                    "ownerSubjectId": row["source_owner_subject_id"],
                    "sourceVersion": row["source_version"],
                    "state": row["source_state"],
                    "authorityEpoch": row["source_authority_epoch"],
                    "sourceKind": row["source_kind"],
                    "contentHash": row["source_content_hash"],
                    "metadata": row["source_metadata"],
                },
            )
        extraction_status = None
        if admitted and source_is_live:
            with self._cursor() as cursor:
                extraction_status = self._read_extraction_status(
                    cursor,
                    vault_id=context.vault_id,
                    owner_subject_id=context.owner_subject_id,
                    source_id=str(row["admission_source_id"]),
                    source_version=int(row["admission_source_version"]),
                    authority_epoch=int(row["vault_authority_epoch"]),
                )
        return InMemoryOwnerTruthInterviewCandidateProposalRepository._status_from_batch(
            batch=status,
            admitted=admitted,
            source_is_live=source_is_live,
            extraction_status=extraction_status,
        )

    @staticmethod
    def _read_extraction_status(
        cursor: Any,
        *,
        vault_id: str,
        owner_subject_id: str,
        source_id: str,
        source_version: int,
        authority_epoch: int,
    ) -> OwnerTruthInterviewCandidateProposalExtractionStatus | None:
        """Match the Candidate-review selection rules without returning values.

        A newer failed/quarantined extraction is still the latest result, while
        pending Candidates remain selectable from the newest successful result.
        The proposal-status endpoint only reports those two booleans/labels;
        it never exposes the result, Candidate, or Source identifiers.
        """

        cursor.execute(
            """
            SELECT id, status
            FROM owner_truth.extraction_results
            WHERE vault_id = %s
              AND source_id = %s
              AND source_version = %s
            ORDER BY completed_at DESC NULLS LAST, created_at DESC, id DESC
            LIMIT 1
            """,
            (vault_id, source_id, source_version),
        )
        latest = cursor.fetchone()
        if latest is None:
            return None

        cursor.execute(
            """
            SELECT id
            FROM owner_truth.extraction_results
            WHERE vault_id = %s
              AND source_id = %s
              AND source_version = %s
              AND status = 'succeeded'
            ORDER BY completed_at DESC NULLS LAST, created_at DESC, id DESC
            LIMIT 1
            """,
            (vault_id, source_id, source_version),
        )
        selected = cursor.fetchone()
        has_pending_candidates = False
        if selected is not None:
            cursor.execute(
                """
                SELECT 1
                FROM owner_truth.memory_candidates
                WHERE vault_id = %s
                  AND owner_subject_id = %s
                  AND source_id = %s
                  AND extraction_result_id = %s
                  AND decision_status = 'pending'
                  AND authority_epoch = %s
                LIMIT 1
                """,
                (
                    vault_id,
                    owner_subject_id,
                    source_id,
                    selected["id"],
                    authority_epoch,
                ),
            )
            has_pending_candidates = cursor.fetchone() is not None
        return OwnerTruthInterviewCandidateProposalExtractionStatus(
            latest_status=str(latest["status"]),
            has_pending_candidates=has_pending_candidates,
        )

    def _locked_active_vault(
        self,
        cursor: Any,
        *,
        record: OwnerTruthInterviewCandidateProposalWriteRecord,
    ) -> Mapping[str, Any]:
        cursor.execute(
            """
            SELECT vault_id, owner_subject_id, authority_epoch, status
            FROM owner_truth.vaults
            WHERE vault_id = %s
            FOR UPDATE
            """,
            (record.vault_id,),
        )
        vault = cursor.fetchone()
        if (
            vault is None
            or str(vault["owner_subject_id"]) != record.owner_subject_id
            or str(vault["status"]) != "active"
        ):
            raise OwnerTruthInterviewCandidateProposalAccessDenied(
                "review batch does not belong to an active Owner Vault"
            )
        return vault

    @staticmethod
    def _locked_review_batch(
        cursor: Any,
        *,
        record: OwnerTruthInterviewCandidateProposalWriteRecord,
    ) -> Mapping[str, Any]:
        cursor.execute(
            """
            SELECT id, vault_id, owner_subject_id, session_id, thread_id,
                state, captured_candidate_batch_turn_count,
                owner_turn_start_count, owner_turn_end_count,
                through_message_sequence, row_version, authority_epoch
            FROM owner_truth.interview_review_batches
            WHERE vault_id = %s AND id = %s
            FOR UPDATE
            """,
            (record.vault_id, record.review_batch_id),
        )
        batch = cursor.fetchone()
        if batch is None:
            raise OwnerTruthInterviewCandidateProposalAccessDenied(
                "review batch does not belong to this active Owner Vault"
            )
        return batch

    @staticmethod
    def _owner_messages_for_batch(
        cursor: Any,
        *,
        batch: Mapping[str, Any],
        record: OwnerTruthInterviewCandidateProposalWriteRecord,
    ) -> list[dict[str, Any]]:
        cursor.execute(
            """
            WITH owner_messages AS (
                SELECT id, sequence_number, content_payload,
                    ROW_NUMBER() OVER (ORDER BY sequence_number ASC) AS owner_turn_number
                FROM owner_truth.conversation_messages
                WHERE vault_id = %s
                  AND owner_subject_id = %s
                  AND thread_id = %s
                  AND session_id = %s
                  AND author = 'owner'
                  AND sequence_number <= %s
                  AND authority_epoch = %s
            )
            SELECT id, sequence_number, content_payload
            FROM owner_messages
            WHERE owner_turn_number BETWEEN %s AND %s
            ORDER BY sequence_number ASC
            """,
            (
                record.vault_id,
                record.owner_subject_id,
                str(batch["thread_id"]),
                str(batch["session_id"]),
                int(batch["through_message_sequence"]),
                int(batch["authority_epoch"]),
                int(batch["owner_turn_start_count"]),
                int(batch["owner_turn_end_count"]),
            ),
        )
        messages: list[dict[str, Any]] = []
        for row in cursor.fetchall():
            payload = row["content_payload"]
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError as exc:
                    raise OwnerTruthInterviewCandidateProposalConflict(
                        "review batch message payload is not recoverable"
                    ) from exc
            if not isinstance(payload, Mapping):
                raise OwnerTruthInterviewCandidateProposalConflict(
                    "review batch message payload is not recoverable"
                )
            text = str(payload.get("text") or "").strip()
            if not text:
                raise OwnerTruthInterviewCandidateProposalConflict(
                    "review batch owner message is not recoverable"
                )
            messages.append(
                {
                    "id": str(row["id"]),
                    "sequence_number": int(row["sequence_number"]),
                    "text": text,
                }
            )
        return messages

    @staticmethod
    def _conversation_turns_for_window(
        cursor: Any,
        *,
        batch: Mapping[str, Any],
        record: OwnerTruthInterviewCandidateProposalWriteRecord,
        first_message_sequence: int,
        last_message_sequence: int,
    ) -> list[dict[str, Any]]:
        cursor.execute(
            """
            SELECT sequence_number, author, content_payload
            FROM owner_truth.conversation_messages
            WHERE vault_id = %s
              AND owner_subject_id = %s
              AND thread_id = %s
              AND session_id = %s
              AND sequence_number BETWEEN %s AND %s
              AND authority_epoch = %s
            ORDER BY sequence_number ASC
            """,
            (
                record.vault_id,
                record.owner_subject_id,
                str(batch["thread_id"]),
                str(batch["session_id"]),
                first_message_sequence,
                last_message_sequence,
                int(batch["authority_epoch"]),
            ),
        )
        turns: list[dict[str, Any]] = []
        for row in cursor.fetchall():
            payload = row["content_payload"]
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError as exc:
                    raise OwnerTruthInterviewCandidateProposalConflict(
                        "review batch conversation context is not recoverable"
                    ) from exc
            text = str(payload.get("text") or "").strip() if isinstance(payload, Mapping) else ""
            author = str(row["author"] or "").strip()
            if not text or author not in {"owner", "assistant"}:
                raise OwnerTruthInterviewCandidateProposalConflict(
                    "review batch conversation context is not recoverable"
                )
            turns.append(
                {
                    "index": int(row["sequence_number"]),
                    "role": "user" if author == "owner" else "assistant",
                    "text": text,
                    "captureMode": str(payload.get("captureMode") or "naturalInput"),
                }
            )
        return turns

    @staticmethod
    def _existing_by_command(
        cursor: Any,
        *,
        record: OwnerTruthInterviewCandidateProposalWriteRecord,
    ) -> Mapping[str, Any] | None:
        cursor.execute(
            """
            SELECT id, review_batch_id, source_id, source_version,
                source_content_hash, effect_operation_id, owner_message_count,
                command_id_hash, payload_hash, actor_subject_id, policy_version,
                authorization_evidence
            FROM owner_truth.interview_review_batch_candidate_admissions
            WHERE vault_id = %s AND command_id_hash = %s
            FOR UPDATE
            """,
            (record.vault_id, record.command_id_hash),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        expected = {
            "id": record.admission_id,
            "review_batch_id": record.review_batch_id,
            "source_id": record.source_id,
            "command_id_hash": record.command_id_hash,
            "payload_hash": record.payload_hash,
            "actor_subject_id": record.actor_subject_id,
            "policy_version": record.policy_version,
        }
        if any(str(row[key]) != str(value) for key, value in expected.items()):
            raise OwnerTruthInterviewCandidateProposalConflict(
                "commandId cannot be reused with a different review batch candidate proposal admission"
            )
        _assert_replay_authorization_capture(
            existing=_authorization_capture_from_database(row.get("authorization_evidence")),
            expected=record.authorization_capture,
        )
        return row

    @staticmethod
    def _existing_by_batch(
        cursor: Any,
        *,
        record: OwnerTruthInterviewCandidateProposalWriteRecord,
    ) -> Mapping[str, Any] | None:
        cursor.execute(
            """
            SELECT id, review_batch_id, source_id, source_version,
                source_content_hash, effect_operation_id, owner_message_count,
                command_id_hash, payload_hash, actor_subject_id, policy_version,
                authorization_evidence
            FROM owner_truth.interview_review_batch_candidate_admissions
            WHERE vault_id = %s AND review_batch_id = %s
            FOR UPDATE
            """,
            (record.vault_id, record.review_batch_id),
        )
        return cursor.fetchone()

    @staticmethod
    def _result_from_row(
        row: Mapping[str, Any],
        *,
        outcome: str,
    ) -> OwnerTruthInterviewCandidateProposalResult:
        return OwnerTruthInterviewCandidateProposalResult(
            outcome=outcome,
            admission_id=str(row["id"]),
            review_batch_id=str(row["review_batch_id"]),
            source_id=str(row["source_id"]),
            source_version=int(row["source_version"]),
            source_content_hash=str(row["source_content_hash"]),
            effect_operation_id=str(row["effect_operation_id"]),
            owner_message_count=int(row["owner_message_count"]),
        )

    @contextmanager
    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - psycopg is a runtime dependency
            dict_row = None
        with self._connection.cursor(row_factory=dict_row) as cursor:
            yield cursor


def _assert_replay_authorization_capture(
    *,
    existing: OwnerTruthCommandAuthorizationCapture | None,
    expected: OwnerTruthCommandAuthorizationCapture | None,
) -> None:
    """Keep legacy QA admissions and formal admissions non-interchangeable.

    An idempotent formal retry may carry a fresh release-policy decision or
    expiry. The immutable admission root only needs to prove the same formal
    feature was used originally; it must never let a QA-only replay impersonate
    that root, or vice versa.
    """

    if (existing is None) != (expected is None):
        raise OwnerTruthInterviewCandidateProposalConflict(
            "commandId cannot replay between QA-only and formally authorized candidate proposal admission"
        )
    if (
        existing is not None
        and expected is not None
        and existing.feature != expected.feature
    ):
        raise OwnerTruthInterviewCandidateProposalConflict(
            "commandId cannot replay under a different authorization feature"
        )


def _authorization_capture_json(
    capture: OwnerTruthCommandAuthorizationCapture | None,
) -> Any:
    payload = {} if capture is None else capture.value_minimized_payload()
    try:
        from psycopg.types.json import Jsonb
    except ImportError:  # pragma: no cover - production dependency
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return Jsonb(payload)


def _authorization_capture_from_database(
    value: object,
) -> OwnerTruthCommandAuthorizationCapture | None:
    if value is None or value == {}:
        return None
    payload = value
    if isinstance(value, str):
        try:
            payload = json.loads(value)
        except json.JSONDecodeError as exc:
            raise OwnerTruthInterviewCandidateProposalConflict(
                "stored candidate proposal authorization evidence is not valid JSON"
            ) from exc
    if not isinstance(payload, Mapping):
        raise OwnerTruthInterviewCandidateProposalConflict(
            "stored candidate proposal authorization evidence must be an object"
        )
    try:
        capture = OwnerTruthCommandAuthorizationCapture.from_value_minimized_payload(payload)
    except (OwnerTruthContractError, TypeError, ValueError) as exc:
        raise OwnerTruthInterviewCandidateProposalConflict(
            "stored candidate proposal authorization evidence is malformed"
        ) from exc
    if capture.feature != FORMAL_INTERVIEW_CANDIDATE_PROPOSAL_FEATURE:
        raise OwnerTruthInterviewCandidateProposalConflict(
            "stored candidate proposal authorization evidence has an unsupported feature"
        )
    return capture


__all__ = [
    "InMemoryOwnerTruthInterviewCandidateProposalRepository",
    "OwnerTruthInterviewCandidateProposalRepository",
    "OwnerTruthInterviewCandidateProposalService",
    "OwnerTruthInterviewCandidateProposalExtractionStatus",
    "OwnerTruthInterviewCandidateProposalStatus",
    "OwnerTruthInterviewCandidateProposalStatusService",
    "PostgresOwnerTruthInterviewCandidateProposalRepository",
]
