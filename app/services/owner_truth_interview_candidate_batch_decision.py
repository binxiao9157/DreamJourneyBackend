"""Private partial batch acceptance over admitted interview Candidate proposals.

The service is a composition boundary, not a second Candidate authority model:
it reuses the existing per-Candidate decision/CAS/DecisionReceipt repository.
It never calls ``activate_memory_version`` and has no public HTTP route.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
from threading import RLock
from typing import Any, ContextManager, Mapping, Protocol

from app.domain.owner_truth.candidate_decisions import (
    OwnerTruthCandidateReviewAccessDenied,
    OwnerTruthCandidateVersionConflict,
)
from app.domain.owner_truth.contracts import OwnerTruthContractError
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
from app.domain.owner_truth.source_commands import (
    OwnerTruthCommandAuthorizationCapture,
    OwnerTruthCommandContext,
)
from app.services.owner_truth_candidate_review import OwnerTruthCandidateReviewResult


FORMAL_INTERVIEW_CANDIDATE_REVIEW_FEATURE = "ownerTruthCandidateReview"


@dataclass(frozen=True)
class OwnerTruthInterviewCandidateBatchDecisionLedgerRecord:
    batch_decision_id: str
    review_batch_id: str
    command_id_hash: str
    payload_hash: str
    owner_subject_id: str
    authority_epoch: int
    selection_count: int
    authorization_capture: OwnerTruthCommandAuthorizationCapture | None = None


@dataclass(frozen=True)
class OwnerTruthInterviewCandidateBatchDecisionReceiptLink:
    """One immutable association between a root command and a terminal receipt."""

    vault_id: str
    batch_decision_id: str
    receipt_id: str
    candidate_id: str
    candidate_command_id_hash: str


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


def _assert_formal_confirmation_authorization_capture(
    context: OwnerTruthCommandContext,
) -> None:
    """Bind a formal batch-confirmation root to its one release-policy feature.

    Empty capture remains the legacy QA-only path. Any populated capture is a
    formal confirmation receipt and cannot be borrowed from another feature.
    """

    capture = context.authorization_capture
    if (
        capture is not None
        and capture.feature != FORMAL_INTERVIEW_CANDIDATE_REVIEW_FEATURE
    ):
        raise OwnerTruthInterviewCandidateBatchDecisionConflict(
            "formal interview Candidate confirmation requires ownerTruthCandidateReview authorization"
        )


class InMemoryOwnerTruthInterviewCandidateBatchDecisionRepository:
    """Semantic double for root-command replay and immutable command meaning."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._records: dict[tuple[str, str], OwnerTruthInterviewCandidateBatchDecisionLedgerRecord] = {}
        self._receipt_links: dict[
            tuple[str, str], OwnerTruthInterviewCandidateBatchDecisionReceiptLink
        ] = {}

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
            authorization_capture=context.authorization_capture,
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

    def link_receipts(
        self,
        *,
        record: OwnerTruthInterviewCandidateBatchDecisionLedgerRecord,
        candidate_results: tuple[OwnerTruthCandidateReviewResult, ...],
        candidate_command_id_hashes: Mapping[str, str],
        context: OwnerTruthCommandContext,
    ) -> None:
        expected_links = _receipt_links_for_results(
            record=record,
            candidate_results=candidate_results,
            candidate_command_id_hashes=candidate_command_id_hashes,
            context=context,
        )
        with self._lock:
            for link in expected_links:
                key = (link.vault_id, link.receipt_id)
                existing = self._receipt_links.get(key)
                if existing is None:
                    self._receipt_links[key] = link
                elif existing != link:
                    raise OwnerTruthInterviewCandidateBatchDecisionConflict(
                        "DecisionReceipt cannot be linked to a different root command"
                    )

    def receipt_links_snapshot(
        self,
    ) -> dict[str, OwnerTruthInterviewCandidateBatchDecisionReceiptLink]:
        with self._lock:
            return {
                f"{vault_id}:{receipt_id}": link
                for (vault_id, receipt_id), link in self._receipt_links.items()
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
            authorization_capture=context.authorization_capture,
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
                       owner_subject_id, authority_epoch, selection_count,
                       authorization_evidence
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
                    authorization_capture=_authorization_capture_from_database(
                        row.get("authorization_evidence")
                    ),
                )
                _assert_ledger_replay(existing=existing, expected=expected)
                return "deduplicated", existing
            cursor.execute(
                """
                INSERT INTO owner_truth.interview_review_batch_candidate_decisions (
                    id, vault_id, owner_subject_id, review_batch_id,
                    command_id_hash, payload_hash, selection_count,
                    actor_subject_id, policy_version, authority_epoch,
                    authorization_evidence
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                    _authorization_capture_json(expected.authorization_capture),
                ),
            )
        return "created", expected

    def link_receipts(
        self,
        *,
        record: OwnerTruthInterviewCandidateBatchDecisionLedgerRecord,
        candidate_results: tuple[OwnerTruthCandidateReviewResult, ...],
        candidate_command_id_hashes: Mapping[str, str],
        context: OwnerTruthCommandContext,
    ) -> None:
        expected_links = _receipt_links_for_results(
            record=record,
            candidate_results=candidate_results,
            candidate_command_id_hashes=candidate_command_id_hashes,
            context=context,
        )
        with self._cursor() as cursor:
            for link in expected_links:
                cursor.execute(
                    """
                    INSERT INTO owner_truth.interview_review_batch_candidate_decision_receipts (
                        vault_id, batch_decision_id, decision_receipt_id, candidate_id,
                        candidate_command_id_hash
                    ) VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (vault_id, decision_receipt_id) DO NOTHING
                    RETURNING batch_decision_id, candidate_id
                    """,
                    (
                        link.vault_id,
                        link.batch_decision_id,
                        link.receipt_id,
                        link.candidate_id,
                        link.candidate_command_id_hash,
                    ),
                )
                inserted = cursor.fetchone()
                if inserted is not None:
                    continue
                cursor.execute(
                    """
                    SELECT batch_decision_id, candidate_id
                    FROM owner_truth.interview_review_batch_candidate_decision_receipts
                    WHERE vault_id = %s AND decision_receipt_id = %s
                    FOR UPDATE
                    """,
                    (link.vault_id, link.receipt_id),
                )
                existing = cursor.fetchone()
                if (
                    existing is None
                    or str(existing["batch_decision_id"]) != link.batch_decision_id
                    or str(existing["candidate_id"]) != link.candidate_id
                ):
                    raise OwnerTruthInterviewCandidateBatchDecisionConflict(
                        "DecisionReceipt cannot be linked to a different root command"
                    )

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
                       owner_subject_id, authority_epoch, selection_count,
                       authorization_evidence
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
            authorization_capture=_authorization_capture_from_database(
                row.get("authorization_evidence")
            ),
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
            raise OwnerTruthInterviewCandidateBatchDecisionConflict(
                "stored authorization evidence is not valid JSON"
            ) from exc
    if not isinstance(payload, Mapping):
        raise OwnerTruthInterviewCandidateBatchDecisionConflict(
            "stored authorization evidence must be an object"
        )
    try:
        return OwnerTruthCommandAuthorizationCapture.from_value_minimized_payload(payload)
    except OwnerTruthContractError as exc:
        raise OwnerTruthInterviewCandidateBatchDecisionConflict(
            "stored authorization evidence is malformed"
        ) from exc


def _receipt_links_for_results(
    *,
    record: OwnerTruthInterviewCandidateBatchDecisionLedgerRecord,
    candidate_results: tuple[OwnerTruthCandidateReviewResult, ...],
    candidate_command_id_hashes: Mapping[str, str],
    context: OwnerTruthCommandContext,
) -> tuple[OwnerTruthInterviewCandidateBatchDecisionReceiptLink, ...]:
    if not candidate_results:
        raise OwnerTruthInterviewCandidateBatchDecisionConflict(
            "root command must link at least one DecisionReceipt"
        )
    if record.owner_subject_id != context.owner_subject_id:
        raise OwnerTruthInterviewCandidateBatchDecisionConflict(
            "root command does not belong to this Owner"
        )
    result_candidate_ids = {result.candidate_id for result in candidate_results}
    if set(candidate_command_id_hashes) != result_candidate_ids:
        raise OwnerTruthInterviewCandidateBatchDecisionConflict(
            "root command receipt links must bind exactly the selected Candidates"
        )
    links: list[OwnerTruthInterviewCandidateBatchDecisionReceiptLink] = []
    receipt_ids: set[str] = set()
    candidate_ids: set[str] = set()
    for result in candidate_results:
        if result.receipt_id in receipt_ids or result.candidate_id in candidate_ids:
            raise OwnerTruthInterviewCandidateBatchDecisionConflict(
                "root command cannot link duplicate Candidate receipts"
            )
        receipt_ids.add(result.receipt_id)
        candidate_ids.add(result.candidate_id)
        candidate_command_id_hash = str(
            candidate_command_id_hashes.get(result.candidate_id) or ""
        ).strip().lower()
        if len(candidate_command_id_hash) != 64 or any(
            character not in "0123456789abcdef" for character in candidate_command_id_hash
        ):
            raise OwnerTruthInterviewCandidateBatchDecisionConflict(
                "root command receipt link requires a SHA-256 child command hash"
            )
        links.append(
            OwnerTruthInterviewCandidateBatchDecisionReceiptLink(
                vault_id=context.vault_id,
                batch_decision_id=record.batch_decision_id,
                receipt_id=result.receipt_id,
                candidate_id=result.candidate_id,
                candidate_command_id_hash=candidate_command_id_hash,
            )
        )
    return tuple(links)


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
    _assert_replay_authorization_capture(existing=existing, expected=expected)


def _assert_replay_authorization_capture(
    *,
    existing: OwnerTruthInterviewCandidateBatchDecisionLedgerRecord,
    expected: OwnerTruthInterviewCandidateBatchDecisionLedgerRecord,
) -> None:
    """Keep QA-only roots and formal release-policy roots non-interchangeable.

    A later formal retry may carry a new policy decision ID or expiry, so those
    fields are intentionally not compared. The immutable root only needs to
    prove that it was originally formally authorized for the same feature.
    """

    existing_capture = existing.authorization_capture
    expected_capture = expected.authorization_capture
    if (existing_capture is None) != (expected_capture is None):
        raise OwnerTruthInterviewCandidateBatchDecisionConflict(
            "commandId cannot replay between QA-only and formally authorized confirmation"
        )
    if (
        existing_capture is not None
        and expected_capture is not None
        and existing_capture.feature != expected_capture.feature
    ):
        raise OwnerTruthInterviewCandidateBatchDecisionConflict(
            "commandId cannot replay under a different authorization feature"
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
    _assert_replay_authorization_capture(
        existing=existing,
        expected=OwnerTruthInterviewCandidateBatchDecisionLedgerRecord(
            batch_decision_id=command.batch_decision_id(vault_id=context.vault_id),
            review_batch_id=command.review_batch_id,
            command_id_hash=command.command_id_hash,
            payload_hash=command.payload_hash,
            owner_subject_id=context.owner_subject_id,
            authority_epoch=existing.authority_epoch,
            selection_count=command.selection_count,
            authorization_capture=context.authorization_capture,
        ),
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
        _assert_formal_confirmation_authorization_capture(context)
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
            child_commands = tuple(
                command.child_command(selection=selection) for selection in command.selections
            )
            results = tuple(
                review_repository.decide(command=child_command, context=context)
                for child_command in child_commands
            )
            ledger.link_receipts(
                record=record,
                candidate_results=results,
                candidate_command_id_hashes={
                    child_command.candidate_id: child_command.command_id_hash
                    for child_command in child_commands
                },
                context=context,
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
    "OwnerTruthInterviewCandidateBatchDecisionReceiptLink",
    "OwnerTruthInterviewCandidateBatchDecisionService",
    "OwnerTruthInterviewCandidateDecisionLedgerCommand",
    "OwnerTruthInterviewCandidateBatchDecisionStore",
    "PostgresOwnerTruthInterviewCandidateBatchDecisionRepository",
]
