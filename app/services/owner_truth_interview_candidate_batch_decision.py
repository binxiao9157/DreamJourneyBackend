"""Private partial batch acceptance over admitted interview Candidate proposals.

The service is a composition boundary, not a second Candidate authority model:
it reuses the existing per-Candidate decision/CAS/DecisionReceipt repository.
It never calls ``activate_memory_version`` and has no public HTTP route.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from threading import RLock
from typing import Any, ContextManager, Mapping, Protocol

from app.domain.owner_truth.candidate_decisions import (
    OwnerTruthCandidateReviewAccessDenied,
    OwnerTruthCandidateVersionConflict,
)
from app.domain.owner_truth.interview_candidate_batch_decision import (
    OwnerTruthInterviewCandidateBatchAcceptCommand,
    OwnerTruthInterviewCandidateBatchDecisionConflict,
    OwnerTruthInterviewCandidateBatchDecisionNotReady,
    OwnerTruthInterviewCandidateBatchDecisionSingleReviewRequired,
)
from app.domain.owner_truth.interview_candidate_review import (
    InterviewCandidateReviewPath,
    InterviewCandidateReviewReadiness,
    OwnerTruthInterviewCandidateReviewComposition,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_candidate_review import OwnerTruthCandidateReviewResult


@dataclass(frozen=True)
class OwnerTruthInterviewCandidateBatchDecisionLedgerRecord:
    batch_decision_id: str
    review_batch_id: str
    command_id_hash: str
    payload_hash: str
    owner_subject_id: str
    authority_epoch: int
    selection_count: int


@dataclass(frozen=True)
class OwnerTruthInterviewCandidateBatchAcceptResult:
    outcome: str
    batch_decision_id: str
    review_batch_id: str
    candidate_results: tuple[OwnerTruthCandidateReviewResult, ...]

    @property
    def accepted_candidate_count(self) -> int:
        return len(self.candidate_results)

    def public_summary(self) -> dict[str, object]:
        """Return only command/receipt diagnostics, never Candidate payloads."""

        return {
            "schemaVersion": "owner-truth-interview-candidate-batch-accept-result-v1",
            "outcome": self.outcome,
            "reviewBatchId": self.review_batch_id,
            "acceptedCandidateCount": self.accepted_candidate_count,
            "receiptCount": len(self.candidate_results),
        }


class OwnerTruthInterviewCandidateBatchDecisionStore(Protocol):
    def request_unit_of_work(
        self,
        *,
        correlation_id: str,
        command_id: str,
    ) -> ContextManager[Any]:
        ...

    def owner_truth_interview_candidate_review_repository(self) -> Any:
        ...

    def owner_truth_candidate_review_repository(self) -> Any:
        ...

    def owner_truth_interview_candidate_batch_decision_repository(self) -> Any:
        ...


class OwnerTruthInterviewCandidateDecisionLedgerCommand(Protocol):
    """Minimal root-command shape persisted by the review-batch ledger."""

    review_batch_id: str
    command_id_hash: str
    payload_hash: str
    selection_count: int

    def batch_decision_id(self, *, vault_id: str) -> str:
        ...


def _assert_owner_context(context: OwnerTruthCommandContext) -> None:
    if not isinstance(context, OwnerTruthCommandContext):
        raise OwnerTruthInterviewCandidateBatchDecisionConflict(
            "owner truth command context is required"
        )
    if context.actor_subject_id != context.owner_subject_id:
        raise OwnerTruthCandidateReviewAccessDenied(
            "only the Vault Owner may accept an interview Candidate batch"
        )


class InMemoryOwnerTruthInterviewCandidateBatchDecisionRepository:
    """Semantic double for root-command replay and immutable command meaning."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._records: dict[tuple[str, str], OwnerTruthInterviewCandidateBatchDecisionLedgerRecord] = {}

    @contextmanager
    def transaction(self):
        with self._lock:
            yield

    def claim(
        self,
        *,
        command: OwnerTruthInterviewCandidateDecisionLedgerCommand,
        context: OwnerTruthCommandContext,
        authority_epoch: int,
    ) -> tuple[str, OwnerTruthInterviewCandidateBatchDecisionLedgerRecord]:
        key = (context.vault_id, command.command_id_hash)
        expected = OwnerTruthInterviewCandidateBatchDecisionLedgerRecord(
            batch_decision_id=command.batch_decision_id(vault_id=context.vault_id),
            review_batch_id=command.review_batch_id,
            command_id_hash=command.command_id_hash,
            payload_hash=command.payload_hash,
            owner_subject_id=context.owner_subject_id,
            authority_epoch=authority_epoch,
            selection_count=command.selection_count,
        )
        with self._lock:
            existing = self._records.get(key)
            if existing is None:
                self._records[key] = expected
                return "created", expected
            _assert_ledger_replay(existing=existing, expected=expected)
            return "deduplicated", existing

    def lookup(
        self,
        *,
        command: OwnerTruthInterviewCandidateDecisionLedgerCommand,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthInterviewCandidateBatchDecisionLedgerRecord | None:
        with self._lock:
            existing = self._records.get((context.vault_id, command.command_id_hash))
        if existing is None:
            return None
        _assert_ledger_command_identity(
            existing=existing,
            command=command,
            context=context,
        )
        return existing

    def snapshot(self) -> dict[str, OwnerTruthInterviewCandidateBatchDecisionLedgerRecord]:
        with self._lock:
            return {
                f"{vault_id}:{command_hash}": record
                for (vault_id, command_hash), record in self._records.items()
            }


class PostgresOwnerTruthInterviewCandidateBatchDecisionRepository:
    """Persist value-minimized root command idempotency in one active UoW."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def claim(
        self,
        *,
        command: OwnerTruthInterviewCandidateDecisionLedgerCommand,
        context: OwnerTruthCommandContext,
        authority_epoch: int,
    ) -> tuple[str, OwnerTruthInterviewCandidateBatchDecisionLedgerRecord]:
        expected = OwnerTruthInterviewCandidateBatchDecisionLedgerRecord(
            batch_decision_id=command.batch_decision_id(vault_id=context.vault_id),
            review_batch_id=command.review_batch_id,
            command_id_hash=command.command_id_hash,
            payload_hash=command.payload_hash,
            owner_subject_id=context.owner_subject_id,
            authority_epoch=authority_epoch,
            selection_count=command.selection_count,
        )
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT owner_subject_id, authority_epoch, status
                FROM owner_truth.vaults
                WHERE vault_id = %s
                FOR UPDATE
                """,
                (context.vault_id,),
            )
            vault = cursor.fetchone()
            if (
                vault is None
                or str(vault["owner_subject_id"]) != context.owner_subject_id
                or str(vault["status"]) != "active"
            ):
                raise OwnerTruthCandidateReviewAccessDenied(
                    "interview batch decision does not belong to this active Owner Vault"
                )
            if int(vault["authority_epoch"]) != authority_epoch:
                raise OwnerTruthInterviewCandidateBatchDecisionConflict(
                    "interview batch decision authority epoch is stale"
                )
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked",
                (
                    f"owner-truth-interview-batch-decision:{context.vault_id}:{command.command_id_hash}",
                ),
            )
            cursor.execute(
                """
                SELECT id, review_batch_id, command_id_hash, payload_hash,
                       owner_subject_id, authority_epoch, selection_count
                FROM owner_truth.interview_review_batch_candidate_decisions
                WHERE vault_id = %s AND command_id_hash = %s
                FOR UPDATE
                """,
                (context.vault_id, command.command_id_hash),
            )
            row = cursor.fetchone()
            if row is not None:
                existing = OwnerTruthInterviewCandidateBatchDecisionLedgerRecord(
                    batch_decision_id=str(row["id"]),
                    review_batch_id=str(row["review_batch_id"]),
                    command_id_hash=str(row["command_id_hash"]),
                    payload_hash=str(row["payload_hash"]),
                    owner_subject_id=str(row["owner_subject_id"]),
                    authority_epoch=int(row["authority_epoch"]),
                    selection_count=int(row["selection_count"]),
                )
                _assert_ledger_replay(existing=existing, expected=expected)
                return "deduplicated", existing
            cursor.execute(
                """
                INSERT INTO owner_truth.interview_review_batch_candidate_decisions (
                    id, vault_id, owner_subject_id, review_batch_id,
                    command_id_hash, payload_hash, selection_count,
                    actor_subject_id, policy_version, authority_epoch
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    expected.batch_decision_id,
                    context.vault_id,
                    expected.owner_subject_id,
                    expected.review_batch_id,
                    expected.command_id_hash,
                    expected.payload_hash,
                    expected.selection_count,
                    context.actor_subject_id,
                    context.policy_version,
                    expected.authority_epoch,
                ),
            )
        return "created", expected

    def lookup(
        self,
        *,
        command: OwnerTruthInterviewCandidateDecisionLedgerCommand,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthInterviewCandidateBatchDecisionLedgerRecord | None:
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT id, review_batch_id, command_id_hash, payload_hash,
                       owner_subject_id, authority_epoch, selection_count
                FROM owner_truth.interview_review_batch_candidate_decisions
                WHERE vault_id = %s AND command_id_hash = %s
                """,
                (context.vault_id, command.command_id_hash),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        existing = OwnerTruthInterviewCandidateBatchDecisionLedgerRecord(
            batch_decision_id=str(row["id"]),
            review_batch_id=str(row["review_batch_id"]),
            command_id_hash=str(row["command_id_hash"]),
            payload_hash=str(row["payload_hash"]),
            owner_subject_id=str(row["owner_subject_id"]),
            authority_epoch=int(row["authority_epoch"]),
            selection_count=int(row["selection_count"]),
        )
        _assert_ledger_command_identity(
            existing=existing,
            command=command,
            context=context,
        )
        return existing

    @contextmanager
    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - production dependency
            dict_row = None
        with self._connection.cursor(row_factory=dict_row) as cursor:
            yield cursor


def _assert_ledger_replay(
    *,
    existing: OwnerTruthInterviewCandidateBatchDecisionLedgerRecord,
    expected: OwnerTruthInterviewCandidateBatchDecisionLedgerRecord,
) -> None:
    if (
        existing.batch_decision_id != expected.batch_decision_id
        or existing.review_batch_id != expected.review_batch_id
        or existing.payload_hash != expected.payload_hash
        or existing.owner_subject_id != expected.owner_subject_id
        or existing.authority_epoch != expected.authority_epoch
        or existing.selection_count != expected.selection_count
    ):
        raise OwnerTruthInterviewCandidateBatchDecisionConflict(
            "commandId cannot be reused with a different interview batch decision"
        )


def _assert_ledger_command_identity(
    *,
    existing: OwnerTruthInterviewCandidateBatchDecisionLedgerRecord,
    command: OwnerTruthInterviewCandidateDecisionLedgerCommand,
    context: OwnerTruthCommandContext,
) -> None:
    if (
        existing.review_batch_id != command.review_batch_id
        or existing.command_id_hash != command.command_id_hash
        or existing.payload_hash != command.payload_hash
        or existing.owner_subject_id != context.owner_subject_id
        or existing.selection_count != command.selection_count
    ):
        raise OwnerTruthInterviewCandidateBatchDecisionConflict(
            "commandId cannot be reused with a different interview batch decision"
        )


class OwnerTruthInterviewCandidateBatchDecisionService:
    """Accept selected ordinary Candidates while preserving all other paths."""

    def __init__(self, store: OwnerTruthInterviewCandidateBatchDecisionStore):
        self._store = store

    def accept_selected(
        self,
        *,
        command: OwnerTruthInterviewCandidateBatchAcceptCommand,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthInterviewCandidateBatchAcceptResult:
        _assert_owner_context(context)
        with self._store.request_unit_of_work(
            correlation_id=(
                "owner-truth-interview-candidate-batch-accept-"
                f"{context.vault_id}:{command.command_id_hash}"
            ),
            command_id=command.command_id_hash,
        ):
            ledger = self._store.owner_truth_interview_candidate_batch_decision_repository()
            existing = self._lookup_existing(
                ledger=ledger,
                command=command,
                context=context,
            )
            if existing is None:
                composition = self._store.owner_truth_interview_candidate_review_repository().compose(
                    review_batch_id=command.review_batch_id,
                    context=context,
                )
                self._assert_selected_batch_candidates(
                    composition=composition,
                    command=command,
                )
                authority_epoch = self._authority_epoch(composition=composition)
            else:
                authority_epoch = existing.authority_epoch
            outcome, record = ledger.claim(
                command=command,
                context=context,
                authority_epoch=authority_epoch,
            )
            review_repository = self._store.owner_truth_candidate_review_repository()
            results = tuple(
                review_repository.decide(
                    command=command.child_command(selection=selection),
                    context=context,
                )
                for selection in command.selections
            )
        return OwnerTruthInterviewCandidateBatchAcceptResult(
            outcome=outcome,
            batch_decision_id=record.batch_decision_id,
            review_batch_id=record.review_batch_id,
            candidate_results=results,
        )

    @staticmethod
    def _lookup_existing(
        *,
        ledger: Any,
        command: OwnerTruthInterviewCandidateBatchAcceptCommand,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthInterviewCandidateBatchDecisionLedgerRecord | None:
        lookup = getattr(ledger, "lookup", None)
        if not callable(lookup):
            return None
        return lookup(command=command, context=context)

    @staticmethod
    def _authority_epoch(
        *,
        composition: OwnerTruthInterviewCandidateReviewComposition,
    ) -> int:
        # Candidate review performs the authoritative vault/source epoch check
        # immediately before each terminal decision. The command ledger stores
        # the same epoch supplied by the checked composition.
        return composition.authority_epoch

    @staticmethod
    def _assert_selected_batch_candidates(
        *,
        composition: OwnerTruthInterviewCandidateReviewComposition,
        command: OwnerTruthInterviewCandidateBatchAcceptCommand,
    ) -> None:
        if composition.readiness is not InterviewCandidateReviewReadiness.REVIEW_READY:
            raise OwnerTruthInterviewCandidateBatchDecisionNotReady(
                "interview Candidate extraction is not ready for batch acceptance"
            )
        batch_candidates = {
            item.candidate_id: item
            for item in composition.batch_candidates
        }
        single_candidates = {
            item.candidate_id: item
            for item in composition.single_candidates
        }
        for selection in command.selections:
            item = batch_candidates.get(selection.candidate_id)
            if item is None:
                if selection.candidate_id in single_candidates:
                    raise OwnerTruthInterviewCandidateBatchDecisionSingleReviewRequired(
                        "selected Candidate requires individual review"
                    )
                raise OwnerTruthInterviewCandidateBatchDecisionConflict(
                    "selected Candidate is not pending in this batch review composition"
                )
            if item.review_path is not InterviewCandidateReviewPath.BATCH:
                raise OwnerTruthInterviewCandidateBatchDecisionSingleReviewRequired(
                    "selected Candidate requires individual review"
                )
            if item.candidate_row_version != selection.expected_candidate_version:
                raise OwnerTruthCandidateVersionConflict(
                    expected_version=selection.expected_candidate_version,
                    current_version=item.candidate_row_version,
                )


__all__ = [
    "InMemoryOwnerTruthInterviewCandidateBatchDecisionRepository",
    "OwnerTruthInterviewCandidateBatchAcceptResult",
    "OwnerTruthInterviewCandidateBatchDecisionLedgerRecord",
    "OwnerTruthInterviewCandidateBatchDecisionService",
    "OwnerTruthInterviewCandidateDecisionLedgerCommand",
    "OwnerTruthInterviewCandidateBatchDecisionStore",
    "PostgresOwnerTruthInterviewCandidateBatchDecisionRepository",
]
