"""Private read composition for Candidates from an admitted interview batch.

This service intentionally does not reuse the Candidate decision writer.  It
only groups eligible pending Candidates so the later review UI can submit
explicit per-Candidate decisions with the existing CAS/DecisionReceipt flow.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
from threading import RLock
from typing import Any, Callable, ContextManager, Mapping, Protocol

from app.domain.owner_truth.candidate_decisions import OwnerTruthCandidateSnapshot
from app.domain.owner_truth.contracts import CandidateDecision, SensitivityLevel, require_uuid
from app.domain.owner_truth.interview_candidate_review import (
    InterviewCandidateReviewPath,
    InterviewCandidateReviewReadiness,
    OwnerTruthInterviewCandidateReviewAccessDenied,
    OwnerTruthInterviewReviewCandidateItem,
    OwnerTruthInterviewCandidateReviewComposition,
    OwnerTruthInterviewCandidateReviewConflict,
    OwnerTruthInterviewCandidateReviewSourceInactive,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_candidate_review import OwnerTruthCandidateInboxItem


def _assert_owner_context(context: OwnerTruthCommandContext) -> None:
    if not isinstance(context, OwnerTruthCommandContext):
        raise OwnerTruthInterviewCandidateReviewAccessDenied(
            "owner truth command context is required"
        )
    if context.actor_subject_id != context.owner_subject_id:
        raise OwnerTruthInterviewCandidateReviewAccessDenied(
            "only the Vault Owner may compose interview Candidates"
        )


def _normalized_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, Mapping):
        return {}
    return value


def _review_path(*, sensitivity: str, review_mode: str) -> InterviewCandidateReviewPath:
    """Fail closed: only standard explicitly-batch proposals join batch review."""

    if sensitivity == SensitivityLevel.STANDARD.value and review_mode == "batch":
        return InterviewCandidateReviewPath.BATCH
    return InterviewCandidateReviewPath.SINGLE


def _readiness(
    *,
    latest_extraction_status: str | None,
    has_candidates: bool,
) -> InterviewCandidateReviewReadiness:
    if has_candidates:
        return InterviewCandidateReviewReadiness.REVIEW_READY
    if latest_extraction_status == "succeeded":
        return InterviewCandidateReviewReadiness.NO_CANDIDATES
    if latest_extraction_status == "failed":
        return InterviewCandidateReviewReadiness.EXTRACTION_FAILED
    if latest_extraction_status == "quarantined":
        return InterviewCandidateReviewReadiness.EXTRACTION_QUARANTINED
    return InterviewCandidateReviewReadiness.AWAITING_EXTRACTION


@dataclass(frozen=True)
class _InMemoryAdmission:
    admission_id: str
    review_batch_id: str
    vault_id: str
    owner_subject_id: str
    source_id: str
    source_version: int
    authority_epoch: int
    review_batch_state: str
    source_state: str
    source_kind: str
    source_metadata: Mapping[str, Any]


@dataclass(frozen=True)
class _InMemoryExtraction:
    extraction_id: str
    vault_id: str
    source_id: str
    source_version: int
    status: str
    order: int


@dataclass(frozen=True)
class _InMemoryCandidate:
    snapshot: OwnerTruthCandidateSnapshot
    extraction_id: str
    source_version: int
    created_at: str | None


class OwnerTruthInterviewCandidateReviewStore(Protocol):
    def request_unit_of_work(
        self,
        *,
        correlation_id: str,
        command_id: str,
    ) -> ContextManager[Any]:
        ...

    def owner_truth_interview_candidate_review_repository(self) -> Any:
        ...


class InMemoryOwnerTruthInterviewCandidateReviewRepository:
    """Semantic double for the batch composition and its safety boundaries."""

    def __init__(
        self,
        *,
        candidate_snapshot_lookup: Callable[[str], OwnerTruthCandidateSnapshot | None] | None = None,
    ) -> None:
        self._lock = RLock()
        # The application in-memory store keeps terminal Candidate state in
        # the canonical review repository.  Reading through this callback
        # makes this composition double observe that state like Postgres does.
        self._candidate_snapshot_lookup = candidate_snapshot_lookup
        self._vaults: dict[str, tuple[str, str, int]] = {}
        self._admissions: dict[tuple[str, str], _InMemoryAdmission] = {}
        self._extractions: dict[str, _InMemoryExtraction] = {}
        self._candidates: dict[str, _InMemoryCandidate] = {}

    def seed_vault(
        self,
        *,
        vault_id: str,
        owner_subject_id: str,
        authority_epoch: int = 0,
        status: str = "active",
    ) -> None:
        self._vaults[vault_id] = (owner_subject_id, status, authority_epoch)

    def seed_admission(
        self,
        *,
        admission_id: str,
        review_batch_id: str,
        vault_id: str,
        owner_subject_id: str,
        source_id: str,
        source_version: int = 1,
        authority_epoch: int = 0,
        review_batch_state: str = "acknowledged",
        source_state: str = "active",
        source_kind: str = "conversation",
        source_metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self._admissions[(vault_id, review_batch_id)] = _InMemoryAdmission(
            admission_id=require_uuid(admission_id, field="admission_id"),
            review_batch_id=require_uuid(review_batch_id, field="review_batch_id"),
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            source_id=require_uuid(source_id, field="source_id"),
            source_version=source_version,
            authority_epoch=authority_epoch,
            review_batch_state=review_batch_state,
            source_state=source_state,
            source_kind=source_kind,
            source_metadata=dict(
                source_metadata
                or {
                    "origin": "interviewReviewBatchCandidateProposal",
                    "reviewBatchId": review_batch_id,
                }
            ),
        )

    def seed_extraction(
        self,
        *,
        extraction_id: str,
        vault_id: str,
        source_id: str,
        source_version: int,
        status: str,
        order: int = 1,
    ) -> None:
        self._extractions[require_uuid(extraction_id, field="extraction_id")] = _InMemoryExtraction(
            extraction_id=require_uuid(extraction_id, field="extraction_id"),
            vault_id=vault_id,
            source_id=require_uuid(source_id, field="source_id"),
            source_version=source_version,
            status=status,
            order=order,
        )

    def seed_candidate(
        self,
        *,
        candidate: OwnerTruthCandidateSnapshot,
        extraction_id: str,
        source_version: int,
        created_at: str | None = None,
    ) -> None:
        if not isinstance(candidate, OwnerTruthCandidateSnapshot):
            raise TypeError("candidate snapshot is required")
        self._candidates[candidate.candidate_id] = _InMemoryCandidate(
            snapshot=candidate,
            extraction_id=require_uuid(extraction_id, field="extraction_id"),
            source_version=source_version,
            created_at=created_at,
        )

    @contextmanager
    def transaction(self):
        with self._lock:
            yield

    def compose(
        self,
        *,
        review_batch_id: str,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthInterviewCandidateReviewComposition:
        _assert_owner_context(context)
        normalized_batch_id = require_uuid(review_batch_id, field="review_batch_id")
        with self._lock:
            vault = self._vaults.get(context.vault_id)
            if (
                vault is None
                or vault[0] != context.owner_subject_id
                or vault[1] != "active"
            ):
                raise OwnerTruthInterviewCandidateReviewAccessDenied(
                    "Vault is not active for this Owner"
                )
            admission = self._admissions.get((context.vault_id, normalized_batch_id))
            if admission is None or admission.owner_subject_id != context.owner_subject_id:
                raise OwnerTruthInterviewCandidateReviewAccessDenied(
                    "review batch has no admitted Candidate source in this Owner Vault"
                )
            self._assert_live_admission(admission=admission, vault_epoch=int(vault[2]))
            matching_extractions = tuple(
                extraction
                for extraction in self._extractions.values()
                if extraction.vault_id == context.vault_id
                and extraction.source_id == admission.source_id
                and extraction.source_version == admission.source_version
            )
            latest = max(matching_extractions, key=lambda item: (item.order, item.extraction_id), default=None)
            successful_ids = {
                item.extraction_id for item in matching_extractions if item.status == "succeeded"
            }
            items: list[OwnerTruthInterviewReviewCandidateItem] = []
            for stored in self._candidates.values():
                candidate = (
                    self._candidate_snapshot_lookup(stored.snapshot.candidate_id)
                    if self._candidate_snapshot_lookup is not None
                    else stored.snapshot
                )
                if candidate is None:
                    continue
                if (
                    stored.extraction_id not in successful_ids
                    or stored.source_version != admission.source_version
                    or candidate.vault_id != context.vault_id
                    or candidate.owner_subject_id != context.owner_subject_id
                    or candidate.source_id != admission.source_id
                    or candidate.authority_epoch != int(vault[2])
                    or candidate.decision is not CandidateDecision.PENDING
                ):
                    continue
                payload = _normalized_mapping(candidate.payload)
                review_mode = str(payload.get("reviewMode") or "single").strip() or "single"
                path = _review_path(
                    sensitivity=candidate.sensitivity.value,
                    review_mode=review_mode,
                )
                items.append(
                    OwnerTruthInterviewReviewCandidateItem(
                        candidate_id=candidate.candidate_id,
                        extraction_id=stored.extraction_id,
                        candidate_row_version=candidate.row_version,
                        candidate_kind=candidate.memory_kind.value,
                        sensitivity=candidate.sensitivity.value,
                        review_mode=review_mode,
                        review_path=path,
                        created_at=stored.created_at,
                    )
                )
        ordered = tuple(sorted(items, key=lambda item: item.candidate_id))
        batch_candidates = tuple(
            item for item in ordered if item.review_path is InterviewCandidateReviewPath.BATCH
        )
        single_candidates = tuple(
            item for item in ordered if item.review_path is InterviewCandidateReviewPath.SINGLE
        )
        latest_status = latest.status if latest is not None else None
        return OwnerTruthInterviewCandidateReviewComposition(
            review_batch_id=admission.review_batch_id,
            admission_id=admission.admission_id,
            source_id=admission.source_id,
            source_version=admission.source_version,
            authority_epoch=int(vault[2]),
            readiness=_readiness(
                latest_extraction_status=latest_status,
                has_candidates=bool(ordered),
            ),
            latest_extraction_status=latest_status,
            batch_candidates=batch_candidates,
            single_candidates=single_candidates,
        )

    @staticmethod
    def _assert_live_admission(*, admission: _InMemoryAdmission, vault_epoch: int) -> None:
        metadata = dict(admission.source_metadata)
        if admission.review_batch_state != "acknowledged":
            raise OwnerTruthInterviewCandidateReviewConflict(
                "review batch must remain acknowledged for Candidate composition"
            )
        if admission.source_state != "active":
            raise OwnerTruthInterviewCandidateReviewSourceInactive(
                "admitted Candidate Source is no longer active"
            )
        if (
            admission.authority_epoch != vault_epoch
            or admission.source_kind != "conversation"
            or metadata.get("origin") != "interviewReviewBatchCandidateProposal"
            or str(metadata.get("reviewBatchId") or "") != admission.review_batch_id
        ):
            raise OwnerTruthInterviewCandidateReviewConflict(
                "admitted Candidate Source provenance no longer matches the review batch"
            )


class PostgresOwnerTruthInterviewCandidateReviewRepository:
    """Read current pending Candidate grouping from immutable batch provenance."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def compose(
        self,
        *,
        review_batch_id: str,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthInterviewCandidateReviewComposition:
        _assert_owner_context(context)
        normalized_batch_id = require_uuid(review_batch_id, field="review_batch_id")
        with self._cursor() as cursor:
            admission = self._load_admission(
                cursor,
                review_batch_id=normalized_batch_id,
                context=context,
            )
            self._assert_live_admission(admission=admission, context=context)
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
                (context.vault_id, admission["source_id"], int(admission["source_version"])),
            )
            latest_extraction = cursor.fetchone()
            cursor.execute(
                """
                SELECT c.id, c.extraction_result_id, c.candidate_kind, c.sensitivity,
                    c.row_version, c.payload, c.created_at
                FROM owner_truth.memory_candidates AS c
                JOIN owner_truth.extraction_results AS e
                  ON e.vault_id = c.vault_id
                 AND e.id = c.extraction_result_id
                WHERE c.vault_id = %s
                  AND c.owner_subject_id = %s
                  AND c.source_id = %s
                  AND c.decision_status = 'pending'
                  AND c.authority_epoch = %s
                  AND e.source_id = %s
                  AND e.source_version = %s
                  AND e.status = 'succeeded'
                ORDER BY c.created_at ASC, c.id ASC
                """,
                (
                    context.vault_id,
                    context.owner_subject_id,
                    admission["source_id"],
                    int(admission["vault_authority_epoch"]),
                    admission["source_id"],
                    int(admission["source_version"]),
                ),
            )
            rows = cursor.fetchall()
        items = tuple(self._candidate_item_from_row(row) for row in rows)
        batch_candidates = tuple(
            item for item in items if item.review_path is InterviewCandidateReviewPath.BATCH
        )
        single_candidates = tuple(
            item for item in items if item.review_path is InterviewCandidateReviewPath.SINGLE
        )
        latest_status = (
            str(latest_extraction["status"]) if latest_extraction is not None else None
        )
        return OwnerTruthInterviewCandidateReviewComposition(
            review_batch_id=str(admission["review_batch_id"]),
            admission_id=str(admission["admission_id"]),
            source_id=str(admission["source_id"]),
            source_version=int(admission["source_version"]),
            authority_epoch=int(admission["vault_authority_epoch"]),
            readiness=_readiness(
                latest_extraction_status=latest_status,
                has_candidates=bool(items),
            ),
            latest_extraction_status=latest_status,
            batch_candidates=batch_candidates,
            single_candidates=single_candidates,
        )

    def _load_admission(
        self,
        cursor: Any,
        *,
        review_batch_id: str,
        context: OwnerTruthCommandContext,
    ) -> Mapping[str, Any]:
        cursor.execute(
            """
            SELECT a.id AS admission_id, a.review_batch_id, a.source_id,
                a.source_version, a.authority_epoch AS admission_authority_epoch,
                b.owner_subject_id AS batch_owner_subject_id,
                b.state AS batch_state,
                b.authority_epoch AS batch_authority_epoch,
                s.owner_subject_id AS source_owner_subject_id,
                s.state AS source_state,
                s.source_kind,
                s.authority_epoch AS source_authority_epoch,
                s.metadata AS source_metadata,
                v.owner_subject_id AS vault_owner_subject_id,
                v.status AS vault_status,
                v.authority_epoch AS vault_authority_epoch
            FROM owner_truth.interview_review_batch_candidate_admissions AS a
            JOIN owner_truth.interview_review_batches AS b
              ON b.vault_id = a.vault_id AND b.id = a.review_batch_id
            JOIN owner_truth.sources AS s
              ON s.vault_id = a.vault_id AND s.id = a.source_id
            JOIN owner_truth.vaults AS v ON v.vault_id = a.vault_id
            WHERE a.vault_id = %s AND a.review_batch_id = %s
            """,
            (context.vault_id, review_batch_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise OwnerTruthInterviewCandidateReviewAccessDenied(
                "review batch has no admitted Candidate source in this Owner Vault"
            )
        return row

    @staticmethod
    def _assert_live_admission(
        *,
        admission: Mapping[str, Any],
        context: OwnerTruthCommandContext,
    ) -> None:
        if (
            str(admission["vault_owner_subject_id"]) != context.owner_subject_id
            or str(admission["vault_status"]) != "active"
            or str(admission["batch_owner_subject_id"]) != context.owner_subject_id
            or str(admission["source_owner_subject_id"]) != context.owner_subject_id
        ):
            raise OwnerTruthInterviewCandidateReviewAccessDenied(
                "review batch does not belong to this active Owner Vault"
            )
        if str(admission["batch_state"]) != "acknowledged":
            raise OwnerTruthInterviewCandidateReviewConflict(
                "review batch must remain acknowledged for Candidate composition"
            )
        if str(admission["source_state"]) != "active":
            raise OwnerTruthInterviewCandidateReviewSourceInactive(
                "admitted Candidate Source is no longer active"
            )
        metadata = _normalized_mapping(admission["source_metadata"])
        authority_epoch = int(admission["vault_authority_epoch"])
        if (
            int(admission["admission_authority_epoch"]) != authority_epoch
            or int(admission["batch_authority_epoch"]) != authority_epoch
            or int(admission["source_authority_epoch"]) != authority_epoch
            or str(admission["source_kind"]) != "conversation"
            or metadata.get("origin") != "interviewReviewBatchCandidateProposal"
            or str(metadata.get("reviewBatchId") or "")
            != str(admission["review_batch_id"])
        ):
            raise OwnerTruthInterviewCandidateReviewConflict(
                "admitted Candidate Source provenance no longer matches the review batch"
            )

    @staticmethod
    def _candidate_item_from_row(row: Mapping[str, Any]) -> OwnerTruthInterviewReviewCandidateItem:
        payload = _normalized_mapping(row["payload"])
        review_mode = str(payload.get("reviewMode") or "single").strip() or "single"
        sensitivity = str(row["sensitivity"])
        return OwnerTruthInterviewReviewCandidateItem(
            candidate_id=str(row["id"]),
            extraction_id=str(row["extraction_result_id"]),
            candidate_row_version=int(row["row_version"]),
            candidate_kind=str(row["candidate_kind"]),
            sensitivity=sensitivity,
            review_mode=review_mode,
            review_path=_review_path(sensitivity=sensitivity, review_mode=review_mode),
            created_at=(row["created_at"].isoformat() if row.get("created_at") else None),
        )

    @contextmanager
    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - production dependency
            dict_row = None
        with self._connection.cursor(row_factory=dict_row) as cursor:
            yield cursor


class OwnerTruthInterviewCandidateReviewCompositionService:
    """Read only the current review grouping for one admitted private batch."""

    def __init__(self, store: OwnerTruthInterviewCandidateReviewStore):
        self._store = store

    def compose(
        self,
        *,
        review_batch_id: str,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthInterviewCandidateReviewComposition:
        _assert_owner_context(context)
        normalized_batch_id = require_uuid(review_batch_id, field="review_batch_id")
        with self._store.request_unit_of_work(
            correlation_id=(
                "owner-truth-interview-candidate-review-"
                f"{context.vault_id}:{normalized_batch_id}"
            ),
            command_id=f"read:{normalized_batch_id}",
        ):
            return self._store.owner_truth_interview_candidate_review_repository().compose(
                review_batch_id=normalized_batch_id,
                context=context,
            )


@dataclass(frozen=True)
class OwnerTruthInterviewCandidateReviewReadItem:
    """One owner-visible pending Candidate plus its immutable review path."""

    review_item: OwnerTruthInterviewReviewCandidateItem
    candidate: OwnerTruthCandidateInboxItem


@dataclass(frozen=True)
class OwnerTruthInterviewCandidateReviewReadResult:
    """One coherent review-batch read within a single unit of work."""

    composition: OwnerTruthInterviewCandidateReviewComposition
    batch_candidates: tuple[OwnerTruthInterviewCandidateReviewReadItem, ...]
    single_candidates: tuple[OwnerTruthInterviewCandidateReviewReadItem, ...]


class OwnerTruthInterviewCandidateReviewReadService:
    """Pair composition references with current owner-scoped Candidate previews.

    The composition intentionally stores only value-minimized metadata. This
    read service joins the existing Candidate inbox in the same UoW so a
    terminal Candidate can never be rendered as still awaiting review.
    """

    def __init__(self, store: Any):
        self._store = store

    def read(
        self,
        *,
        review_batch_id: str,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthInterviewCandidateReviewReadResult:
        _assert_owner_context(context)
        normalized_batch_id = require_uuid(review_batch_id, field="review_batch_id")
        with self._store.request_unit_of_work(
            correlation_id=(
                "owner-truth-interview-candidate-review-read-"
                f"{context.vault_id}:{normalized_batch_id}"
            ),
            command_id=f"read:{normalized_batch_id}",
        ):
            composition = self._store.owner_truth_interview_candidate_review_repository().compose(
                review_batch_id=normalized_batch_id,
                context=context,
            )
            pending_by_id = {
                item.candidate_id: item
                for item in self._store.owner_truth_candidate_review_repository().list_pending(
                    context=context
                )
            }
            return OwnerTruthInterviewCandidateReviewReadResult(
                composition=composition,
                batch_candidates=self._pair_items(
                    composition.batch_candidates,
                    pending_by_id=pending_by_id,
                ),
                single_candidates=self._pair_items(
                    composition.single_candidates,
                    pending_by_id=pending_by_id,
                ),
            )

    @staticmethod
    def _pair_items(
        review_items: tuple[OwnerTruthInterviewReviewCandidateItem, ...],
        *,
        pending_by_id: Mapping[str, OwnerTruthCandidateInboxItem],
    ) -> tuple[OwnerTruthInterviewCandidateReviewReadItem, ...]:
        paired: list[OwnerTruthInterviewCandidateReviewReadItem] = []
        for review_item in review_items:
            candidate = pending_by_id.get(review_item.candidate_id)
            if candidate is None:
                raise OwnerTruthInterviewCandidateReviewConflict(
                    "review composition Candidate is no longer pending"
                )
            if candidate.candidate_row_version != review_item.candidate_row_version:
                raise OwnerTruthInterviewCandidateReviewConflict(
                    "review composition Candidate version is stale"
                )
            paired.append(
                OwnerTruthInterviewCandidateReviewReadItem(
                    review_item=review_item,
                    candidate=candidate,
                )
            )
        return tuple(paired)


__all__ = [
    "InMemoryOwnerTruthInterviewCandidateReviewRepository",
    "OwnerTruthInterviewCandidateReviewCompositionService",
    "OwnerTruthInterviewCandidateReviewReadItem",
    "OwnerTruthInterviewCandidateReviewReadResult",
    "OwnerTruthInterviewCandidateReviewReadService",
    "OwnerTruthInterviewCandidateReviewStore",
    "PostgresOwnerTruthInterviewCandidateReviewRepository",
]
