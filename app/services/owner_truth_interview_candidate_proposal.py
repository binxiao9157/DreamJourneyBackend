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
from typing import Any, ContextManager, Mapping, Protocol

from app.async_effects.contracts import EffectReceiptSummary
from app.domain.owner_truth.contracts import SourceKind
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
    OwnerTruthCommandContext,
    OwnerTruthSourceCommandResult,
    OwnerTruthSourceWriteRecord,
)
from app.services.owner_truth_source import build_source_created_effect_intent


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


def _assert_owner_context(context: OwnerTruthCommandContext) -> None:
    if not isinstance(context, OwnerTruthCommandContext):
        raise OwnerTruthInterviewCandidateProposalAccessDenied(
            "owner truth command context is required"
        )
    if context.actor_subject_id != context.owner_subject_id:
        raise OwnerTruthInterviewCandidateProposalAccessDenied(
            "only the Vault Owner may admit an interview review batch"
        )


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


class InMemoryOwnerTruthInterviewCandidateProposalRepository:
    """G0 semantic double for acknowledged-batch admission tests."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._batches: dict[tuple[str, str], _InMemoryReviewBatch] = {}
        self._admissions_by_command: dict[tuple[str, str], dict[str, Any]] = {}
        self._admissions_by_batch: dict[tuple[str, str], dict[str, Any]] = {}

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
            }
            self._admissions_by_command[(record.vault_id, record.command_id_hash)] = item
            self._admissions_by_batch[(record.vault_id, record.review_batch_id)] = item
            return self._result_from_item(item, outcome="created")

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {
                "admissionsByBatch": deepcopy(self._admissions_by_batch),
                "admissionsByCommand": deepcopy(self._admissions_by_command),
            }

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
                    actor_subject_id, policy_version, authority_epoch,
                    owner_message_count, first_message_sequence, last_message_sequence
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
    def _existing_by_command(
        cursor: Any,
        *,
        record: OwnerTruthInterviewCandidateProposalWriteRecord,
    ) -> Mapping[str, Any] | None:
        cursor.execute(
            """
            SELECT id, review_batch_id, source_id, source_version,
                source_content_hash, effect_operation_id, owner_message_count,
                command_id_hash, payload_hash, actor_subject_id, policy_version
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
                command_id_hash, payload_hash, actor_subject_id, policy_version
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


__all__ = [
    "InMemoryOwnerTruthInterviewCandidateProposalRepository",
    "OwnerTruthInterviewCandidateProposalRepository",
    "OwnerTruthInterviewCandidateProposalService",
    "PostgresOwnerTruthInterviewCandidateProposalRepository",
]
