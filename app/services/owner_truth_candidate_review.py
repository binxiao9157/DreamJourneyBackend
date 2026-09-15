"""Candidate Inbox and Owner-only terminal DecisionReceipt application service."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
from threading import RLock
from typing import Any, Callable, ContextManager, Mapping, Protocol
from uuid import UUID, uuid5

from app.async_effects.contracts import AsyncEffectIntent, EffectReceiptSummary
from app.domain.owner_truth.candidate_decisions import (
    OwnerTruthCandidateDecisionWriteRecord,
    OwnerTruthCandidateReviewAccessDenied,
    OwnerTruthCandidateReviewCommand,
    OwnerTruthCandidateReviewConflict,
    OwnerTruthCandidateReviewError,
    OwnerTruthCandidateReviewSourceInactive,
    OwnerTruthCandidateSnapshot,
    OwnerTruthCandidateVersionConflict,
)
from app.domain.owner_truth.contracts import CandidateDecision
from app.domain.owner_truth.contracts import OwnerTruthContractError
from app.domain.owner_truth.memory_activation import (
    OwnerTruthMemoryActivationError,
    OwnerTruthMemoryActivationResult,
    build_memory_activation_plan,
)
from app.domain.owner_truth.memory_changeset import (
    OwnerTruthCurrentFormalMemory,
    OwnerTruthMemoryChangeSet,
    OwnerTruthMemoryChangeSetProposal,
    build_memory_changeset_proposal,
)
from app.domain.owner_truth.memory_changeset_activation import (
    OwnerTruthMemoryChangeSetActivationError,
    build_memory_changeset_activation_plan,
)
from app.domain.owner_truth.memory_correction import (
    OwnerTruthMemoryCorrectionActivationResult,
    OwnerTruthMemoryCorrectionError,
    OwnerTruthMemoryCorrectionPlan,
    OwnerTruthMemoryVersionSnapshot,
    build_memory_correction_plan,
)
from app.domain.owner_truth.memory_projection import (
    OwnerTruthMemoryProjectionError,
    OwnerTruthMemoryProjectionInput,
)
from app.domain.owner_truth.ontology import OWNER_TRUTH_SCHEMA_VERSION_V5
from app.domain.owner_truth.source_commands import (
    OwnerTruthCommandAuthorizationCapture,
    OwnerTruthCommandContext,
)
from app.services.owner_truth_memory_projection_effects import (
    build_memory_projection_rebuild_effect_intent,
)


_MEMORY_CHANGESET_RELATION_NAMESPACE = UUID("0355e56b-1d6e-49f8-8b83-3d82c8a0791f")


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _reason_hash(reason_code: str) -> str:
    return sha256(reason_code.encode("utf-8")).hexdigest()


def _candidate_at_review_version(
    candidate: OwnerTruthCandidateSnapshot,
    *,
    expected_candidate_version: Any,
) -> OwnerTruthCandidateSnapshot:
    """Recover the immutable Candidate row version that an Owner reviewed.

    Terminal review increments the Candidate row only to fence a second
    decision. It must not change the identity of the pre-review ChangeSet that
    was shown to the Owner. The receipt stores the version used by that review,
    so activation recomputes its proposal against that immutable identity.
    """

    try:
        row_version = int(expected_candidate_version)
    except (TypeError, ValueError) as exc:
        raise OwnerTruthCandidateReviewConflict(
            "DecisionReceipt is missing its reviewed Candidate version"
        ) from exc
    if row_version < 1 or row_version != candidate.row_version - 1:
        raise OwnerTruthCandidateReviewConflict(
            "DecisionReceipt reviewed Candidate version is invalid"
        )
    return replace(candidate, row_version=row_version)


def _receipt_changeset_binding_matches(
    receipt: Mapping[str, Any],
    *,
    change_set_id: str,
    proposal_hash: str,
) -> bool:
    """Compare database UUID values with their canonical domain strings."""

    return (
        str(receipt.get("expected_change_set_id") or "") == change_set_id
        and str(receipt.get("expected_proposal_hash") or "") == proposal_hash
    )


def _authorization_capture_payload(
    capture: OwnerTruthCommandAuthorizationCapture | None,
) -> dict[str, Any]:
    return {} if capture is None else capture.value_minimized_payload()


def _authorization_capture_from_value(
    value: Any,
) -> OwnerTruthCommandAuthorizationCapture | None:
    if value is None or value == {}:
        return None
    payload = value
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise OwnerTruthCandidateReviewConflict(
                "stored authorization evidence is malformed"
            ) from exc
    if not isinstance(payload, Mapping):
        raise OwnerTruthCandidateReviewConflict(
            "stored authorization evidence is malformed"
        )
    try:
        capture = OwnerTruthCommandAuthorizationCapture.from_value_minimized_payload(payload)
    except (OwnerTruthContractError, TypeError, ValueError) as exc:
        raise OwnerTruthCandidateReviewConflict(
            "stored authorization evidence is malformed"
        ) from exc
    if capture.feature != "ownerTruthCandidateReview":
        raise OwnerTruthCandidateReviewConflict(
            "stored authorization evidence has an unsupported feature"
        )
    return capture


def _assert_replay_authorization_capture(
    *,
    existing: OwnerTruthCommandAuthorizationCapture | None,
    expected: OwnerTruthCommandAuthorizationCapture | None,
) -> None:
    """Keep legacy QA replays separate from formally authorized replays."""

    if (existing is None) != (expected is None):
        raise OwnerTruthCandidateReviewConflict(
            "commandId cannot replay between QA-only and formally authorized Candidate review"
        )
    if (
        existing is not None
        and expected is not None
        and existing.feature != expected.feature
    ):
        raise OwnerTruthCandidateReviewConflict(
            "commandId cannot replay under a different authorization feature"
        )


@dataclass(frozen=True)
class OwnerTruthCandidateInboxItem:
    candidate_id: str
    source_id: str
    memory_kind: str
    perspective_type: str
    epistemic_status: str
    sensitivity: str
    content_schema_version: str
    content: Mapping[str, Any]
    content_hash: str
    source_refs: tuple[Mapping[str, Any], ...]
    review_mode: str
    candidate_row_version: int
    created_at: str | None = None
    proposed_change_set: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class OwnerTruthCandidateChangeSetPreview:
    """One proposal plus the source and correction values used to build it."""

    proposal: OwnerTruthMemoryChangeSetProposal | None
    source_candidate_version: int
    source_candidate_content_hash: str
    submitted_corrected_value: Mapping[str, Any] | None = None
    submitted_corrected_value_schema_version: str | None = None

    def correction_binding_payload(self) -> Mapping[str, Any] | None:
        if self.submitted_corrected_value is None:
            return None
        if self.submitted_corrected_value_schema_version is None or self.proposal is None:
            raise OwnerTruthCandidateReviewConflict(
                "correction preview binding is incomplete"
            )
        return {
            "schemaVersion": "owner-truth-candidate-correction-binding-v1",
            "sourceCandidateVersion": self.source_candidate_version,
            "sourceCandidateContentHash": self.source_candidate_content_hash,
            "submittedCorrection": {
                "correctedValueSchemaVersion": self.submitted_corrected_value_schema_version,
                "correctedValue": deepcopy(dict(self.submitted_corrected_value)),
            },
            "resolvedContentHash": self.proposal.candidate_content_hash,
        }


@dataclass(frozen=True)
class OwnerTruthCandidateReviewHistoryItem:
    candidate: OwnerTruthCandidateInboxItem
    decision: CandidateDecision
    decided_at: str
    memory_activation_status: str
    memory_id: str | None
    memory_version_id: str | None
    memory_version: int | None


@dataclass(frozen=True)
class OwnerTruthMemoryVersionHistoryItem:
    version_number: int
    status: str
    decision: CandidateDecision
    content_schema_version: str
    content: Mapping[str, Any]
    source_count: int
    created_at: str


@dataclass(frozen=True)
class OwnerTruthMemoryVersionHistory:
    memory_kind: str
    perspective_type: str
    epistemic_status: str
    sensitivity: str
    memory_status: str
    versions: tuple[OwnerTruthMemoryVersionHistoryItem, ...]


@dataclass(frozen=True)
class OwnerTruthCandidateReviewResult:
    outcome: str
    receipt_id: str
    candidate_id: str
    decision: CandidateDecision
    candidate_row_version: int
    candidate_before_hash: str
    candidate_after_hash: str
    corrected_value_id: str | None


@dataclass(frozen=True)
class OwnerTruthCandidateDecisionActivationResult:
    """One Owner decision plus its receipt-derived MemoryVersion outcome."""

    review: OwnerTruthCandidateReviewResult
    memory_activation: OwnerTruthMemoryActivationResult
    projection_effect: EffectReceiptSummary | None = None
    memory_revision: int | None = None


@dataclass(frozen=True)
class OwnerTruthCandidateDecisionLookupResult:
    """Read-only observation of one immutable review command result."""

    result: str
    expected_candidate_version: int | None = None
    candidate_before_hash: str | None = None
    expected_change_set_id: str | None = None
    expected_proposal_hash: str | None = None
    decision_result: OwnerTruthCandidateDecisionActivationResult | None = None


class OwnerTruthCandidateReviewStore(Protocol):
    def owner_truth_candidate_review_repository(self) -> Any:
        ...


def _assert_owner_context(context: OwnerTruthCommandContext) -> None:
    if not isinstance(context, OwnerTruthCommandContext):
        raise OwnerTruthCandidateReviewError("owner truth command context is required")
    if context.actor_subject_id != context.owner_subject_id:
        raise OwnerTruthCandidateReviewAccessDenied(
            "only the Vault Owner may review a Candidate"
        )


def _assert_generic_activation_allowed(candidate: OwnerTruthCandidateSnapshot) -> None:
    """Keep correction candidates out of the initial-Memory activation path.

    A correction Candidate points at an already authoritative MemoryVersion.
    Reusing the generic decision-and-activate flow would create a second
    MemoryRecord instead of superseding that version, so it must wait for the
    dedicated correction resolver.
    """

    if str(candidate.payload.get("reviewMode") or "") == "correction":
        raise OwnerTruthCandidateReviewConflict(
            "correction Candidate requires the correction-specific resolver"
        )


def _inbox_item(
    candidate: OwnerTruthCandidateSnapshot,
    *,
    created_at: str | None = None,
    proposed_change_set: Mapping[str, Any] | None = None,
) -> OwnerTruthCandidateInboxItem:
    payload = dict(candidate.payload)
    return OwnerTruthCandidateInboxItem(
        candidate_id=candidate.candidate_id,
        source_id=candidate.source_id,
        memory_kind=candidate.memory_kind.value,
        perspective_type=candidate.perspective_type.value,
        epistemic_status=candidate.epistemic_status.value,
        sensitivity=candidate.sensitivity.value,
        content_schema_version=candidate.content_schema_version,
        content=deepcopy(candidate.content),
        content_hash=candidate.content_hash,
        source_refs=tuple(deepcopy(item) for item in candidate.source_refs),
        review_mode=str(payload.get("reviewMode") or "single"),
        candidate_row_version=candidate.row_version,
        created_at=created_at,
        proposed_change_set=(
            deepcopy(dict(proposed_change_set))
            if isinstance(proposed_change_set, Mapping)
            else None
        ),
    )


def _review_history_item(
    *,
    candidate: OwnerTruthCandidateSnapshot,
    created_at: str | None,
    decided_at: str,
    activation: Mapping[str, Any] | None,
) -> OwnerTruthCandidateReviewHistoryItem:
    if candidate.decision in {CandidateDecision.REJECTED, CandidateDecision.INVALIDATED}:
        activation_status = "notApplicable"
        memory_id = None
        memory_version_id = None
        memory_version = None
    elif activation is None:
        activation_status = "pending"
        memory_id = None
        memory_version_id = None
        memory_version = None
    elif activation.get("isActivationAuditOnly") is True:
        # A reviewed duplicate or a non-personal statement still has an
        # immutable ChangeSet receipt, but must not masquerade as a current
        # formal-memory version in the Owner's review history.
        change_set = activation.get("changeSet")
        change_set_operation = (
            change_set.get("operation") if isinstance(change_set, Mapping) else None
        )
        operation = str(activation.get("operation") or change_set_operation or "").strip()
        activation_status = "deduplicated" if operation == "duplicate" else "notApplicable"
        memory_id = str(activation.get("memoryId") or "").strip() or None
        memory_version_id = str(activation.get("memoryVersionId") or "").strip() or None
        try:
            memory_version = int(activation.get("memoryVersion"))
        except (TypeError, ValueError):
            memory_version = None
    else:
        activation_status = "current" if activation.get("isCurrent") is True else "superseded"
        memory_id = str(activation.get("memoryId") or "").strip() or None
        memory_version_id = str(activation.get("memoryVersionId") or "").strip() or None
        try:
            memory_version = int(activation.get("memoryVersion"))
        except (TypeError, ValueError):
            memory_version = None
        if memory_id is None or memory_version_id is None or not memory_version or memory_version < 1:
            raise OwnerTruthCandidateReviewConflict(
                "Candidate review history has an incomplete MemoryVersion activation"
            )
    return OwnerTruthCandidateReviewHistoryItem(
        candidate=_inbox_item(candidate, created_at=created_at),
        decision=candidate.decision,
        decided_at=str(decided_at),
        memory_activation_status=activation_status,
        memory_id=memory_id,
        memory_version_id=memory_version_id,
        memory_version=memory_version,
    )


def _memory_version_history_item(
    *,
    version_number: Any,
    is_current: Any,
    decision: Any,
    schema_version: Any,
    payload: Any,
    created_at: Any,
) -> OwnerTruthMemoryVersionHistoryItem:
    try:
        normalized_version = int(version_number)
    except (TypeError, ValueError) as exc:
        raise OwnerTruthCandidateReviewConflict(
            "MemoryVersion history has an invalid version number"
        ) from exc
    try:
        normalized_decision = (
            decision if isinstance(decision, CandidateDecision) else CandidateDecision(str(decision))
        )
    except ValueError as exc:
        raise OwnerTruthCandidateReviewConflict(
            "MemoryVersion history has an invalid decision"
        ) from exc
    if normalized_version < 1 or normalized_decision not in {
        CandidateDecision.ACCEPTED,
        CandidateDecision.CORRECTED,
    }:
        raise OwnerTruthCandidateReviewConflict(
            "MemoryVersion history is not backed by an activating decision"
        )
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise OwnerTruthCandidateReviewConflict(
                "MemoryVersion history payload is malformed"
            ) from exc
    if not isinstance(payload, Mapping):
        raise OwnerTruthCandidateReviewConflict(
            "MemoryVersion history payload is unavailable"
        )
    content = payload.get("content")
    evidence_refs = payload.get("evidenceRefs")
    normalized_schema = str(
        payload.get("contentSchemaVersion") or schema_version or ""
    ).strip()
    normalized_created_at = (
        created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at or "").strip()
    )
    if (
        not isinstance(content, Mapping)
        or not normalized_schema
        or not isinstance(evidence_refs, list)
        or not evidence_refs
        or not normalized_created_at
    ):
        raise OwnerTruthCandidateReviewConflict(
            "MemoryVersion history is missing Owner-facing content or provenance"
        )
    source_refs: set[tuple[str, int]] = set()
    for item in evidence_refs:
        if not isinstance(item, Mapping):
            continue
        source_id = str(item.get("sourceId") or "").strip()
        try:
            source_version = int(item.get("sourceVersion") or 0)
        except (TypeError, ValueError):
            continue
        if source_id and source_version > 0:
            source_refs.add((source_id, source_version))
    if not source_refs:
        raise OwnerTruthCandidateReviewConflict(
            "MemoryVersion history has no valid source provenance"
        )
    return OwnerTruthMemoryVersionHistoryItem(
        version_number=normalized_version,
        status="current" if is_current is True else "superseded",
        decision=normalized_decision,
        content_schema_version=normalized_schema,
        content=deepcopy(dict(content)),
        source_count=len(source_refs),
        created_at=normalized_created_at,
    )


class InMemoryOwnerTruthCandidateReviewRepository:
    """Thread-safe semantic double for command/CAS/receipt behavior."""

    def __init__(
        self,
        *,
        memory_projection_rebuild_runnable_reader: Callable[[AsyncEffectIntent], bool]
        | None = None,
    ) -> None:
        self._lock = RLock()
        self._candidates: dict[str, OwnerTruthCandidateSnapshot] = {}
        self._candidate_created_at: dict[str, str | None] = {}
        self._candidate_decided_at: dict[str, str] = {}
        self._source_states: dict[tuple[str, str], str] = {}
        self._vault_states: dict[str, tuple[str, str, int]] = {}
        self._receipts: dict[str, dict[str, Any]] = {}
        self._candidate_receipts: dict[str, str] = {}
        self._corrected_values: dict[str, dict[str, Any]] = {}
        self._memory_activations: dict[str, dict[str, Any]] = {}
        self._memory_revisions: dict[str, int] = {}
        self._memory_changesets: dict[str, dict[str, Any]] = {}
        self._memory_changeset_proposals: dict[str, dict[str, Any]] = {}
        self._memory_changeset_group_proposals: dict[str, dict[str, Any]] = {}
        self._memory_changeset_group_receipts: dict[str, dict[str, Any]] = {}
        self._memory_projection_rebuild_runnable_reader = (
            memory_projection_rebuild_runnable_reader
        )

    def seed(
        self,
        candidate: OwnerTruthCandidateSnapshot,
        *,
        source_state: str = "active",
        vault_status: str = "active",
        created_at: str | None = None,
    ) -> None:
        if not isinstance(candidate, OwnerTruthCandidateSnapshot):
            raise TypeError("candidate snapshot is required")
        with self._lock:
            if candidate.candidate_id in self._candidates:
                raise OwnerTruthCandidateReviewConflict("candidate seed already exists")
            self._candidates[candidate.candidate_id] = candidate
            self._candidate_created_at[candidate.candidate_id] = created_at
            self._source_states[(candidate.vault_id, candidate.source_id)] = source_state
            self._vault_states[candidate.vault_id] = (
                candidate.owner_subject_id,
                vault_status,
                candidate.authority_epoch,
            )
            self._memory_revisions.setdefault(candidate.vault_id, 0)

    @contextmanager
    def transaction(self):
        with self._lock:
            # The semantic double must model the all-or-nothing boundary used
            # by the real PostgreSQL Unit of Work.  A stale formal revision
            # cannot leave an accepted Candidate without its matching receipt
            # activation merely because an in-memory test has no database.
            before = {
                "candidates": deepcopy(self._candidates),
                "candidateDecidedAt": deepcopy(self._candidate_decided_at),
                "candidateReceipts": deepcopy(self._candidate_receipts),
                "correctedValues": deepcopy(self._corrected_values),
                "memoryActivations": deepcopy(self._memory_activations),
                "memoryChangesets": deepcopy(self._memory_changesets),
                "memoryChangesetProposals": deepcopy(self._memory_changeset_proposals),
                "memoryChangeSetGroupProposals": deepcopy(self._memory_changeset_group_proposals),
                "memoryChangeSetGroupReceipts": deepcopy(self._memory_changeset_group_receipts),
                "memoryRevisions": deepcopy(self._memory_revisions),
                "receipts": deepcopy(self._receipts),
            }
            try:
                yield
            except Exception:
                self._candidates = before["candidates"]
                self._candidate_decided_at = before["candidateDecidedAt"]
                self._candidate_receipts = before["candidateReceipts"]
                self._corrected_values = before["correctedValues"]
                self._memory_activations = before["memoryActivations"]
                self._memory_changesets = before["memoryChangesets"]
                self._memory_changeset_proposals = before["memoryChangesetProposals"]
                self._memory_changeset_group_proposals = before["memoryChangeSetGroupProposals"]
                self._memory_changeset_group_receipts = before["memoryChangeSetGroupReceipts"]
                self._memory_revisions = before["memoryRevisions"]
                self._receipts = before["receipts"]
                raise

    def list_pending(self, *, context: OwnerTruthCommandContext) -> tuple[OwnerTruthCandidateInboxItem, ...]:
        _assert_owner_context(context)
        with self._lock:
            vault = self._vault_states.get(context.vault_id)
            if vault is None or vault[0] != context.owner_subject_id or vault[1] != "active":
                raise OwnerTruthCandidateReviewAccessDenied("Vault is not active for this Owner")
            current_memories = self._current_formal_memories(context=context)
            base_memory_revision = int(self._memory_revisions.get(context.vault_id, 0))
            items = []
            for candidate in self._candidates.values():
                if (
                    candidate.vault_id != context.vault_id
                    or candidate.owner_subject_id != context.owner_subject_id
                    or candidate.decision is not CandidateDecision.PENDING
                    or candidate.authority_epoch != vault[2]
                    or self._source_states.get((candidate.vault_id, candidate.source_id)) != "active"
                ):
                    continue
                proposal = self._propose_changeset(
                    candidate=candidate,
                    current_memories=current_memories,
                    base_memory_revision=base_memory_revision,
                )
                items.append(
                    _inbox_item(
                        candidate,
                        created_at=self._candidate_created_at.get(candidate.candidate_id),
                        proposed_change_set=(proposal.payload() if proposal is not None else None),
                    )
                )
        return tuple(sorted(items, key=lambda item: item.candidate_id))

    def preview_changeset(
        self,
        *,
        candidate_id: str,
        context: OwnerTruthCommandContext,
        corrected_value: Mapping[str, Any] | None = None,
        corrected_value_schema_version: str | None = None,
    ) -> OwnerTruthMemoryChangeSetProposal | None:
        return self.preview_changeset_result(
            candidate_id=candidate_id,
            context=context,
            corrected_value=corrected_value,
            corrected_value_schema_version=corrected_value_schema_version,
        ).proposal

    def preview_changeset_result(
        self,
        *,
        candidate_id: str,
        context: OwnerTruthCommandContext,
        corrected_value: Mapping[str, Any] | None = None,
        corrected_value_schema_version: str | None = None,
    ) -> OwnerTruthCandidateChangeSetPreview:
        """Create the exact Owner-visible V5 diff before a terminal decision.

        This read path does not mutate the processor-owned Candidate. A
        correction is validated through the same canonical content path used
        by the later DecisionReceipt, so its preview cannot be bound to a
        different owner value.
        """

        _assert_owner_context(context)
        with self._lock:
            candidate = self._candidates.get(str(candidate_id or ""))
            if candidate is None:
                raise OwnerTruthCandidateReviewAccessDenied(
                    "Candidate does not exist in this Vault"
                )
            self._assert_live_target(candidate=candidate, context=context)
            if candidate.decision is not CandidateDecision.PENDING:
                raise OwnerTruthCandidateReviewConflict(
                    "terminal Candidate cannot receive a ChangeSet preview"
                )
            proposal = self._propose_changeset(
                candidate=candidate,
                current_memories=self._current_formal_memories(context=context),
                base_memory_revision=int(self._memory_revisions.get(context.vault_id, 0)),
                resolved_content=corrected_value,
                resolved_content_schema_version=corrected_value_schema_version,
            )
            return OwnerTruthCandidateChangeSetPreview(
                proposal=proposal,
                source_candidate_version=candidate.row_version,
                source_candidate_content_hash=candidate.content_hash,
                submitted_corrected_value=(
                    deepcopy(dict(corrected_value))
                    if corrected_value is not None
                    else None
                ),
                submitted_corrected_value_schema_version=corrected_value_schema_version,
            )

    def list_review_history(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> tuple[OwnerTruthCandidateReviewHistoryItem, ...]:
        _assert_owner_context(context)
        with self._lock:
            vault = self._vault_states.get(context.vault_id)
            if vault is None or vault[0] != context.owner_subject_id or vault[1] != "active":
                raise OwnerTruthCandidateReviewAccessDenied("Vault is not active for this Owner")
            receipts_by_id = {
                str(receipt.get("id") or ""): receipt
                for receipt in self._receipts.values()
            }
            history: list[OwnerTruthCandidateReviewHistoryItem] = []
            for candidate in self._candidates.values():
                if (
                    candidate.vault_id != context.vault_id
                    or candidate.owner_subject_id != context.owner_subject_id
                    or not candidate.decision.is_terminal
                ):
                    continue
                receipt_id = self._candidate_receipts.get(candidate.candidate_id)
                receipt = receipts_by_id.get(str(receipt_id or ""))
                decided_at = self._candidate_decided_at.get(candidate.candidate_id)
                if receipt is None or decided_at is None:
                    raise OwnerTruthCandidateReviewConflict(
                        "terminal Candidate is missing its review audit record"
                    )
                activation = self._memory_activations.get(str(receipt_id))
                history.append(
                    _review_history_item(
                        candidate=candidate,
                        created_at=self._candidate_created_at.get(candidate.candidate_id),
                        decided_at=decided_at,
                        activation=activation,
                    )
                )
        return tuple(
            sorted(
                history,
                key=lambda item: (item.decided_at, item.candidate.candidate_id),
                reverse=True,
            )
        )

    def list_memory_version_history(
        self,
        *,
        memory_id: str,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthMemoryVersionHistory:
        _assert_owner_context(context)
        with self._lock:
            vault = self._vault_states.get(context.vault_id)
            if vault is None or vault[0] != context.owner_subject_id or vault[1] != "active":
                raise OwnerTruthCandidateReviewAccessDenied("Vault is not active for this Owner")
            records = [
                record
                for record in self._memory_activations.values()
                if str(record.get("memoryId") or "") == memory_id
                and record.get("isActivationAuditOnly") is not True
            ]
            if not records:
                raise OwnerTruthCandidateReviewAccessDenied(
                    "Memory does not exist in this Owner Vault"
                )
            candidates: list[OwnerTruthCandidateSnapshot] = []
            versions: list[OwnerTruthMemoryVersionHistoryItem] = []
            for record in records:
                candidate = self._candidates.get(str(record.get("candidateId") or ""))
                if (
                    candidate is None
                    or candidate.vault_id != context.vault_id
                    or candidate.owner_subject_id != context.owner_subject_id
                ):
                    raise OwnerTruthCandidateReviewAccessDenied(
                        "Memory does not belong to this Owner Vault"
                    )
                candidates.append(candidate)
                versions.append(
                    _memory_version_history_item(
                        version_number=record.get("memoryVersion"),
                        is_current=record.get("isCurrent"),
                        decision=candidate.decision,
                        schema_version=(record.get("payload") or {}).get(
                            "contentSchemaVersion"
                        )
                        if isinstance(record.get("payload"), Mapping)
                        else None,
                        payload=record.get("payload"),
                        created_at=record.get("createdAt"),
                    )
                )
            initial_candidate = min(
                zip(records, candidates),
                key=lambda value: int(value[0].get("memoryVersion") or 0),
            )[1]
        ordered_versions = tuple(
            sorted(versions, key=lambda item: item.version_number, reverse=True)
        )
        if sum(item.status == "current" for item in ordered_versions) != 1:
            raise OwnerTruthCandidateReviewConflict(
                "MemoryVersion history must contain exactly one current version"
            )
        return OwnerTruthMemoryVersionHistory(
            memory_kind=initial_candidate.memory_kind.value,
            perspective_type=initial_candidate.perspective_type.value,
            epistemic_status=initial_candidate.epistemic_status.value,
            sensitivity=initial_candidate.sensitivity.value,
            memory_status="active",
            versions=ordered_versions,
        )

    def assert_active_owner_vault(self, *, context: OwnerTruthCommandContext) -> None:
        """Prove an active owner/vault boundary without loading Candidate content."""

        _assert_owner_context(context)
        with self._lock:
            vault = self._vault_states.get(context.vault_id)
            if (
                vault is None
                or vault[0] != context.owner_subject_id
                or vault[1] != "active"
            ):
                raise OwnerTruthCandidateReviewAccessDenied(
                    "Vault is not active for this Owner"
                )

    def is_memory_activation_pending(
        self,
        candidate_id: str,
        receipt_id: str,
        context: OwnerTruthCommandContext,
    ) -> bool:
        """Return only whether one terminal receipt can still activate memory.

        This is an internal, value-free bridge for the formal activation inbox.
        It deliberately returns no Candidate, receipt, or MemoryVersion data.
        """

        _assert_owner_context(context)
        with self._lock:
            candidate = self._candidates.get(candidate_id)
            receipt = next(
                (
                    item
                    for item in self._receipts.values()
                    if str(item.get("id") or "") == receipt_id
                ),
                None,
            )
            if (
                candidate is None
                or receipt is None
                or candidate.vault_id != context.vault_id
                or candidate.owner_subject_id != context.owner_subject_id
                or candidate.decision
                not in {CandidateDecision.ACCEPTED, CandidateDecision.CORRECTED}
                or self._candidate_receipts.get(candidate_id) != receipt_id
                or str(receipt.get("candidateId") or "") != candidate_id
                or str(receipt.get("decision") or "") != candidate.decision.value
                or receipt_id in self._memory_activations
            ):
                return False
            try:
                self._assert_live_target(candidate=candidate, context=context)
            except (
                OwnerTruthCandidateReviewAccessDenied,
                OwnerTruthCandidateReviewSourceInactive,
            ):
                return False
            return True

    def is_memory_projection_recovery_pending(
        self,
        candidate_id: str,
        receipt_id: str,
        context: OwnerTruthCommandContext,
    ) -> bool:
        """Return whether one live formal activation lacks a ready projection.

        This is the in-memory semantic-double bridge for the formal Projection
        recovery inbox. It returns no Candidate, receipt, MemoryVersion, or
        Projection data; the caller still checks the global Projection state.
        """

        _assert_owner_context(context)
        with self._lock:
            candidate = self._candidates.get(candidate_id)
            receipt = next(
                (
                    item
                    for item in self._receipts.values()
                    if str(item.get("id") or "") == receipt_id
                ),
                None,
            )
            activation = self._memory_activations.get(receipt_id)
            if (
                candidate is None
                or receipt is None
                or activation is None
                or candidate.vault_id != context.vault_id
                or candidate.owner_subject_id != context.owner_subject_id
                or candidate.decision
                not in {CandidateDecision.ACCEPTED, CandidateDecision.CORRECTED}
                or self._candidate_receipts.get(candidate_id) != receipt_id
                or str(receipt.get("candidateId") or "") != candidate_id
                or str(receipt.get("decision") or "") != candidate.decision.value
                or str(activation.get("candidateId") or "") != candidate_id
                or activation.get("isCurrent") is not True
            ):
                return False
            try:
                self._assert_live_target(candidate=candidate, context=context)
            except (
                OwnerTruthCandidateReviewAccessDenied,
                OwnerTruthCandidateReviewSourceInactive,
            ):
                return False
            reader = self._memory_projection_rebuild_runnable_reader
            if reader is None:
                return False
            try:
                intent = build_memory_projection_rebuild_effect_intent(
                    context=context,
                    activation=OwnerTruthMemoryActivationResult(
                        outcome="deduplicated",
                        receipt_id=receipt_id,
                        candidate_id=candidate_id,
                        decision=candidate.decision,
                        memory_id=str(activation.get("memoryId") or ""),
                        memory_version_id=str(activation.get("memoryVersionId") or ""),
                        memory_version=int(activation.get("memoryVersion") or 0),
                        authority_epoch=int(activation.get("authorityEpoch") or 0),
                        content_hash=str(activation.get("contentHash") or ""),
                    ),
                )
            except (OwnerTruthMemoryActivationError, TypeError, ValueError):
                return False
            return reader(intent)

    def decide(
        self,
        *,
        command: OwnerTruthCandidateReviewCommand,
        context: OwnerTruthCommandContext,
        allow_correction: bool = False,
    ) -> OwnerTruthCandidateReviewResult:
        _assert_owner_context(context)
        with self._lock:
            existing = self._receipts.get(command.command_id_hash)
            if existing is not None:
                self._assert_existing_command(existing, command=command, context=context)
                candidate = self._candidates.get(str(existing["candidateId"]))
                if candidate is None or candidate.decision.value != existing["decision"]:
                    raise OwnerTruthCandidateReviewConflict(
                        "decision receipt does not match its terminal Candidate"
                    )
                return OwnerTruthCandidateReviewResult(
                    outcome="deduplicated",
                    receipt_id=str(existing["id"]),
                    candidate_id=candidate.candidate_id,
                    decision=candidate.decision,
                    candidate_row_version=candidate.row_version,
                    candidate_before_hash=str(existing["candidateBeforeHash"]),
                    candidate_after_hash=str(existing["candidateAfterHash"]),
                    corrected_value_id=existing.get("correctedValueId"),
                )

            candidate = self._candidates.get(command.candidate_id)
            if candidate is None:
                raise OwnerTruthCandidateReviewAccessDenied("Candidate does not exist in this Vault")
            self._assert_live_target(candidate=candidate, context=context)
            if not allow_correction:
                _assert_generic_activation_allowed(candidate)
            record = command.write_record(candidate=candidate, context=context)
            self._assert_proposal_binding(
                candidate=candidate,
                command=command,
                record=record,
                context=context,
            )
            existing_receipt_id = self._candidate_receipts.get(candidate.candidate_id)
            if existing_receipt_id is not None or candidate.decision is not CandidateDecision.PENDING:
                raise OwnerTruthCandidateReviewConflict("terminal Candidate cannot receive a new decision")

            decided = replace(
                candidate,
                decision=record.decision,
                row_version=candidate.row_version + 1,
            )
            receipt = {
                "id": record.receipt_id,
                "candidateAfterHash": record.candidate_after_hash,
                "candidateBeforeHash": record.candidate_before_hash,
                "candidateId": record.candidate_id,
                "commandIdHash": record.command_id_hash,
                "correctedValueId": record.corrected_value_id,
                "decision": record.decision.value,
                "expectedCandidateVersion": record.expected_candidate_version,
                "payloadHash": record.payload_hash,
                "actorSubjectId": record.actor_subject_id,
                "policyVersion": record.policy_version,
                "authorizationCapture": _authorization_capture_payload(
                    record.authorization_capture
                ),
                "expectedChangeSetId": record.expected_change_set_id,
                "expectedProposalHash": record.expected_proposal_hash,
            }
            self._candidates[candidate.candidate_id] = decided
            self._candidate_decided_at[candidate.candidate_id] = datetime.now(
                timezone.utc
            ).isoformat()
            self._receipts[record.command_id_hash] = receipt
            self._candidate_receipts[candidate.candidate_id] = record.receipt_id
            if record.corrected_value is not None:
                self._corrected_values[record.corrected_value_id or ""] = {
                    "candidateId": record.candidate_id,
                    "content": deepcopy(dict(record.corrected_value)),
                    "contentHash": record.candidate_after_hash,
                    "contentSchemaVersion": record.corrected_value_schema_version,
                    "decisionReceiptId": record.receipt_id,
                    "id": record.corrected_value_id,
                }
            return OwnerTruthCandidateReviewResult(
                outcome="created",
                receipt_id=record.receipt_id,
                candidate_id=record.candidate_id,
                decision=record.decision,
                candidate_row_version=decided.row_version,
                candidate_before_hash=record.candidate_before_hash,
                candidate_after_hash=record.candidate_after_hash,
                corrected_value_id=record.corrected_value_id,
            )

    def lookup_decision_result(
        self,
        *,
        candidate_id: str,
        command_id_hash: str,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthCandidateDecisionLookupResult:
        """Observe a durable receipt without replaying the review command."""

        _assert_owner_context(context)
        with self._lock:
            self.assert_active_owner_vault(context=context)
            candidate = self._candidates.get(candidate_id)
            if (
                candidate is None
                or candidate.vault_id != context.vault_id
                or candidate.owner_subject_id != context.owner_subject_id
            ):
                raise OwnerTruthCandidateReviewAccessDenied(
                    "Candidate does not exist in this Owner Vault"
                )
            receipt = self._receipts.get(command_id_hash)
            if receipt is None or str(receipt.get("candidateId") or "") != candidate_id:
                return OwnerTruthCandidateDecisionLookupResult(result="notObserved")
            if str(receipt.get("actorSubjectId") or "") != context.owner_subject_id:
                raise OwnerTruthCandidateReviewAccessDenied(
                    "DecisionReceipt does not belong to this Owner Vault"
                )

            activation_record = self._memory_activations.get(str(receipt["id"]))
            if candidate.decision in {
                CandidateDecision.REJECTED,
                CandidateDecision.INVALIDATED,
            }:
                activation = OwnerTruthMemoryActivationResult(
                    outcome="notApplicable",
                    receipt_id=str(receipt["id"]),
                    candidate_id=candidate.candidate_id,
                    decision=candidate.decision,
                    memory_id=None,
                    memory_version_id=None,
                    memory_version=None,
                    authority_epoch=None,
                    content_hash=None,
                )
            elif activation_record is not None:
                activation = self._activation_result_from_record(
                    record=activation_record,
                    outcome=str(activation_record.get("activationOutcome") or "created"),
                    receipt_id=str(receipt["id"]),
                    candidate=candidate,
                )
            else:
                raise OwnerTruthCandidateReviewConflict(
                    "persisted DecisionReceipt is missing its MemoryVersion result"
                )

            corrected_value_id = receipt.get("correctedValueId")
            review = OwnerTruthCandidateReviewResult(
                outcome="created",
                receipt_id=str(receipt["id"]),
                candidate_id=candidate.candidate_id,
                decision=candidate.decision,
                candidate_row_version=candidate.row_version,
                candidate_before_hash=str(receipt["candidateBeforeHash"]),
                candidate_after_hash=str(receipt["candidateAfterHash"]),
                corrected_value_id=(
                    str(corrected_value_id) if corrected_value_id is not None else None
                ),
            )
            return OwnerTruthCandidateDecisionLookupResult(
                result="found",
                expected_candidate_version=int(receipt["expectedCandidateVersion"]),
                candidate_before_hash=str(receipt["candidateBeforeHash"]),
                expected_change_set_id=(
                    str(receipt["expectedChangeSetId"])
                    if receipt.get("expectedChangeSetId")
                    else None
                ),
                expected_proposal_hash=(
                    str(receipt["expectedProposalHash"])
                    if receipt.get("expectedProposalHash")
                    else None
                ),
                decision_result=OwnerTruthCandidateDecisionActivationResult(
                    review=review,
                    memory_activation=activation,
                    memory_revision=int(self._memory_revisions.get(context.vault_id, 0)),
                ),
            )

    def _current_formal_memories(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> tuple[OwnerTruthCurrentFormalMemory, ...]:
        """Return only current authoritative versions for one in-memory Vault."""

        memories: list[OwnerTruthCurrentFormalMemory] = []
        for record in self._memory_activations.values():
            if record.get("isActivationAuditOnly") or record.get("isCurrent") is not True:
                continue
            candidate = self._candidates.get(str(record.get("candidateId") or ""))
            payload = record.get("payload")
            if (
                candidate is None
                or candidate.vault_id != context.vault_id
                or candidate.owner_subject_id != context.owner_subject_id
                or not isinstance(payload, Mapping)
                or not isinstance(payload.get("content"), Mapping)
                or not isinstance(payload.get("evidenceRefs"), list)
            ):
                continue
            memories.append(
                OwnerTruthCurrentFormalMemory(
                    memory_id=str(record.get("memoryId") or ""),
                    memory_version_id=str(record.get("memoryVersionId") or ""),
                    vault_id=context.vault_id,
                    owner_subject_id=context.owner_subject_id,
                    version_number=int(record.get("memoryVersion") or 0),
                    memory_kind=str(record.get("memoryKind") or candidate.memory_kind.value),
                    content_schema_version=str(
                        payload.get("contentSchemaVersion")
                        or candidate.content_schema_version
                    ),
                    content=payload["content"],
                    evidence_refs=tuple(payload["evidenceRefs"]),
                )
            )
        return tuple(sorted(memories, key=lambda item: (item.memory_id, item.version_number)))

    def _propose_changeset(
        self,
        *,
        candidate: OwnerTruthCandidateSnapshot,
        current_memories: tuple[OwnerTruthCurrentFormalMemory, ...],
        base_memory_revision: int,
        resolved_content: Mapping[str, Any] | None = None,
        resolved_content_schema_version: str | None = None,
    ) -> OwnerTruthMemoryChangeSetProposal | None:
        """Create a deterministic, immutable preview without changing a Candidate."""

        if candidate.content_schema_version != OWNER_TRUTH_SCHEMA_VERSION_V5:
            return None
        proposal = build_memory_changeset_proposal(
            candidate=candidate,
            current_memories=current_memories,
            base_memory_revision=base_memory_revision,
            resolved_content=resolved_content,
            resolved_content_schema_version=resolved_content_schema_version,
        )
        self._memory_changeset_proposals[proposal.proposal_id] = proposal.payload()
        return proposal

    def _assert_proposal_binding(
        self,
        *,
        candidate: OwnerTruthCandidateSnapshot,
        command: OwnerTruthCandidateReviewCommand,
        record: OwnerTruthCandidateDecisionWriteRecord,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthMemoryChangeSetProposal | None:
        """Fail closed when an Owner confirms an outdated or unseen V5 diff."""

        if candidate.content_schema_version != OWNER_TRUTH_SCHEMA_VERSION_V5:
            return None
        if (
            command.expected_change_set_id is None
            or command.expected_proposal_hash is None
            or command.expected_memory_revision is None
        ):
            raise OwnerTruthCandidateReviewConflict(
                "V5 Candidate review requires a proposed ChangeSet and base memory revision"
            )
        current_revision = int(self._memory_revisions.get(context.vault_id, 0))
        proposal = self._propose_changeset(
            candidate=candidate,
            current_memories=self._current_formal_memories(context=context),
            base_memory_revision=current_revision,
            resolved_content=record.corrected_value,
            resolved_content_schema_version=record.corrected_value_schema_version,
        )
        if proposal is None:  # pragma: no cover - guarded above
            raise OwnerTruthCandidateReviewConflict("V5 ChangeSet proposal is unavailable")
        if command.expected_memory_revision != proposal.change_set.base_memory_revision:
            raise OwnerTruthCandidateReviewConflict(
                "formal memory revision does not match the proposed ChangeSet"
            )
        if (
            command.expected_change_set_id != proposal.change_set.change_set_id
            or command.expected_proposal_hash != proposal.proposal_hash
        ):
            raise OwnerTruthCandidateReviewConflict(
                "proposed ChangeSet is stale; reload the Candidate review diff"
            )
        return proposal

    @staticmethod
    def _activation_result_from_record(
        *,
        record: Mapping[str, Any],
        outcome: str,
        receipt_id: str,
        candidate: OwnerTruthCandidateSnapshot,
    ) -> OwnerTruthMemoryActivationResult:
        return OwnerTruthMemoryActivationResult(
            outcome=outcome,
            receipt_id=receipt_id,
            candidate_id=candidate.candidate_id,
            decision=candidate.decision,
            memory_id=(str(record["memoryId"]) if record.get("memoryId") else None),
            memory_version_id=(
                str(record["memoryVersionId"])
                if record.get("memoryVersionId")
                else None
            ),
            memory_version=(
                int(record["memoryVersion"])
                if record.get("memoryVersion") is not None
                else None
            ),
            authority_epoch=(
                int(record["authorityEpoch"])
                if record.get("authorityEpoch") is not None
                else None
            ),
            content_hash=(str(record["contentHash"]) if record.get("contentHash") else None),
        )

    def memory_revision(self, *, context: OwnerTruthCommandContext) -> int:
        """Read the monotonic current-formal revision without exposing facts."""

        _assert_owner_context(context)
        with self._lock:
            self.assert_active_owner_vault(context=context)
            return int(self._memory_revisions.get(context.vault_id, 0))

    def activate_memory_version(
        self,
        *,
        receipt_id: str,
        context: OwnerTruthCommandContext,
        expected_memory_revision: int | None = None,
    ) -> OwnerTruthMemoryActivationResult:
        """Apply one receipt through the V5 changeset path or legacy activation.

        Older payload schemas retain their isolated compatibility path.  New
        V5 candidates use a current-formal snapshot, revision compare-and-swap
        and one explicit changeset operation; they never silently create a
        duplicate record for the same reviewed fact.
        """

        _assert_owner_context(context)
        with self._lock:
            receipt = next(
                (item for item in self._receipts.values() if item["id"] == receipt_id),
                None,
            )
            if receipt is None:
                raise OwnerTruthCandidateReviewAccessDenied(
                    "DecisionReceipt does not exist in this Owner Vault"
                )
            candidate = self._candidates.get(str(receipt["candidateId"]))
            if candidate is None or candidate.vault_id != context.vault_id:
                raise OwnerTruthCandidateReviewAccessDenied(
                    "DecisionReceipt does not belong to this Owner Vault"
                )
            if candidate.decision.value != str(receipt["decision"]):
                raise OwnerTruthCandidateReviewConflict(
                    "DecisionReceipt does not match its terminal Candidate"
                )
            decision = candidate.decision
            existing_activation = self._memory_activations.get(str(receipt_id))
            if existing_activation is not None:
                return self._activation_result_from_record(
                    record=existing_activation,
                    outcome="deduplicated",
                    receipt_id=str(receipt_id),
                    candidate=candidate,
                )
            self._assert_live_target(candidate=candidate, context=context)
            corrected_value = None
            corrected_schema_version = None
            corrected_value_id = receipt.get("correctedValueId")
            if corrected_value_id is not None:
                stored = self._corrected_values.get(str(corrected_value_id))
                if stored is None or stored.get("decisionReceiptId") != receipt_id:
                    raise OwnerTruthCandidateReviewConflict(
                        "corrected DecisionReceipt is missing its immutable value"
                    )
                corrected_value = stored["content"]
                corrected_schema_version = str(stored["contentSchemaVersion"])

            if candidate.content_schema_version == OWNER_TRUTH_SCHEMA_VERSION_V5:
                current_revision = int(self._memory_revisions.get(context.vault_id, 0))
                if (
                    expected_memory_revision is not None
                    and expected_memory_revision != current_revision
                ):
                    raise OwnerTruthCandidateReviewConflict(
                        "formal memory revision does not match expectedMemoryRevision"
                    )
                reviewed_candidate = _candidate_at_review_version(
                    candidate,
                    expected_candidate_version=receipt.get("expectedCandidateVersion"),
                )
                proposal = self._propose_changeset(
                    candidate=reviewed_candidate,
                    current_memories=self._current_formal_memories(context=context),
                    base_memory_revision=current_revision,
                    resolved_content=corrected_value,
                    resolved_content_schema_version=corrected_schema_version,
                )
                if proposal is None:  # pragma: no cover - V5 guard
                    raise OwnerTruthCandidateReviewConflict("V5 ChangeSet proposal is unavailable")
                if (
                    receipt.get("expectedChangeSetId")
                    != proposal.change_set.change_set_id
                    or receipt.get("expectedProposalHash") != proposal.proposal_hash
                ):
                    raise OwnerTruthCandidateReviewConflict(
                        "review receipt is not bound to the current proposed ChangeSet"
                    )
                try:
                    changeset_plan = build_memory_changeset_activation_plan(
                        candidate=reviewed_candidate,
                        receipt_id=str(receipt_id),
                        receipt_decision=decision,
                        receipt_after_hash=str(receipt["candidateAfterHash"]),
                        current_memories=self._current_formal_memories(context=context),
                        base_memory_revision=current_revision,
                        resolved_content=corrected_value,
                        resolved_content_schema_version=corrected_schema_version,
                    )
                except OwnerTruthMemoryChangeSetActivationError as exc:
                    raise OwnerTruthCandidateReviewConflict(
                        f"formal memory changeset cannot be applied: {exc}"
                    ) from exc

                operation = changeset_plan.change_set.operation
                activation_record: dict[str, Any] = {
                    "activationOutcome": changeset_plan.outcome,
                    "authorityEpoch": changeset_plan.authority_epoch,
                    "candidateId": changeset_plan.candidate_id,
                    "changeSet": {
                        "baseMemoryRevision": changeset_plan.change_set.base_memory_revision,
                        "changeSetId": changeset_plan.change_set.change_set_id,
                        "operation": operation.kind.value,
                        "reason": operation.reason,
                        "targetMemoryId": operation.target_memory_id,
                        "targetMemoryVersionId": operation.target_memory_version_id,
                    },
                    "contentHash": changeset_plan.content_hash,
                    "createdAt": datetime.now(timezone.utc).isoformat(),
                    "isCurrent": changeset_plan.writes_memory_version,
                    "isActivationAuditOnly": not changeset_plan.writes_memory_version,
                    "memoryId": changeset_plan.memory_id,
                    "memoryKind": changeset_plan.memory_kind,
                    "memoryVersion": changeset_plan.memory_version,
                    "memoryVersionId": changeset_plan.memory_version_id,
                    "payload": (
                        deepcopy(dict(changeset_plan.payload))
                        if changeset_plan.payload is not None
                        else None
                    ),
                    "sourceId": changeset_plan.source_id,
                    "sourceVersion": changeset_plan.source_version,
                }
                if changeset_plan.outcome == "revised" and changeset_plan.memory_id:
                    for item in self._memory_activations.values():
                        if (
                            item.get("memoryId") == changeset_plan.memory_id
                            and item.get("isCurrent") is True
                        ):
                            item["isCurrent"] = False
                self._memory_activations[str(receipt_id)] = activation_record
                self._memory_changesets[changeset_plan.change_set.change_set_id] = deepcopy(
                    activation_record["changeSet"]
                )
                if changeset_plan.writes_memory_version:
                    self._memory_revisions[context.vault_id] = current_revision + 1
                return self._activation_result_from_record(
                    record=activation_record,
                    outcome=changeset_plan.outcome,
                    receipt_id=str(receipt_id),
                    candidate=candidate,
                )

            if decision in {CandidateDecision.REJECTED, CandidateDecision.INVALIDATED}:
                return OwnerTruthMemoryActivationResult(
                    outcome="notApplicable",
                    receipt_id=str(receipt_id),
                    candidate_id=candidate.candidate_id,
                    decision=decision,
                    memory_id=None,
                    memory_version_id=None,
                    memory_version=None,
                    authority_epoch=None,
                    content_hash=None,
                )

            plan = build_memory_activation_plan(
                candidate=candidate,
                receipt_id=str(receipt_id),
                receipt_decision=decision,
                receipt_after_hash=str(receipt["candidateAfterHash"]),
                corrected_value=corrected_value,
                corrected_value_schema_version=corrected_schema_version,
            )
            if plan is None:  # defensive: non-activating decisions returned above
                raise OwnerTruthCandidateReviewConflict("terminal decision cannot activate MemoryVersion")
            self._memory_activations[plan.receipt_id] = {
                "activationOutcome": "created",
                "authorityEpoch": plan.authority_epoch,
                "candidateId": plan.candidate_id,
                "contentHash": plan.content_hash,
                "isCurrent": True,
                "memoryId": plan.memory_id,
                "memoryKind": plan.memory_kind,
                "memoryVersionId": plan.memory_version_id,
                "memoryVersion": 1,
                "createdAt": datetime.now(timezone.utc).isoformat(),
                "payload": deepcopy(dict(plan.payload)),
                "sourceId": plan.source_id,
                "sourceVersion": plan.source_version,
            }
            return OwnerTruthMemoryActivationResult(
                outcome="created",
                receipt_id=plan.receipt_id,
                candidate_id=plan.candidate_id,
                decision=decision,
                memory_id=plan.memory_id,
                memory_version_id=plan.memory_version_id,
                memory_version=1,
                authority_epoch=plan.authority_epoch,
                content_hash=plan.content_hash,
            )

    def activate_correction_memory_version(
        self,
        *,
        receipt_id: str,
        correction_request_id: str,
        memory_id: str,
        expected_memory_version_id: str,
        reason_code_hash: str,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthMemoryCorrectionActivationResult:
        """Supersede one current in-memory version after a correction decision.

        This is a semantic-double implementation of the Postgres operation
        below.  It keeps the original version immutable, changes only its
        current pointer, and makes the correction-source version the sole
        projection input.
        """

        _assert_owner_context(context)
        with self._lock:
            receipt = next(
                (item for item in self._receipts.values() if item["id"] == receipt_id),
                None,
            )
            if receipt is None:
                raise OwnerTruthCandidateReviewAccessDenied(
                    "DecisionReceipt does not exist in this Owner Vault"
                )
            candidate = self._candidates.get(str(receipt["candidateId"]))
            if candidate is None or candidate.vault_id != context.vault_id:
                raise OwnerTruthCandidateReviewAccessDenied(
                    "DecisionReceipt does not belong to this Owner Vault"
                )
            if candidate.decision is not CandidateDecision.CORRECTED:
                raise OwnerTruthMemoryCorrectionError(
                    "correction resolver requires a corrected Candidate"
                )
            self._assert_live_target(candidate=candidate, context=context)
            corrected_value_id = receipt.get("correctedValueId")
            corrected_value = self._corrected_values.get(str(corrected_value_id or ""))
            if corrected_value is None:
                raise OwnerTruthCandidateReviewConflict(
                    "corrected DecisionReceipt is missing its immutable value"
                )
            predecessor_record = next(
                (
                    record
                    for record in self._memory_activations.values()
                    if str(record.get("memoryId") or "") == memory_id
                    and str(record.get("memoryVersionId") or "") == expected_memory_version_id
                    and record.get("isCurrent", True) is True
                ),
                None,
            )
            if predecessor_record is None:
                raise OwnerTruthMemoryCorrectionError(
                    "cited MemoryVersion is no longer current and cannot be corrected"
                )
            predecessor_payload = predecessor_record.get("payload")
            if not isinstance(predecessor_payload, Mapping):
                raise OwnerTruthMemoryCorrectionError(
                    "cited MemoryVersion payload is unavailable"
                )
            predecessor = OwnerTruthMemoryVersionSnapshot(
                vault_id=context.vault_id,
                memory_id=memory_id,
                memory_version_id=expected_memory_version_id,
                version_number=int(predecessor_record.get("memoryVersion") or 0),
                is_current=True,
                # Epoch zero is the valid initial Owner Truth epoch.  Do not
                # use ``or`` here: it would turn that valid value into the
                # sentinel for a missing epoch and reject the first correction.
                authority_epoch=(
                    -1
                    if predecessor_record.get("authorityEpoch") is None
                    else int(predecessor_record["authorityEpoch"])
                ),
                source_id=str(predecessor_record.get("sourceId") or ""),
                source_version=int(predecessor_record.get("sourceVersion") or 0),
                content_schema_version=str(
                    predecessor_payload.get("contentSchemaVersion") or ""
                ),
                content_hash=str(predecessor_record.get("contentHash") or ""),
                payload=predecessor_payload,
            )
            plan = build_memory_correction_plan(
                candidate=candidate,
                receipt_id=receipt_id,
                receipt_after_hash=str(receipt["candidateAfterHash"]),
                corrected_value=corrected_value["content"],
                corrected_value_schema_version=str(corrected_value["contentSchemaVersion"]),
                correction_request_id=correction_request_id,
                reason_code_hash=reason_code_hash,
                predecessor=predecessor,
            )
            existing = self._memory_activations.get(plan.receipt_id)
            if existing is not None:
                if (
                    existing.get("memoryId") != plan.memory_id
                    or existing.get("memoryVersionId") != plan.replacement_memory_version_id
                    or existing.get("contentHash") != plan.content_hash
                    or existing.get("isCurrent") is not True
                ):
                    raise OwnerTruthCandidateReviewConflict(
                        "DecisionReceipt already supersedes a different MemoryVersion"
                    )
                return OwnerTruthMemoryCorrectionActivationResult(
                    outcome="deduplicated",
                    receipt_id=plan.receipt_id,
                    candidate_id=plan.candidate_id,
                    correction_request_id=plan.correction_request_id,
                    memory_id=plan.memory_id,
                    superseded_memory_version_id=plan.superseded_memory_version_id,
                    superseded_memory_version=plan.superseded_memory_version,
                    replacement_memory_version_id=plan.replacement_memory_version_id,
                    replacement_memory_version=plan.replacement_memory_version,
                    authority_epoch=plan.authority_epoch,
                    content_hash=plan.content_hash,
                )
            predecessor_record["isCurrent"] = False
            self._memory_activations[plan.receipt_id] = {
                "authorityEpoch": plan.authority_epoch,
                "candidateId": plan.candidate_id,
                "contentHash": plan.content_hash,
                "correctionRequestId": plan.correction_request_id,
                "isCurrent": True,
                "memoryId": plan.memory_id,
                "memoryVersionId": plan.replacement_memory_version_id,
                "memoryVersion": plan.replacement_memory_version,
                "createdAt": datetime.now(timezone.utc).isoformat(),
                "payload": deepcopy(dict(plan.payload)),
                "sourceId": plan.source_id,
                "sourceVersion": plan.source_version,
                "supersedesVersionId": plan.superseded_memory_version_id,
            }
            return OwnerTruthMemoryCorrectionActivationResult(
                outcome="created",
                receipt_id=plan.receipt_id,
                candidate_id=plan.candidate_id,
                correction_request_id=plan.correction_request_id,
                memory_id=plan.memory_id,
                superseded_memory_version_id=plan.superseded_memory_version_id,
                superseded_memory_version=plan.superseded_memory_version,
                replacement_memory_version_id=plan.replacement_memory_version_id,
                replacement_memory_version=plan.replacement_memory_version,
                authority_epoch=plan.authority_epoch,
                content_hash=plan.content_hash,
            )

    def list_memory_projection_inputs(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> tuple[int, tuple[OwnerTruthMemoryProjectionInput, ...]]:
        """Expose only current, accepted/corrected MemoryVersions to a projection.

        This is an in-memory semantic-double port.  It mirrors the Postgres
        projector query without exposing Candidate proposals, decision receipts,
        or review rationale as projection input.
        """

        _assert_owner_context(context)
        with self._lock:
            vault = self._vault_states.get(context.vault_id)
            if (
                vault is None
                or vault[0] != context.owner_subject_id
                or vault[1] != "active"
            ):
                raise OwnerTruthCandidateReviewAccessDenied(
                    "Vault is not active for this Owner"
                )
            authority_epoch = int(vault[2])
            inputs: list[OwnerTruthMemoryProjectionInput] = []
            for activation in self._memory_activations.values():
                if activation.get("isCurrent", True) is not True:
                    continue
                candidate = self._candidates.get(str(activation.get("candidateId") or ""))
                if (
                    candidate is None
                    or candidate.vault_id != context.vault_id
                    or candidate.owner_subject_id != context.owner_subject_id
                    or candidate.authority_epoch != authority_epoch
                    or candidate.decision
                    not in {CandidateDecision.ACCEPTED, CandidateDecision.CORRECTED}
                    or self._source_states.get((candidate.vault_id, candidate.source_id))
                    != "active"
                ):
                    continue
                payload = activation.get("payload")
                if not isinstance(payload, Mapping):
                    raise OwnerTruthMemoryProjectionError(
                        "activated MemoryVersion payload is unavailable"
                    )
                evidence_refs = payload.get("evidenceRefs")
                if not isinstance(evidence_refs, list) or not evidence_refs:
                    raise OwnerTruthMemoryProjectionError(
                        "activated MemoryVersion evidence references are unavailable"
                    )
                source_id = str(activation.get("sourceId") or candidate.source_id)
                try:
                    source_version = int(
                        activation.get("sourceVersion")
                        if activation.get("sourceVersion") is not None
                        else 0
                    )
                except (TypeError, ValueError):
                    source_version = 0
                if source_id != candidate.source_id or source_version < 1:
                    raise OwnerTruthMemoryProjectionError(
                        "activated MemoryVersion source version is unavailable"
                    )
                inputs.append(
                    OwnerTruthMemoryProjectionInput(
                        memory_id=str(activation.get("memoryId") or ""),
                        memory_version_id=str(activation.get("memoryVersionId") or ""),
                        vault_id=context.vault_id,
                        owner_subject_id=context.owner_subject_id,
                        authority_epoch=authority_epoch,
                        version_number=int(activation.get("memoryVersion") or 0),
                        source_id=source_id,
                        source_version=source_version,
                        memory_kind=candidate.memory_kind.value,
                        perspective_type=candidate.perspective_type.value,
                        epistemic_status=candidate.epistemic_status.value,
                        sensitivity=candidate.sensitivity.value,
                        content_schema_version=str(
                            payload.get("contentSchemaVersion") or ""
                        ),
                        content_hash=str(activation.get("contentHash") or ""),
                        content=payload.get("content"),
                        evidence_refs=tuple(
                            dict(item)
                            for item in evidence_refs
                            if isinstance(item, Mapping)
                        ),
                    )
                )
        return authority_epoch, tuple(
            sorted(inputs, key=lambda item: (item.memory_id, item.version_number, item.memory_version_id))
        )

    def _assert_live_target(
        self,
        *,
        candidate: OwnerTruthCandidateSnapshot,
        context: OwnerTruthCommandContext,
    ) -> None:
        vault = self._vault_states.get(candidate.vault_id)
        if (
            vault is None
            or candidate.vault_id != context.vault_id
            or candidate.owner_subject_id != context.owner_subject_id
            or vault[0] != context.owner_subject_id
            or vault[1] != "active"
            or vault[2] != candidate.authority_epoch
        ):
            raise OwnerTruthCandidateReviewAccessDenied("Candidate does not belong to this active Owner Vault")
        if self._source_states.get((candidate.vault_id, candidate.source_id)) != "active":
            raise OwnerTruthCandidateReviewSourceInactive("Candidate Source is no longer active")

    @staticmethod
    def _assert_existing_command(
        existing: Mapping[str, Any],
        *,
        command: OwnerTruthCandidateReviewCommand,
        context: OwnerTruthCommandContext,
    ) -> None:
        expected = {
            "candidateId": command.candidate_id,
            "expectedCandidateVersion": command.expected_candidate_version,
            "payloadHash": command.payload_hash,
            "actorSubjectId": context.actor_subject_id,
            "policyVersion": context.policy_version,
        }
        if any(existing.get(key) != value for key, value in expected.items()):
            raise OwnerTruthCandidateReviewConflict(
                "commandId cannot be reused with a different Candidate decision"
            )
        _assert_replay_authorization_capture(
            existing=_authorization_capture_from_value(existing.get("authorizationCapture")),
            expected=context.authorization_capture,
        )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "candidates": {
                    candidate_id: {
                        "candidateId": candidate.candidate_id,
                        "vaultId": candidate.vault_id,
                        "ownerSubjectId": candidate.owner_subject_id,
                        "sourceId": candidate.source_id,
                        "memoryKind": candidate.memory_kind.value,
                        "perspectiveType": candidate.perspective_type.value,
                        "epistemicStatus": candidate.epistemic_status.value,
                        "sensitivity": candidate.sensitivity.value,
                        "decision": candidate.decision.value,
                        "payload": deepcopy(dict(candidate.payload)),
                        "policyVersion": candidate.policy_version,
                        "authorityEpoch": candidate.authority_epoch,
                        "rowVersion": candidate.row_version,
                        "contentHash": candidate.content_hash,
                        "contentSchemaVersion": candidate.content_schema_version,
                        "createdAt": self._candidate_created_at.get(candidate_id),
                    }
                    for candidate_id, candidate in self._candidates.items()
                },
                "correctedValues": deepcopy(self._corrected_values),
                "memoryActivations": deepcopy(self._memory_activations),
                "memoryChangesets": deepcopy(self._memory_changesets),
                "memoryChangesetProposals": deepcopy(self._memory_changeset_proposals),
                "memoryChangeSetGroupProposals": deepcopy(self._memory_changeset_group_proposals),
                "memoryChangeSetGroupReceipts": deepcopy(self._memory_changeset_group_receipts),
                "memoryRevisions": deepcopy(self._memory_revisions),
                "receipts": deepcopy(self._receipts),
            }

    def candidate_snapshot(self, candidate_id: str) -> OwnerTruthCandidateSnapshot | None:
        """Return the current semantic-double Candidate state for a read join.

        This is intentionally only a narrow in-memory test-double bridge. The
        Postgres review-batch composition reads the canonical table directly.
        """

        with self._lock:
            candidate = self._candidates.get(str(candidate_id or ""))
            return None if candidate is None else deepcopy(candidate)

    def changeset_group_state(
        self,
        *,
        candidate_ids: tuple[str, ...],
        context: OwnerTruthCommandContext,
        lock: bool,
    ) -> tuple[
        tuple[OwnerTruthCandidateSnapshot, ...],
        tuple[OwnerTruthCurrentFormalMemory, ...],
        int,
    ]:
        """Load one owner-scoped group against a single formal-memory revision.

        ``lock`` has no behavioural distinction for this in-memory semantic
        double because its RLock serializes both reads and writes.  It is kept
        in the port so the PostgreSQL implementation can take current-version
        and Candidate locks during the terminal compare-and-swap.
        """

        del lock
        _assert_owner_context(context)
        normalized_ids = tuple(str(candidate_id or "").strip() for candidate_id in candidate_ids)
        if not normalized_ids or len(normalized_ids) != len(set(normalized_ids)):
            raise OwnerTruthCandidateReviewConflict("ChangeSet group Candidate IDs are invalid")
        with self._lock:
            self.assert_active_owner_vault(context=context)
            candidates: list[OwnerTruthCandidateSnapshot] = []
            for candidate_id in normalized_ids:
                candidate = self._candidates.get(candidate_id)
                if candidate is None:
                    raise OwnerTruthCandidateReviewAccessDenied(
                        "Candidate does not exist in this Vault"
                    )
                self._assert_live_target(candidate=candidate, context=context)
                if candidate.decision is not CandidateDecision.PENDING:
                    raise OwnerTruthCandidateReviewConflict(
                        "terminal Candidate cannot enter a ChangeSet group"
                    )
                candidates.append(deepcopy(candidate))
            return (
                tuple(candidates),
                self._current_formal_memories(context=context),
                int(self._memory_revisions.get(context.vault_id, 0)),
            )

    def persist_changeset_group_proposal(
        self,
        *,
        proposal: Any,
        context: OwnerTruthCommandContext,
    ) -> None:
        """Persist immutable group and child previews for later terminal binding."""

        _assert_owner_context(context)
        if (
            getattr(proposal, "vault_id", None) != context.vault_id
            or getattr(proposal, "owner_subject_id", None) != context.owner_subject_id
        ):
            raise OwnerTruthCandidateReviewAccessDenied(
                "ChangeSet group proposal does not belong to this Owner Vault"
            )
        proposal_id = str(getattr(proposal, "proposal_id", ""))
        proposal_hash = str(getattr(proposal, "proposal_hash", ""))
        payload_method = getattr(proposal, "payload", None)
        members = tuple(getattr(proposal, "members", ()))
        if not proposal_id or not proposal_hash or not callable(payload_method) or not members:
            raise OwnerTruthCandidateReviewConflict("ChangeSet group proposal is malformed")
        with self._lock:
            existing = self._memory_changeset_group_proposals.get(proposal_id)
            if existing is not None:
                if existing.get("groupProposalHash") != proposal_hash:
                    raise OwnerTruthCandidateReviewConflict(
                        "ChangeSet group proposal ID cannot be reused with different content"
                    )
                return
            for member in members:
                child = getattr(member, "proposal", None)
                child_payload = child.payload() if child is not None else None
                if not isinstance(child_payload, Mapping):
                    raise OwnerTruthCandidateReviewConflict(
                        "ChangeSet group member proposal is malformed"
                    )
                self._memory_changeset_proposals.setdefault(
                    str(child.proposal_id),
                    deepcopy(dict(child_payload)),
                )
            self._memory_changeset_group_proposals[proposal_id] = deepcopy(dict(payload_method()))

    def has_changeset_group_proposal(
        self,
        *,
        proposal: Any,
        context: OwnerTruthCommandContext,
    ) -> bool:
        _assert_owner_context(context)
        with self._lock:
            stored = self._memory_changeset_group_proposals.get(
                str(getattr(proposal, "proposal_id", ""))
            )
            return bool(
                isinstance(stored, Mapping)
                and stored.get("vaultId") == context.vault_id
                and stored.get("groupProposalHash") == getattr(proposal, "proposal_hash", None)
            )

    def lookup_changeset_group_receipt(
        self,
        *,
        command_id_hash: str,
        context: OwnerTruthCommandContext,
    ) -> Mapping[str, Any] | None:
        _assert_owner_context(context)
        with self._lock:
            stored = self._memory_changeset_group_receipts.get(str(command_id_hash or ""))
            if stored is None:
                return None
            if (
                stored.get("vaultId") != context.vault_id
                or stored.get("ownerSubjectId") != context.owner_subject_id
            ):
                raise OwnerTruthCandidateReviewAccessDenied(
                    "ChangeSet group receipt does not belong to this Owner Vault"
                )
            return deepcopy(stored)

    def persist_changeset_group_receipt(
        self,
        *,
        result: Any,
        command: Any,
        context: OwnerTruthCommandContext,
    ) -> None:
        _assert_owner_context(context)
        payload_method = getattr(result, "payload", None)
        command_id_hash = str(getattr(command, "command_id_hash", ""))
        payload_hash = str(getattr(command, "payload_hash", ""))
        if not command_id_hash or not payload_hash or not callable(payload_method):
            raise OwnerTruthCandidateReviewConflict("ChangeSet group receipt is malformed")
        with self._lock:
            existing = self._memory_changeset_group_receipts.get(command_id_hash)
            if existing is not None:
                if existing.get("payloadHash") != payload_hash:
                    raise OwnerTruthCandidateReviewConflict(
                        "group commandId cannot be reused with different content"
                    )
                return
            payload = dict(payload_method())
            self._memory_changeset_group_receipts[command_id_hash] = {
                **deepcopy(payload),
                "vaultId": context.vault_id,
                "ownerSubjectId": context.owner_subject_id,
                "payloadHash": payload_hash,
            }


class PostgresOwnerTruthCandidateReviewRepository:
    """Postgres Owner Truth review port bound to one active Unit of Work."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def list_pending(self, *, context: OwnerTruthCommandContext) -> tuple[OwnerTruthCandidateInboxItem, ...]:
        _assert_owner_context(context)
        with self._cursor() as cursor:
            vault = self._active_vault(cursor, context=context, lock=False)
            cursor.execute(
                """
                SELECT c.id, c.vault_id, c.owner_subject_id, c.source_id,
                    c.candidate_kind, c.perspective_type, c.epistemic_status,
                    c.sensitivity, c.decision_status, c.policy_version,
                    c.authority_epoch, c.row_version, c.content_hash,
                    c.payload_schema_version, c.payload, c.created_at
                FROM owner_truth.memory_candidates AS c
                JOIN owner_truth.sources AS s
                  ON s.vault_id = c.vault_id AND s.id = c.source_id
                WHERE c.vault_id = %s
                  AND c.owner_subject_id = %s
                  AND c.decision_status = 'pending'
                  AND c.authority_epoch = %s
                  AND s.owner_subject_id = %s
                  AND s.authority_epoch = %s
                  AND s.state = 'active'
                ORDER BY c.created_at ASC, c.id ASC
                """,
                (
                    context.vault_id,
                    context.owner_subject_id,
                    int(vault["authority_epoch"]),
                    context.owner_subject_id,
                    int(vault["authority_epoch"]),
                ),
            )
            rows = cursor.fetchall()
            current_memories = self._current_formal_memories(
                cursor,
                context=context,
                lock=False,
            )
            base_memory_revision = self._memory_revision_for_read(
                cursor,
                vault_id=context.vault_id,
            )
            items: list[OwnerTruthCandidateInboxItem] = []
            for row in rows:
                candidate = self._candidate_from_row(row)
                proposal = self._propose_changeset(
                    cursor,
                    candidate=candidate,
                    current_memories=current_memories,
                    base_memory_revision=base_memory_revision,
                )
                items.append(
                    _inbox_item(
                        candidate,
                        created_at=(row.get("created_at").isoformat() if row.get("created_at") else None),
                        proposed_change_set=(proposal.payload() if proposal is not None else None),
                    )
                )
        return tuple(items)

    def preview_changeset(
        self,
        *,
        candidate_id: str,
        context: OwnerTruthCommandContext,
        corrected_value: Mapping[str, Any] | None = None,
        corrected_value_schema_version: str | None = None,
    ) -> OwnerTruthMemoryChangeSetProposal | None:
        return self.preview_changeset_result(
            candidate_id=candidate_id,
            context=context,
            corrected_value=corrected_value,
            corrected_value_schema_version=corrected_value_schema_version,
        ).proposal

    def preview_changeset_result(
        self,
        *,
        candidate_id: str,
        context: OwnerTruthCommandContext,
        corrected_value: Mapping[str, Any] | None = None,
        corrected_value_schema_version: str | None = None,
    ) -> OwnerTruthCandidateChangeSetPreview:
        """Persist a deterministic, Owner-visible V5 ChangeSet preview.

        PostgreSQL stores the immutable preview so a later decision can prove
        which diff the Owner saw. The terminal decision still reacquires locks
        and recomputes it against the current formal-memory revision.
        """

        _assert_owner_context(context)
        with self._cursor() as cursor:
            vault = self._active_vault(cursor, context=context, lock=False)
            candidate = self._locked_candidate(
                cursor,
                candidate_id=str(candidate_id or ""),
                context=context,
            )
            self._assert_candidate_live(cursor, candidate=candidate, context=context, vault=vault)
            if candidate.decision is not CandidateDecision.PENDING:
                raise OwnerTruthCandidateReviewConflict(
                    "terminal Candidate cannot receive a ChangeSet preview"
                )
            proposal = self._propose_changeset(
                cursor,
                candidate=candidate,
                current_memories=self._current_formal_memories(
                    cursor,
                    context=context,
                    lock=False,
                ),
                base_memory_revision=self._memory_revision_for_read(
                    cursor,
                    vault_id=context.vault_id,
                ),
                resolved_content=corrected_value,
                resolved_content_schema_version=corrected_value_schema_version,
            )
            return OwnerTruthCandidateChangeSetPreview(
                proposal=proposal,
                source_candidate_version=candidate.row_version,
                source_candidate_content_hash=candidate.content_hash,
                submitted_corrected_value=(
                    deepcopy(dict(corrected_value))
                    if corrected_value is not None
                    else None
                ),
                submitted_corrected_value_schema_version=corrected_value_schema_version,
            )

    def list_review_history(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> tuple[OwnerTruthCandidateReviewHistoryItem, ...]:
        _assert_owner_context(context)
        with self._cursor() as cursor:
            self._active_vault(cursor, context=context, lock=False)
            cursor.execute(
                """
                SELECT c.id, c.vault_id, c.owner_subject_id, c.source_id,
                    c.candidate_kind, c.perspective_type, c.epistemic_status,
                    c.sensitivity, c.decision_status, c.policy_version,
                    c.authority_epoch, c.row_version, c.content_hash,
                    c.payload_schema_version, c.payload, c.created_at,
                    receipt.created_at AS decided_at,
                    memory.id AS memory_id,
                    version.id AS memory_version_id,
                    version.version_number AS memory_version,
                    version.is_current AS memory_version_is_current,
                    changeset.operation_kind AS changeset_operation_kind,
                    changeset.activation_outcome AS changeset_activation_outcome,
                    changeset.target_memory_id AS changeset_memory_id,
                    changeset.target_memory_version_id AS changeset_memory_version_id,
                    changeset.target_memory_version AS changeset_memory_version
                FROM owner_truth.memory_candidates AS c
                JOIN owner_truth.decision_receipts AS receipt
                  ON receipt.vault_id = c.vault_id
                 AND receipt.candidate_id = c.id
                 AND receipt.decision = c.decision_status
                LEFT JOIN owner_truth.memory_versions AS version
                  ON version.vault_id = receipt.vault_id
                 AND version.decision_receipt_id = receipt.id
                LEFT JOIN owner_truth.memories AS memory
                  ON memory.vault_id = version.vault_id
                 AND memory.id = version.memory_id
                LEFT JOIN owner_truth.memory_changesets AS changeset
                  ON changeset.vault_id = receipt.vault_id
                 AND changeset.decision_receipt_id = receipt.id
                WHERE c.vault_id = %s
                  AND c.owner_subject_id = %s
                  AND c.decision_status <> 'pending'
                ORDER BY receipt.created_at DESC, c.id DESC
                """,
                (context.vault_id, context.owner_subject_id),
            )
            rows = cursor.fetchall()

        history: list[OwnerTruthCandidateReviewHistoryItem] = []
        for row in rows:
            candidate = self._candidate_from_row(row)
            decided_at = row.get("decided_at")
            if decided_at is None:
                raise OwnerTruthCandidateReviewConflict(
                    "terminal Candidate is missing its review timestamp"
                )
            activation: dict[str, Any] | None = None
            if row.get("memory_id") is not None or row.get("memory_version_id") is not None:
                activation = {
                    "isCurrent": row.get("memory_version_is_current"),
                    "memoryId": str(row.get("memory_id") or ""),
                    "memoryVersionId": str(row.get("memory_version_id") or ""),
                    "memoryVersion": row.get("memory_version"),
                }
            elif row.get("changeset_operation_kind") is not None:
                activation = {
                    "isActivationAuditOnly": True,
                    "operation": row.get("changeset_operation_kind"),
                    "memoryId": row.get("changeset_memory_id"),
                    "memoryVersionId": row.get("changeset_memory_version_id"),
                    "memoryVersion": row.get("changeset_memory_version"),
                }
            history.append(
                _review_history_item(
                    candidate=candidate,
                    created_at=(
                        row.get("created_at").isoformat()
                        if row.get("created_at")
                        else None
                    ),
                    decided_at=decided_at.isoformat(),
                    activation=activation,
                )
            )
        return tuple(history)

    def list_memory_version_history(
        self,
        *,
        memory_id: str,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthMemoryVersionHistory:
        _assert_owner_context(context)
        with self._cursor() as cursor:
            self._active_vault(cursor, context=context, lock=False)
            cursor.execute(
                """
                SELECT memory.memory_kind, memory.perspective_type,
                    memory.epistemic_status, memory.sensitivity, memory.status,
                    version.version_number, version.is_current,
                    version.schema_version, version.payload, version.created_at,
                    receipt.decision
                FROM owner_truth.memories AS memory
                JOIN owner_truth.memory_versions AS version
                  ON version.vault_id = memory.vault_id
                 AND version.memory_id = memory.id
                JOIN owner_truth.decision_receipts AS receipt
                  ON receipt.vault_id = version.vault_id
                 AND receipt.id = version.decision_receipt_id
                WHERE memory.vault_id = %s
                  AND memory.id = %s
                  AND memory.owner_subject_id = %s
                  AND memory.status = 'active'
                ORDER BY version.version_number DESC
                """,
                (context.vault_id, memory_id, context.owner_subject_id),
            )
            rows = cursor.fetchall()
        if not rows:
            raise OwnerTruthCandidateReviewAccessDenied(
                "Memory does not exist in this Owner Vault"
            )
        versions = tuple(
            _memory_version_history_item(
                version_number=row.get("version_number"),
                is_current=row.get("is_current"),
                decision=row.get("decision"),
                schema_version=row.get("schema_version"),
                payload=row.get("payload"),
                created_at=row.get("created_at"),
            )
            for row in rows
        )
        if sum(item.status == "current" for item in versions) != 1:
            raise OwnerTruthCandidateReviewConflict(
                "MemoryVersion history must contain exactly one current version"
            )
        first = rows[0]
        return OwnerTruthMemoryVersionHistory(
            memory_kind=str(first.get("memory_kind") or ""),
            perspective_type=str(first.get("perspective_type") or ""),
            epistemic_status=str(first.get("epistemic_status") or ""),
            sensitivity=str(first.get("sensitivity") or ""),
            memory_status=str(first.get("status") or ""),
            versions=versions,
        )

    def assert_active_owner_vault(self, *, context: OwnerTruthCommandContext) -> None:
        """Prove the owner/vault boundary without reading Candidate payloads."""

        _assert_owner_context(context)
        with self._cursor() as cursor:
            self._active_vault(cursor, context=context, lock=False)

    def changeset_group_state(
        self,
        *,
        candidate_ids: tuple[str, ...],
        context: OwnerTruthCommandContext,
        lock: bool,
    ) -> tuple[
        tuple[OwnerTruthCandidateSnapshot, ...],
        tuple[OwnerTruthCurrentFormalMemory, ...],
        int,
    ]:
        """Read related pending Candidates and one formal-memory snapshot.

        Terminal group confirmation calls this with ``lock=True``. That locks
        the vault revision and current versions before a child DecisionReceipt
        is written, so a competing write wins as a whole or returns stale.
        """

        _assert_owner_context(context)
        normalized_ids = tuple(str(candidate_id or "").strip() for candidate_id in candidate_ids)
        if not normalized_ids or len(normalized_ids) != len(set(normalized_ids)):
            raise OwnerTruthCandidateReviewConflict("ChangeSet group Candidate IDs are invalid")
        with self._cursor() as cursor:
            vault = self._active_vault(cursor, context=context, lock=lock)
            candidates: list[OwnerTruthCandidateSnapshot] = []
            for candidate_id in normalized_ids:
                candidate = self._locked_candidate(
                    cursor,
                    candidate_id=candidate_id,
                    context=context,
                )
                self._assert_candidate_live(
                    cursor,
                    candidate=candidate,
                    context=context,
                    vault=vault,
                )
                if candidate.decision is not CandidateDecision.PENDING:
                    raise OwnerTruthCandidateReviewConflict(
                        "terminal Candidate cannot enter a ChangeSet group"
                    )
                candidates.append(candidate)
            current_memories = self._current_formal_memories(
                cursor,
                context=context,
                lock=lock,
            )
            revision = (
                self._memory_revision_for_update(cursor, vault_id=context.vault_id)
                if lock
                else self._memory_revision_for_read(cursor, vault_id=context.vault_id)
            )
        return tuple(candidates), current_memories, revision

    def persist_changeset_group_proposal(
        self,
        *,
        proposal: Any,
        context: OwnerTruthCommandContext,
    ) -> None:
        """Persist the group header plus immutable per-Candidate previews."""

        _assert_owner_context(context)
        if (
            getattr(proposal, "vault_id", None) != context.vault_id
            or getattr(proposal, "owner_subject_id", None) != context.owner_subject_id
        ):
            raise OwnerTruthCandidateReviewAccessDenied(
                "ChangeSet group proposal does not belong to this Owner Vault"
            )
        payload_method = getattr(proposal, "payload", None)
        members = tuple(getattr(proposal, "members", ()))
        if not callable(payload_method) or not members:
            raise OwnerTruthCandidateReviewConflict("ChangeSet group proposal is malformed")
        with self._cursor() as cursor:
            for member in members:
                child_proposal = getattr(member, "proposal", None)
                if not isinstance(child_proposal, OwnerTruthMemoryChangeSetProposal):
                    raise OwnerTruthCandidateReviewConflict(
                        "ChangeSet group member proposal is malformed"
                    )
                self._persist_proposed_changeset(cursor, proposal=child_proposal)
            cursor.execute(
                """
                INSERT INTO owner_truth.memory_changeset_group_proposals (
                    id, vault_id, owner_subject_id, base_memory_revision,
                    proposal_hash, schema_version, payload
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (vault_id, id) DO NOTHING
                """,
                self._adapt_params(
                    (
                        proposal.proposal_id,
                        proposal.vault_id,
                        proposal.owner_subject_id,
                        proposal.base_memory_revision,
                        proposal.proposal_hash,
                        proposal.schema_version,
                        payload_method(),
                    )
                ),
            )
            cursor.execute(
                """
                SELECT proposal_hash, owner_subject_id
                FROM owner_truth.memory_changeset_group_proposals
                WHERE vault_id = %s AND id = %s
                FOR UPDATE
                """,
                (context.vault_id, proposal.proposal_id),
            )
            stored = cursor.fetchone()
            if (
                stored is None
                or str(stored.get("proposal_hash") or "") != proposal.proposal_hash
                or str(stored.get("owner_subject_id") or "") != context.owner_subject_id
            ):
                raise OwnerTruthCandidateReviewConflict(
                    "ChangeSet group proposal ID cannot be reused with different content"
                )
            operation_index_by_candidate = {
                member.selection.candidate_id: member.operation_index
                for member in members
            }
            for member in members:
                cursor.execute(
                    """
                    INSERT INTO owner_truth.memory_changeset_group_proposal_members (
                        group_proposal_id, vault_id, operation_index, candidate_id,
                        candidate_row_version, child_proposal_id, child_change_set_id,
                        requested_action, anticipated_outcome, applied_memory_revision
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (vault_id, group_proposal_id, operation_index) DO NOTHING
                    """,
                    (
                        proposal.proposal_id,
                        context.vault_id,
                        member.operation_index,
                        member.selection.candidate_id,
                        member.selection.expected_candidate_version,
                        member.proposal.proposal_id,
                        member.proposal.change_set.change_set_id,
                        member.selection.action.value,
                        member.anticipated_outcome,
                        member.applied_memory_revision,
                    ),
                )
            for dependency in proposal.dependencies:
                cursor.execute(
                    """
                    INSERT INTO owner_truth.memory_changeset_group_proposal_dependencies (
                        group_proposal_id, vault_id, before_operation_index,
                        after_operation_index
                    ) VALUES (%s, %s, %s, %s)
                    ON CONFLICT (vault_id, group_proposal_id, before_operation_index,
                                 after_operation_index) DO NOTHING
                    """,
                    (
                        proposal.proposal_id,
                        context.vault_id,
                        operation_index_by_candidate[dependency.before_candidate_id],
                        operation_index_by_candidate[dependency.after_candidate_id],
                    ),
                )

    def has_changeset_group_proposal(
        self,
        *,
        proposal: Any,
        context: OwnerTruthCommandContext,
    ) -> bool:
        _assert_owner_context(context)
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM owner_truth.memory_changeset_group_proposals
                WHERE vault_id = %s
                  AND owner_subject_id = %s
                  AND id = %s
                  AND proposal_hash = %s
                """,
                (
                    context.vault_id,
                    context.owner_subject_id,
                    getattr(proposal, "proposal_id", ""),
                    getattr(proposal, "proposal_hash", ""),
                ),
            )
            return cursor.fetchone() is not None

    def lookup_changeset_group_receipt(
        self,
        *,
        command_id_hash: str,
        context: OwnerTruthCommandContext,
    ) -> Mapping[str, Any] | None:
        _assert_owner_context(context)
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT payload_hash, result_payload
                FROM owner_truth.memory_changeset_group_receipts
                WHERE vault_id = %s AND owner_subject_id = %s AND command_id_hash = %s
                """,
                (context.vault_id, context.owner_subject_id, command_id_hash),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        payload = self._json_mapping(row.get("result_payload"), field="group receipt payload")
        return {**payload, "payloadHash": str(row.get("payload_hash") or "")}

    def persist_changeset_group_receipt(
        self,
        *,
        result: Any,
        command: Any,
        context: OwnerTruthCommandContext,
    ) -> None:
        _assert_owner_context(context)
        payload_method = getattr(result, "payload", None)
        command_id_hash = str(getattr(command, "command_id_hash", ""))
        payload_hash = str(getattr(command, "payload_hash", ""))
        if not callable(payload_method) or not command_id_hash or not payload_hash:
            raise OwnerTruthCandidateReviewConflict("ChangeSet group receipt is malformed")
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT payload_hash
                FROM owner_truth.memory_changeset_group_receipts
                WHERE vault_id = %s AND command_id_hash = %s
                FOR UPDATE
                """,
                (context.vault_id, command_id_hash),
            )
            existing = cursor.fetchone()
            if existing is not None:
                if str(existing.get("payload_hash") or "") != payload_hash:
                    raise OwnerTruthCandidateReviewConflict(
                        "group commandId cannot be reused with different content"
                    )
                return
            cursor.execute(
                """
                INSERT INTO owner_truth.memory_changeset_group_receipts (
                    id, vault_id, owner_subject_id, group_proposal_id,
                    group_proposal_hash, command_id_hash, payload_hash,
                    base_memory_revision, applied_memory_revision, result_payload
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                self._adapt_params(
                    (
                        result.group_receipt_id,
                        context.vault_id,
                        context.owner_subject_id,
                        result.group_proposal_id,
                        result.group_proposal_hash,
                        command_id_hash,
                        payload_hash,
                        result.base_memory_revision,
                        result.applied_memory_revision,
                        payload_method(),
                    )
                ),
            )

    def decide(
        self,
        *,
        command: OwnerTruthCandidateReviewCommand,
        context: OwnerTruthCommandContext,
        allow_correction: bool = False,
    ) -> OwnerTruthCandidateReviewResult:
        _assert_owner_context(context)
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked",
                (f"owner-truth-candidate-command:{context.vault_id}:{command.command_id_hash}",),
            )
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked",
                (f"owner-truth-candidate:{context.vault_id}:{command.candidate_id}",),
            )
            existing = self._receipt_by_command(cursor, vault_id=context.vault_id, command_id_hash=command.command_id_hash)
            if existing is not None:
                return self._deduplicated_result(cursor, existing=existing, command=command, context=context)

            vault = self._active_vault(cursor, context=context, lock=True)
            candidate = self._locked_candidate(cursor, candidate_id=command.candidate_id, context=context)
            self._assert_candidate_live(cursor, candidate=candidate, context=context, vault=vault)
            if not allow_correction:
                _assert_generic_activation_allowed(candidate)
            record = command.write_record(candidate=candidate, context=context)
            self._assert_proposal_binding(
                cursor,
                candidate=candidate,
                command=command,
                record=record,
                context=context,
            )
            self._assert_candidate_has_no_receipt(cursor, candidate=candidate)

            cursor.execute(
                """
                UPDATE owner_truth.memory_candidates
                SET decision_status = %s
                WHERE vault_id = %s
                  AND id = %s
                  AND decision_status = 'pending'
                  AND row_version = %s
                RETURNING row_version, decision_status
                """,
                (
                    record.decision.value,
                    record.vault_id,
                    record.candidate_id,
                    record.expected_candidate_version,
                ),
            )
            updated = cursor.fetchone()
            if updated is None:
                raise OwnerTruthCandidateVersionConflict(
                    expected_version=record.expected_candidate_version,
                    current_version=candidate.row_version,
                )
            cursor.execute(
                """
                INSERT INTO owner_truth.decision_receipts (
                    id, vault_id, candidate_id, decision, actor_subject_id,
                    authority_epoch, policy_version, rationale_hash,
                    command_id_hash, payload_hash, expected_candidate_version,
                    candidate_before_hash, candidate_after_hash, decision_basis,
                    authorization_evidence, expected_change_set_id,
                    expected_proposal_hash
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                self._adapt_params(
                    (
                        record.receipt_id,
                        record.vault_id,
                        record.candidate_id,
                        record.decision.value,
                        record.actor_subject_id,
                        record.authority_epoch,
                        record.policy_version,
                        _reason_hash(record.reason_code),
                        record.command_id_hash,
                        record.payload_hash,
                        record.expected_candidate_version,
                        record.candidate_before_hash,
                        record.candidate_after_hash,
                        dict(record.decision_basis),
                        _authorization_capture_payload(record.authorization_capture),
                        record.expected_change_set_id,
                        record.expected_proposal_hash,
                    )
                ),
            )
            if record.corrected_value is not None:
                cursor.execute(
                    """
                    INSERT INTO owner_truth.candidate_decision_values (
                        id, vault_id, candidate_id, decision_receipt_id,
                        content_schema_version, content_hash, content
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    self._adapt_params(
                        (
                            record.corrected_value_id,
                            record.vault_id,
                            record.candidate_id,
                            record.receipt_id,
                            record.corrected_value_schema_version,
                            record.candidate_after_hash,
                            dict(record.corrected_value),
                        )
                    ),
                )

        return OwnerTruthCandidateReviewResult(
            outcome="created",
            receipt_id=record.receipt_id,
            candidate_id=record.candidate_id,
            decision=record.decision,
            candidate_row_version=int(updated["row_version"]),
            candidate_before_hash=record.candidate_before_hash,
            candidate_after_hash=record.candidate_after_hash,
            corrected_value_id=record.corrected_value_id,
        )

    def lookup_decision_result(
        self,
        *,
        candidate_id: str,
        command_id_hash: str,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthCandidateDecisionLookupResult:
        """Read one committed command result without locks or side effects."""

        _assert_owner_context(context)
        with self._cursor() as cursor:
            vault = self._active_vault(cursor, context=context, lock=False)
            cursor.execute(
                """
                SELECT c.id, c.decision_status, c.row_version, c.authority_epoch,
                    receipt.id AS receipt_id, receipt.decision,
                    receipt.actor_subject_id, receipt.expected_candidate_version,
                    receipt.candidate_before_hash, receipt.candidate_after_hash,
                    receipt.expected_change_set_id, receipt.expected_proposal_hash,
                    correction.id AS corrected_value_id,
                    changeset.activation_outcome,
                    changeset.target_memory_id, changeset.target_memory_version_id,
                    changeset.target_memory_version,
                    version.memory_id AS written_memory_id,
                    version.id AS written_memory_version_id,
                    version.version_number AS written_memory_version,
                    version.content_hash AS written_content_hash
                FROM owner_truth.memory_candidates AS c
                LEFT JOIN owner_truth.decision_receipts AS receipt
                  ON receipt.vault_id = c.vault_id
                 AND receipt.candidate_id = c.id
                 AND receipt.command_id_hash = %s
                LEFT JOIN owner_truth.candidate_decision_values AS correction
                  ON correction.vault_id = receipt.vault_id
                 AND correction.decision_receipt_id = receipt.id
                LEFT JOIN owner_truth.memory_changesets AS changeset
                  ON changeset.vault_id = receipt.vault_id
                 AND changeset.decision_receipt_id = receipt.id
                LEFT JOIN owner_truth.memory_versions AS version
                  ON version.vault_id = receipt.vault_id
                 AND (
                    version.decision_receipt_id = receipt.id
                    OR version.id = changeset.target_memory_version_id
                 )
                WHERE c.vault_id = %s
                  AND c.id = %s
                  AND c.owner_subject_id = %s
                """,
                (
                    command_id_hash,
                    context.vault_id,
                    candidate_id,
                    context.owner_subject_id,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                raise OwnerTruthCandidateReviewAccessDenied(
                    "Candidate does not exist in this Owner Vault"
                )
            if row.get("receipt_id") is None:
                return OwnerTruthCandidateDecisionLookupResult(result="notObserved")
            if str(row.get("actor_subject_id") or "") != context.owner_subject_id:
                raise OwnerTruthCandidateReviewAccessDenied(
                    "DecisionReceipt does not belong to this Owner Vault"
                )

            decision = CandidateDecision(str(row["decision"]))
            if decision in {CandidateDecision.REJECTED, CandidateDecision.INVALIDATED}:
                activation = OwnerTruthMemoryActivationResult(
                    outcome="notApplicable",
                    receipt_id=str(row["receipt_id"]),
                    candidate_id=candidate_id,
                    decision=decision,
                    memory_id=None,
                    memory_version_id=None,
                    memory_version=None,
                    authority_epoch=None,
                    content_hash=None,
                )
            else:
                memory_id = row.get("written_memory_id") or row.get("target_memory_id")
                memory_version_id = (
                    row.get("written_memory_version_id")
                    or row.get("target_memory_version_id")
                )
                memory_version = (
                    row.get("written_memory_version")
                    or row.get("target_memory_version")
                )
                if not memory_id or not memory_version_id or memory_version is None:
                    raise OwnerTruthCandidateReviewConflict(
                        "persisted DecisionReceipt is missing its MemoryVersion result"
                    )
                activation = OwnerTruthMemoryActivationResult(
                    outcome=str(row.get("activation_outcome") or "created"),
                    receipt_id=str(row["receipt_id"]),
                    candidate_id=candidate_id,
                    decision=decision,
                    memory_id=str(memory_id),
                    memory_version_id=str(memory_version_id),
                    memory_version=int(memory_version),
                    authority_epoch=int(row.get("authority_epoch") or vault["authority_epoch"]),
                    content_hash=(
                        str(row["written_content_hash"])
                        if row.get("written_content_hash")
                        else str(row["candidate_after_hash"])
                    ),
                )

            review = OwnerTruthCandidateReviewResult(
                outcome="created",
                receipt_id=str(row["receipt_id"]),
                candidate_id=candidate_id,
                decision=decision,
                candidate_row_version=int(row["row_version"]),
                candidate_before_hash=str(row["candidate_before_hash"]),
                candidate_after_hash=str(row["candidate_after_hash"]),
                corrected_value_id=(
                    str(row["corrected_value_id"])
                    if row.get("corrected_value_id")
                    else None
                ),
            )
            return OwnerTruthCandidateDecisionLookupResult(
                result="found",
                expected_candidate_version=int(row["expected_candidate_version"]),
                candidate_before_hash=str(row["candidate_before_hash"]),
                expected_change_set_id=(
                    str(row["expected_change_set_id"])
                    if row.get("expected_change_set_id")
                    else None
                ),
                expected_proposal_hash=(
                    str(row["expected_proposal_hash"])
                    if row.get("expected_proposal_hash")
                    else None
                ),
                decision_result=OwnerTruthCandidateDecisionActivationResult(
                    review=review,
                    memory_activation=activation,
                    memory_revision=self._memory_revision_for_read(
                        cursor,
                        vault_id=context.vault_id,
                    ),
                ),
            )

    def memory_revision(self, *, context: OwnerTruthCommandContext) -> int:
        """Read the current Vault-wide V5 formal-memory revision."""

        _assert_owner_context(context)
        with self._cursor() as cursor:
            self._active_vault(cursor, context=context, lock=False)
            cursor.execute(
                """
                SELECT revision
                FROM owner_truth.memory_revisions
                WHERE vault_id = %s
                """,
                (context.vault_id,),
            )
            row = cursor.fetchone()
        return 0 if row is None else int(row["revision"])

    @staticmethod
    def _memory_revision_for_update(cursor: Any, *, vault_id: str) -> int:
        cursor.execute(
            """
            INSERT INTO owner_truth.memory_revisions (vault_id, revision)
            VALUES (%s, 0)
            ON CONFLICT (vault_id) DO NOTHING
            """,
            (vault_id,),
        )
        cursor.execute(
            """
            SELECT revision
            FROM owner_truth.memory_revisions
            WHERE vault_id = %s
            FOR UPDATE
            """,
            (vault_id,),
        )
        row = cursor.fetchone()
        if row is None:  # pragma: no cover - INSERT/SELECT transaction invariant
            raise OwnerTruthCandidateReviewConflict("formal memory revision is unavailable")
        return int(row["revision"])

    @staticmethod
    def _memory_revision_for_read(cursor: Any, *, vault_id: str) -> int:
        cursor.execute(
            """
            SELECT revision
            FROM owner_truth.memory_revisions
            WHERE vault_id = %s
            """,
            (vault_id,),
        )
        row = cursor.fetchone()
        return 0 if row is None else int(row["revision"])

    @staticmethod
    def _json_mapping(value: Any, *, field: str) -> Mapping[str, Any]:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise OwnerTruthCandidateReviewConflict(f"{field} is malformed") from exc
        if not isinstance(value, Mapping):
            raise OwnerTruthCandidateReviewConflict(f"{field} is unavailable")
        return value

    def _current_formal_memories(
        self,
        cursor: Any,
        *,
        context: OwnerTruthCommandContext,
        lock: bool,
    ) -> tuple[OwnerTruthCurrentFormalMemory, ...]:
        """Read current, owner-authoritative versions for a V5 ChangeSet."""

        cursor.execute(
            """
            SELECT memory.id AS memory_id,
                version.id AS memory_version_id,
                memory.memory_kind,
                version.version_number,
                version.schema_version,
                version.payload
            FROM owner_truth.memories AS memory
            JOIN owner_truth.memory_versions AS version
              ON version.vault_id = memory.vault_id
             AND version.memory_id = memory.id
             AND version.is_current = TRUE
            WHERE memory.vault_id = %s
              AND memory.owner_subject_id = %s
              AND memory.status = 'active'
            ORDER BY memory.id ASC
            """ + ("FOR UPDATE OF memory, version" if lock else ""),
            (context.vault_id, context.owner_subject_id),
        )
        values: list[OwnerTruthCurrentFormalMemory] = []
        for row in cursor.fetchall():
            payload = self._json_mapping(row.get("payload"), field="current MemoryVersion payload")
            content = payload.get("content")
            evidence_refs = payload.get("evidenceRefs")
            if not isinstance(content, Mapping) or not isinstance(evidence_refs, list):
                raise OwnerTruthCandidateReviewConflict(
                    "current formal MemoryVersion is missing typed content or provenance"
                )
            try:
                values.append(
                    OwnerTruthCurrentFormalMemory(
                        memory_id=str(row["memory_id"]),
                        memory_version_id=str(row["memory_version_id"]),
                        vault_id=context.vault_id,
                        owner_subject_id=context.owner_subject_id,
                        version_number=int(row["version_number"]),
                        memory_kind=str(row["memory_kind"]),
                        content_schema_version=str(row["schema_version"]),
                        content=content,
                        evidence_refs=tuple(
                            item for item in evidence_refs if isinstance(item, Mapping)
                        ),
                    )
                )
            except (OwnerTruthContractError, TypeError, ValueError) as exc:
                raise OwnerTruthCandidateReviewConflict(
                    "current formal MemoryVersion cannot participate in a safe changeset"
                ) from exc
        return tuple(values)

    def _current_formal_memories_for_update(
        self,
        cursor: Any,
        *,
        context: OwnerTruthCommandContext,
    ) -> tuple[OwnerTruthCurrentFormalMemory, ...]:
        return self._current_formal_memories(cursor, context=context, lock=True)

    def _propose_changeset(
        self,
        cursor: Any,
        *,
        candidate: OwnerTruthCandidateSnapshot,
        current_memories: tuple[OwnerTruthCurrentFormalMemory, ...],
        base_memory_revision: int,
        resolved_content: Mapping[str, Any] | None = None,
        resolved_content_schema_version: str | None = None,
    ) -> OwnerTruthMemoryChangeSetProposal | None:
        if candidate.content_schema_version != OWNER_TRUTH_SCHEMA_VERSION_V5:
            return None
        proposal = build_memory_changeset_proposal(
            candidate=candidate,
            current_memories=current_memories,
            base_memory_revision=base_memory_revision,
            resolved_content=resolved_content,
            resolved_content_schema_version=resolved_content_schema_version,
        )
        self._persist_proposed_changeset(cursor, proposal=proposal)
        return proposal

    def _persist_proposed_changeset(
        self,
        cursor: Any,
        *,
        proposal: OwnerTruthMemoryChangeSetProposal,
    ) -> None:
        """Persist a preview as immutable audit material before Owner review."""

        payload = proposal.payload()
        cursor.execute(
            """
            INSERT INTO owner_truth.memory_changeset_proposals (
                id, vault_id, candidate_id, candidate_content_hash,
                candidate_row_version, base_memory_revision, change_set_id,
                proposal_hash, schema_version, payload
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (vault_id, id) DO NOTHING
            """,
            self._adapt_params(
                (
                    proposal.proposal_id,
                    proposal.change_set.vault_id,
                    proposal.change_set.candidate_id,
                    proposal.candidate_content_hash,
                    proposal.candidate_row_version,
                    proposal.change_set.base_memory_revision,
                    proposal.change_set.change_set_id,
                    proposal.proposal_hash,
                    proposal.schema_version,
                    payload,
                )
            ),
        )
        for index, operation in enumerate(proposal.change_set.operations):
            dependencies = [
                before
                for before, after in proposal.change_set.dependencies
                if after == index
            ]
            cursor.execute(
                """
                INSERT INTO owner_truth.memory_changeset_proposal_operations (
                    proposal_id, vault_id, operation_index, operation_kind,
                    candidate_id, target_memory_id, target_memory_version_id,
                    target_memory_version, candidate_assertion, changed_fields,
                    added_evidence_count, reason, depends_on_operation_indexes
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (vault_id, proposal_id, operation_index) DO NOTHING
                """,
                self._adapt_changeset_operation_params(
                    (
                        proposal.proposal_id,
                        proposal.change_set.vault_id,
                        index,
                        operation.kind.value,
                        operation.candidate_id,
                        operation.target_memory_id,
                        operation.target_memory_version_id,
                        operation.target_memory_version,
                        list(operation.candidate_assertion_key),
                        list(operation.changed_fields),
                        operation.added_evidence_count,
                        operation.reason,
                    ),
                    dependencies=dependencies,
                ),
            )

    def _assert_proposal_binding(
        self,
        cursor: Any,
        *,
        candidate: OwnerTruthCandidateSnapshot,
        command: OwnerTruthCandidateReviewCommand,
        record: OwnerTruthCandidateDecisionWriteRecord,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthMemoryChangeSetProposal | None:
        if candidate.content_schema_version != OWNER_TRUTH_SCHEMA_VERSION_V5:
            return None
        if (
            command.expected_change_set_id is None
            or command.expected_proposal_hash is None
            or command.expected_memory_revision is None
        ):
            raise OwnerTruthCandidateReviewConflict(
                "V5 Candidate review requires a proposed ChangeSet and base memory revision"
            )
        current_revision = self._memory_revision_for_update(cursor, vault_id=context.vault_id)
        proposal = self._propose_changeset(
            cursor,
            candidate=candidate,
            current_memories=self._current_formal_memories_for_update(cursor, context=context),
            base_memory_revision=current_revision,
            resolved_content=record.corrected_value,
            resolved_content_schema_version=record.corrected_value_schema_version,
        )
        if proposal is None:  # pragma: no cover - V5 guard
            raise OwnerTruthCandidateReviewConflict("V5 ChangeSet proposal is unavailable")
        if command.expected_memory_revision != proposal.change_set.base_memory_revision:
            raise OwnerTruthCandidateReviewConflict(
                "formal memory revision does not match the proposed ChangeSet"
            )
        if (
            command.expected_change_set_id != proposal.change_set.change_set_id
            or command.expected_proposal_hash != proposal.proposal_hash
        ):
            raise OwnerTruthCandidateReviewConflict(
                "proposed ChangeSet is stale; reload the Candidate review diff"
            )
        return proposal

    @staticmethod
    def _changeset_by_receipt(
        cursor: Any,
        *,
        vault_id: str,
        receipt_id: str,
    ) -> Mapping[str, Any] | None:
        cursor.execute(
            """
            SELECT id, activation_outcome, target_memory_id,
                target_memory_version_id, target_memory_version
            FROM owner_truth.memory_changesets
            WHERE vault_id = %s AND decision_receipt_id = %s
            FOR UPDATE
            """,
            (vault_id, receipt_id),
        )
        return cursor.fetchone()

    @staticmethod
    def _version_by_receipt_or_id(
        cursor: Any,
        *,
        vault_id: str,
        receipt_id: str,
        version_id: str | None,
    ) -> Mapping[str, Any] | None:
        if version_id:
            cursor.execute(
                """
                SELECT memory_id, id AS memory_version_id, version_number, content_hash
                FROM owner_truth.memory_versions
                WHERE vault_id = %s AND id = %s
                FOR UPDATE
                """,
                (vault_id, version_id),
            )
        else:
            cursor.execute(
                """
                SELECT memory_id, id AS memory_version_id, version_number, content_hash
                FROM owner_truth.memory_versions
                WHERE vault_id = %s AND decision_receipt_id = %s
                FOR UPDATE
                """,
                (vault_id, receipt_id),
            )
        return cursor.fetchone()

    def _existing_changeset_activation(
        self,
        cursor: Any,
        *,
        record: Mapping[str, Any],
        receipt_id: str,
        candidate: OwnerTruthCandidateSnapshot,
    ) -> OwnerTruthMemoryActivationResult:
        outcome = str(record.get("activation_outcome") or "")
        if outcome == "notApplicable":
            return OwnerTruthMemoryActivationResult(
                outcome=outcome,
                receipt_id=receipt_id,
                candidate_id=candidate.candidate_id,
                decision=candidate.decision,
                memory_id=None,
                memory_version_id=None,
                memory_version=None,
                authority_epoch=None,
                content_hash=None,
            )
        version = self._version_by_receipt_or_id(
            cursor,
            vault_id=candidate.vault_id,
            receipt_id=receipt_id,
            version_id=(
                str(record.get("target_memory_version_id"))
                if outcome == "duplicate" and record.get("target_memory_version_id")
                else None
            ),
        )
        if version is None:
            raise OwnerTruthCandidateReviewConflict(
                "persisted MemoryChangeSet is missing its MemoryVersion result"
            )
        return OwnerTruthMemoryActivationResult(
            outcome=outcome,
            receipt_id=receipt_id,
            candidate_id=candidate.candidate_id,
            decision=candidate.decision,
            memory_id=str(version["memory_id"]),
            memory_version_id=str(version["memory_version_id"]),
            memory_version=int(version["version_number"]),
            authority_epoch=candidate.authority_epoch,
            content_hash=str(version["content_hash"]),
        )

    def _persist_memory_changeset(
        self,
        cursor: Any,
        *,
        changeset: OwnerTruthMemoryChangeSet,
        receipt_id: str,
        outcome: str,
        applied_memory_revision: int,
    ) -> None:
        operation = changeset.operation
        cursor.execute(
            """
            INSERT INTO owner_truth.memory_changesets (
                id, vault_id, decision_receipt_id, candidate_id,
                base_memory_revision, applied_memory_revision, operation_kind,
                activation_outcome, target_memory_id, target_memory_version_id,
                target_memory_version, candidate_assertion, reason
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            self._adapt_params(
                (
                    changeset.change_set_id,
                    changeset.vault_id,
                    receipt_id,
                    changeset.candidate_id,
                    changeset.base_memory_revision,
                    applied_memory_revision,
                    operation.kind.value,
                    outcome,
                    operation.target_memory_id,
                    operation.target_memory_version_id,
                    operation.target_memory_version,
                    list(operation.candidate_assertion_key),
                    operation.reason,
                )
            ),
        )
        cursor.execute(
            """
            INSERT INTO owner_truth.memory_changeset_operations (
                changeset_id, vault_id, operation_index, operation_kind,
                changed_fields, added_evidence_count
            ) VALUES (%s, %s, 1, %s, %s, %s)
            """,
            self._adapt_params(
                (
                    changeset.change_set_id,
                    changeset.vault_id,
                    operation.kind.value,
                    list(operation.changed_fields),
                    operation.added_evidence_count,
                )
            ),
        )

    def _activate_v5_memory_version(
        self,
        cursor: Any,
        *,
        receipt: Mapping[str, Any],
        candidate: OwnerTruthCandidateSnapshot,
        decision: CandidateDecision,
        context: OwnerTruthCommandContext,
        vault: Mapping[str, Any],
        expected_memory_revision: int | None,
    ) -> OwnerTruthMemoryActivationResult:
        """Apply one typed Candidate as an atomic, audited V5 changeset."""

        receipt_id = str(receipt["id"])
        existing = self._changeset_by_receipt(
            cursor,
            vault_id=context.vault_id,
            receipt_id=receipt_id,
        )
        if existing is not None:
            return self._existing_changeset_activation(
                cursor,
                record=existing,
                receipt_id=receipt_id,
                candidate=candidate,
            )

        current_revision = self._memory_revision_for_update(
            cursor,
            vault_id=context.vault_id,
        )
        if (
            expected_memory_revision is not None
            and expected_memory_revision != current_revision
        ):
            raise OwnerTruthCandidateReviewConflict(
                "formal memory revision does not match expectedMemoryRevision"
            )

        corrected_value = None
        corrected_schema_version = None
        if decision is CandidateDecision.CORRECTED:
            correction = self._corrected_value_by_receipt(
                cursor,
                vault_id=context.vault_id,
                receipt_id=receipt_id,
            )
            if correction is None:
                raise OwnerTruthCandidateReviewConflict(
                    "corrected DecisionReceipt is missing its immutable value"
                )
            corrected_value = correction["content"]
            corrected_schema_version = str(correction["content_schema_version"])

        current_memories = self._current_formal_memories_for_update(
            cursor,
            context=context,
        )
        reviewed_candidate = _candidate_at_review_version(
            candidate,
            expected_candidate_version=receipt.get("expected_candidate_version"),
        )
        proposal = self._propose_changeset(
            cursor,
            candidate=reviewed_candidate,
            current_memories=current_memories,
            base_memory_revision=current_revision,
            resolved_content=corrected_value,
            resolved_content_schema_version=corrected_schema_version,
        )
        if proposal is None:
            raise OwnerTruthCandidateReviewConflict(
                "V5 DecisionReceipt is missing its proposed ChangeSet"
            )
        if not _receipt_changeset_binding_matches(
            receipt,
            change_set_id=proposal.change_set.change_set_id,
            proposal_hash=proposal.proposal_hash,
        ):
            raise OwnerTruthCandidateReviewConflict(
                "review receipt is not bound to the current proposed ChangeSet"
            )

        try:
            changeset_plan = build_memory_changeset_activation_plan(
                candidate=reviewed_candidate,
                receipt_id=receipt_id,
                receipt_decision=decision,
                receipt_after_hash=str(receipt["candidate_after_hash"]),
                current_memories=current_memories,
                base_memory_revision=current_revision,
                resolved_content=corrected_value,
                resolved_content_schema_version=corrected_schema_version,
            )
        except OwnerTruthMemoryChangeSetActivationError as exc:
            raise OwnerTruthCandidateReviewConflict(
                f"formal memory changeset cannot be applied: {exc}"
            ) from exc

        self._assert_candidate_live(
            cursor,
            candidate=candidate,
            context=context,
            vault=vault,
            expected_source_version=changeset_plan.source_version,
        )

        if changeset_plan.writes_memory_version:
            if (
                not changeset_plan.memory_id
                or not changeset_plan.memory_version_id
                or not changeset_plan.payload
                or not changeset_plan.source_id
                or changeset_plan.source_version is None
                or not changeset_plan.memory_kind
            ):
                raise OwnerTruthCandidateReviewConflict(
                    "formal memory changeset write plan is incomplete"
                )
            if changeset_plan.creates_memory_record:
                if (
                    not changeset_plan.perspective_type
                    or not changeset_plan.epistemic_status
                    or not changeset_plan.sensitivity
                    or not changeset_plan.policy_version
                    or changeset_plan.authority_epoch is None
                    or not changeset_plan.content_hash
                    or not changeset_plan.content_schema_version
                ):
                    raise OwnerTruthCandidateReviewConflict(
                        "initial formal memory changeset plan is incomplete"
                    )
                cursor.execute(
                    """
                    INSERT INTO owner_truth.memories (
                        id, vault_id, owner_subject_id, source_id, source_version,
                        memory_kind, perspective_type, epistemic_status, sensitivity,
                        status, policy_version, content_hash, authority_epoch,
                        decision_receipt_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'active', %s, %s, %s, %s)
                    """,
                    (
                        changeset_plan.memory_id,
                        context.vault_id,
                        context.owner_subject_id,
                        changeset_plan.source_id,
                        changeset_plan.source_version,
                        changeset_plan.memory_kind,
                        changeset_plan.perspective_type,
                        changeset_plan.epistemic_status,
                        changeset_plan.sensitivity,
                        changeset_plan.policy_version,
                        changeset_plan.content_hash,
                        changeset_plan.authority_epoch,
                        receipt_id,
                    ),
                )
            else:
                if not changeset_plan.supersedes_version_id:
                    raise OwnerTruthCandidateReviewConflict(
                        "replacement formal memory changeset has no predecessor"
                    )
                cursor.execute(
                    """
                    UPDATE owner_truth.memory_versions
                    SET is_current = FALSE
                    WHERE vault_id = %s AND id = %s AND is_current = TRUE
                    RETURNING id
                    """,
                    (context.vault_id, changeset_plan.supersedes_version_id),
                )
                if cursor.fetchone() is None:
                    raise OwnerTruthCandidateReviewConflict(
                        "formal memory predecessor is no longer current"
                    )

            cursor.execute(
                """
                INSERT INTO owner_truth.memory_versions (
                    id, vault_id, memory_id, version_number, is_current,
                    schema_version, content_hash, payload, source_id,
                    source_version, decision_receipt_id, supersedes_version_id
                ) VALUES (%s, %s, %s, %s, TRUE, %s, %s, %s, %s, %s, %s, %s)
                """,
                self._adapt_params(
                    (
                        changeset_plan.memory_version_id,
                        context.vault_id,
                        changeset_plan.memory_id,
                        changeset_plan.memory_version,
                        changeset_plan.content_schema_version,
                        changeset_plan.content_hash,
                        dict(changeset_plan.payload),
                        changeset_plan.source_id,
                        changeset_plan.source_version,
                        receipt_id,
                        changeset_plan.supersedes_version_id,
                    )
                ),
            )
            if changeset_plan.relation_to_memory_id and changeset_plan.relation_type:
                relation_id = str(
                    uuid5(
                        _MEMORY_CHANGESET_RELATION_NAMESPACE,
                        f"{context.vault_id}:{receipt_id}:{changeset_plan.memory_id}:"
                        f"{changeset_plan.relation_to_memory_id}:{changeset_plan.relation_type}",
                    )
                )
                cursor.execute(
                    """
                    INSERT INTO owner_truth.memory_relations (
                        id, vault_id, from_memory_id, to_memory_id, relation_type
                    ) VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (vault_id, from_memory_id, to_memory_id, relation_type) DO NOTHING
                    """,
                    (
                        relation_id,
                        context.vault_id,
                        changeset_plan.memory_id,
                        changeset_plan.relation_to_memory_id,
                        changeset_plan.relation_type,
                    ),
                )
            cursor.execute(
                """
                UPDATE owner_truth.memory_revisions
                SET revision = revision + 1, updated_at = NOW()
                WHERE vault_id = %s
                RETURNING revision
                """,
                (context.vault_id,),
            )
            revision_row = cursor.fetchone()
            if revision_row is None:  # pragma: no cover - locked-row invariant
                raise OwnerTruthCandidateReviewConflict("formal memory revision did not advance")
            applied_memory_revision = int(revision_row["revision"])
        else:
            applied_memory_revision = current_revision

        self._persist_memory_changeset(
            cursor,
            changeset=changeset_plan.change_set,
            receipt_id=receipt_id,
            outcome=changeset_plan.outcome,
            applied_memory_revision=applied_memory_revision,
        )
        return OwnerTruthMemoryActivationResult(
            outcome=changeset_plan.outcome,
            receipt_id=receipt_id,
            candidate_id=candidate.candidate_id,
            decision=decision,
            memory_id=changeset_plan.memory_id,
            memory_version_id=changeset_plan.memory_version_id,
            memory_version=changeset_plan.memory_version,
            authority_epoch=changeset_plan.authority_epoch,
            content_hash=changeset_plan.content_hash,
        )

    def activate_memory_version(
        self,
        *,
        receipt_id: str,
        context: OwnerTruthCommandContext,
        expected_memory_revision: int | None = None,
    ) -> OwnerTruthMemoryActivationResult:
        """Activate exactly one initial MemoryVersion from one DecisionReceipt."""

        _assert_owner_context(context)
        # The schema-backed V5 path is introduced below with its own revision
        # row.  Preserve the legacy activation contract while callers roll out
        # the new optional compare-and-swap field.
        if expected_memory_revision is not None and expected_memory_revision < 0:
            raise OwnerTruthCandidateReviewConflict(
                "expectedMemoryRevision must be non-negative"
            )
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked",
                (f"owner-truth-memory-activation:{context.vault_id}:{receipt_id}",),
            )
            receipt = self._receipt_by_id(
                cursor,
                vault_id=context.vault_id,
                receipt_id=receipt_id,
            )
            if receipt is None:
                raise OwnerTruthCandidateReviewAccessDenied(
                    "DecisionReceipt does not exist in this Owner Vault"
                )
            vault = self._active_vault(cursor, context=context, lock=True)
            candidate = self._locked_candidate(
                cursor,
                candidate_id=str(receipt["candidate_id"]),
                context=context,
            )
            if candidate.decision.value != str(receipt["decision"]):
                raise OwnerTruthCandidateReviewConflict(
                    "DecisionReceipt does not match its terminal Candidate"
                )
            decision = candidate.decision
            if candidate.content_schema_version == OWNER_TRUTH_SCHEMA_VERSION_V5:
                return self._activate_v5_memory_version(
                    cursor,
                    receipt=receipt,
                    candidate=candidate,
                    decision=decision,
                    context=context,
                    vault=vault,
                    expected_memory_revision=expected_memory_revision,
                )
            if decision in {CandidateDecision.REJECTED, CandidateDecision.INVALIDATED}:
                return OwnerTruthMemoryActivationResult(
                    outcome="notApplicable",
                    receipt_id=str(receipt["id"]),
                    candidate_id=candidate.candidate_id,
                    decision=decision,
                    memory_id=None,
                    memory_version_id=None,
                    memory_version=None,
                    authority_epoch=None,
                    content_hash=None,
                )

            corrected_value = None
            corrected_schema_version = None
            if decision is CandidateDecision.CORRECTED:
                correction = self._corrected_value_by_receipt(
                    cursor,
                    vault_id=context.vault_id,
                    receipt_id=str(receipt["id"]),
                )
                if correction is None:
                    raise OwnerTruthCandidateReviewConflict(
                        "corrected DecisionReceipt is missing its immutable value"
                    )
                corrected_value = correction["content"]
                corrected_schema_version = str(correction["content_schema_version"])

            plan = build_memory_activation_plan(
                candidate=candidate,
                receipt_id=str(receipt["id"]),
                receipt_decision=decision,
                receipt_after_hash=str(receipt["candidate_after_hash"]),
                corrected_value=corrected_value,
                corrected_value_schema_version=corrected_schema_version,
            )
            if plan is None:  # defensive: non-activating decisions returned above
                raise OwnerTruthCandidateReviewConflict("terminal decision cannot activate MemoryVersion")
            self._assert_candidate_live(
                cursor,
                candidate=candidate,
                context=context,
                vault=vault,
                expected_source_version=plan.source_version,
            )

            existing = self._memory_by_receipt(
                cursor,
                vault_id=context.vault_id,
                receipt_id=plan.receipt_id,
            )
            if existing is not None:
                current_version = self._current_memory_version(
                    cursor,
                    vault_id=context.vault_id,
                    memory_id=str(existing["id"]),
                )
                if (
                    str(existing["id"]) != plan.memory_id
                    or str(existing["content_hash"]) != plan.content_hash
                    or current_version is None
                    or str(current_version["id"]) != plan.memory_version_id
                    or str(current_version["content_hash"]) != plan.content_hash
                    or int(current_version["version_number"]) != 1
                ):
                    raise OwnerTruthCandidateReviewConflict(
                        "DecisionReceipt already activates a different MemoryVersion"
                    )
                return OwnerTruthMemoryActivationResult(
                    outcome="deduplicated",
                    receipt_id=plan.receipt_id,
                    candidate_id=plan.candidate_id,
                    decision=decision,
                    memory_id=plan.memory_id,
                    memory_version_id=plan.memory_version_id,
                    memory_version=int(current_version["version_number"]),
                    authority_epoch=int(vault["authority_epoch"]),
                    content_hash=plan.content_hash,
                )

            cursor.execute(
                """
                INSERT INTO owner_truth.memories (
                    id, vault_id, owner_subject_id, source_id, source_version,
                    memory_kind, perspective_type, epistemic_status, sensitivity,
                    status, policy_version, content_hash, authority_epoch,
                    decision_receipt_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'active', %s, %s, %s, %s)
                """,
                (
                    plan.memory_id,
                    plan.vault_id,
                    plan.owner_subject_id,
                    plan.source_id,
                    plan.source_version,
                    plan.memory_kind,
                    plan.perspective_type,
                    plan.epistemic_status,
                    plan.sensitivity,
                    plan.policy_version,
                    plan.content_hash,
                    plan.authority_epoch,
                    plan.receipt_id,
                ),
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.memory_versions (
                    id, vault_id, memory_id, version_number, is_current,
                    schema_version, content_hash, payload, source_id,
                    source_version, decision_receipt_id
                ) VALUES (%s, %s, %s, 1, TRUE, %s, %s, %s, %s, %s, %s)
                """,
                self._adapt_params(
                    (
                        plan.memory_version_id,
                        plan.vault_id,
                        plan.memory_id,
                        plan.content_schema_version,
                        plan.content_hash,
                        dict(plan.payload),
                        plan.source_id,
                        plan.source_version,
                        plan.receipt_id,
                    )
                ),
            )
        return OwnerTruthMemoryActivationResult(
            outcome="created",
            receipt_id=plan.receipt_id,
            candidate_id=plan.candidate_id,
            decision=decision,
            memory_id=plan.memory_id,
            memory_version_id=plan.memory_version_id,
            memory_version=1,
            authority_epoch=plan.authority_epoch,
            content_hash=plan.content_hash,
        )

    def activate_correction_memory_version(
        self,
        *,
        receipt_id: str,
        correction_request_id: str,
        memory_id: str,
        expected_memory_version_id: str,
        reason_code_hash: str,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthMemoryCorrectionActivationResult:
        """Atomically replace one current MemoryVersion after Owner correction.

        Unlike ``activate_memory_version`` this never inserts another
        ``MemoryRecord``.  It locks the cited version, flips only its current
        pointer, and writes its successor with correction-source provenance.
        The surrounding caller keeps the correction request, resolution ledger
        and async projection intent in the same Unit of Work.
        """

        _assert_owner_context(context)
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked",
                (
                    "owner-truth-memory-correction:"
                    f"{context.vault_id}:{memory_id}:{expected_memory_version_id}",
                ),
            )
            receipt = self._receipt_by_id(
                cursor,
                vault_id=context.vault_id,
                receipt_id=receipt_id,
            )
            if receipt is None:
                raise OwnerTruthCandidateReviewAccessDenied(
                    "DecisionReceipt does not exist in this Owner Vault"
                )
            vault = self._active_vault(cursor, context=context, lock=True)
            candidate = self._locked_candidate(
                cursor,
                candidate_id=str(receipt["candidate_id"]),
                context=context,
            )
            if candidate.decision is not CandidateDecision.CORRECTED:
                raise OwnerTruthMemoryCorrectionError(
                    "correction resolver requires a corrected Candidate"
                )
            if candidate.decision.value != str(receipt["decision"]):
                raise OwnerTruthCandidateReviewConflict(
                    "DecisionReceipt does not match its terminal Candidate"
                )
            candidate_source_versions = {
                int(reference["sourceVersion"])
                for reference in candidate.source_refs
                if str(reference.get("sourceId") or "") == candidate.source_id
            }
            if len(candidate_source_versions) != 1:
                raise OwnerTruthMemoryCorrectionError(
                    "correction Candidate source version is ambiguous"
                )
            self._assert_candidate_live(
                cursor,
                candidate=candidate,
                context=context,
                vault=vault,
                expected_source_version=next(iter(candidate_source_versions)),
            )
            corrected_value = self._corrected_value_by_receipt(
                cursor,
                vault_id=context.vault_id,
                receipt_id=str(receipt["id"]),
            )
            if corrected_value is None:
                raise OwnerTruthCandidateReviewConflict(
                    "corrected DecisionReceipt is missing its immutable value"
                )
            cursor.execute(
                """
                SELECT memory.owner_subject_id, memory.status AS memory_status,
                    memory.authority_epoch AS memory_authority_epoch,
                    version.id AS memory_version_id, version.version_number,
                    version.is_current, version.schema_version, version.content_hash,
                    version.payload, version.source_id, version.source_version
                FROM owner_truth.memories AS memory
                JOIN owner_truth.memory_versions AS version
                  ON version.vault_id = memory.vault_id
                 AND version.memory_id = memory.id
                WHERE memory.vault_id = %s
                  AND memory.id = %s
                  AND version.id = %s
                FOR UPDATE OF memory, version
                """,
                (context.vault_id, memory_id, expected_memory_version_id),
            )
            row = cursor.fetchone()
            if (
                row is None
                or str(row["owner_subject_id"]) != context.owner_subject_id
                or str(row["memory_status"]) != "active"
                or int(row["memory_authority_epoch"]) != int(vault["authority_epoch"])
                or bool(row["is_current"]) is not True
            ):
                raise OwnerTruthMemoryCorrectionError(
                    "cited MemoryVersion is no longer current and cannot be corrected"
                )
            payload = row["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            predecessor = OwnerTruthMemoryVersionSnapshot(
                vault_id=context.vault_id,
                memory_id=memory_id,
                memory_version_id=str(row["memory_version_id"]),
                version_number=int(row["version_number"]),
                is_current=bool(row["is_current"]),
                authority_epoch=int(vault["authority_epoch"]),
                source_id=str(row["source_id"]),
                source_version=int(row["source_version"]),
                content_schema_version=str(row["schema_version"]),
                content_hash=str(row["content_hash"]),
                payload=payload or {},
            )
            plan = build_memory_correction_plan(
                candidate=candidate,
                receipt_id=str(receipt["id"]),
                receipt_after_hash=str(receipt["candidate_after_hash"]),
                corrected_value=corrected_value["content"],
                corrected_value_schema_version=str(corrected_value["content_schema_version"]),
                correction_request_id=correction_request_id,
                reason_code_hash=reason_code_hash,
                predecessor=predecessor,
            )
            cursor.execute(
                """
                SELECT id, memory_id, version_number, content_hash, is_current
                FROM owner_truth.memory_versions
                WHERE vault_id = %s AND decision_receipt_id = %s
                FOR UPDATE
                """,
                (context.vault_id, plan.receipt_id),
            )
            existing = cursor.fetchone()
            if existing is not None:
                if (
                    str(existing["memory_id"]) != plan.memory_id
                    or int(existing["version_number"]) != plan.replacement_memory_version
                    or str(existing["id"]) != plan.replacement_memory_version_id
                    or str(existing["content_hash"]) != plan.content_hash
                    or bool(existing["is_current"]) is not True
                ):
                    raise OwnerTruthCandidateReviewConflict(
                        "DecisionReceipt already supersedes a different MemoryVersion"
                    )
                return OwnerTruthMemoryCorrectionActivationResult(
                    outcome="deduplicated",
                    receipt_id=plan.receipt_id,
                    candidate_id=plan.candidate_id,
                    correction_request_id=plan.correction_request_id,
                    memory_id=plan.memory_id,
                    superseded_memory_version_id=plan.superseded_memory_version_id,
                    superseded_memory_version=plan.superseded_memory_version,
                    replacement_memory_version_id=plan.replacement_memory_version_id,
                    replacement_memory_version=plan.replacement_memory_version,
                    authority_epoch=plan.authority_epoch,
                    content_hash=plan.content_hash,
                )
            cursor.execute(
                """
                UPDATE owner_truth.memory_versions
                SET is_current = FALSE
                WHERE vault_id = %s AND id = %s AND is_current = TRUE
                RETURNING id
                """,
                (context.vault_id, plan.superseded_memory_version_id),
            )
            if cursor.fetchone() is None:
                raise OwnerTruthMemoryCorrectionError(
                    "cited MemoryVersion lost its current pointer before replacement"
                )
            cursor.execute(
                """
                INSERT INTO owner_truth.memory_versions (
                    id, vault_id, memory_id, version_number, is_current,
                    schema_version, content_hash, payload, source_id,
                    source_version, decision_receipt_id, supersedes_version_id
                ) VALUES (%s, %s, %s, %s, TRUE, %s, %s, %s, %s, %s, %s, %s)
                """,
                self._adapt_params(
                    (
                        plan.replacement_memory_version_id,
                        plan.vault_id,
                        plan.memory_id,
                        plan.replacement_memory_version,
                        plan.content_schema_version,
                        plan.content_hash,
                        dict(plan.payload),
                        plan.source_id,
                        plan.source_version,
                        plan.receipt_id,
                        plan.superseded_memory_version_id,
                    )
                ),
            )
        return OwnerTruthMemoryCorrectionActivationResult(
            outcome="created",
            receipt_id=plan.receipt_id,
            candidate_id=plan.candidate_id,
            correction_request_id=plan.correction_request_id,
            memory_id=plan.memory_id,
            superseded_memory_version_id=plan.superseded_memory_version_id,
            superseded_memory_version=plan.superseded_memory_version,
            replacement_memory_version_id=plan.replacement_memory_version_id,
            replacement_memory_version=plan.replacement_memory_version,
            authority_epoch=plan.authority_epoch,
            content_hash=plan.content_hash,
        )

    def _deduplicated_result(
        self,
        cursor: Any,
        *,
        existing: Mapping[str, Any],
        command: OwnerTruthCandidateReviewCommand,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthCandidateReviewResult:
        expected = {
            "candidate_id": command.candidate_id,
            "expected_candidate_version": command.expected_candidate_version,
            "payload_hash": command.payload_hash,
            "actor_subject_id": context.actor_subject_id,
            "policy_version": context.policy_version,
        }
        if any(str(existing[key]) != str(value) for key, value in expected.items()):
            raise OwnerTruthCandidateReviewConflict(
                "commandId cannot be reused with a different Candidate decision"
            )
        _assert_replay_authorization_capture(
            existing=_authorization_capture_from_value(
                existing.get("authorization_evidence")
            ),
            expected=context.authorization_capture,
        )
        candidate = self._locked_candidate(cursor, candidate_id=command.candidate_id, context=context)
        if candidate.decision.value != str(existing["decision"]):
            raise OwnerTruthCandidateReviewConflict(
                "decision receipt does not match its terminal Candidate"
            )
        corrected_value_id = None
        cursor.execute(
            """
            SELECT id FROM owner_truth.candidate_decision_values
            WHERE vault_id = %s AND decision_receipt_id = %s
            """,
            (context.vault_id, existing["id"]),
        )
        correction = cursor.fetchone()
        if correction is not None:
            corrected_value_id = str(correction["id"])
        return OwnerTruthCandidateReviewResult(
            outcome="deduplicated",
            receipt_id=str(existing["id"]),
            candidate_id=candidate.candidate_id,
            decision=candidate.decision,
            candidate_row_version=candidate.row_version,
            candidate_before_hash=str(existing["candidate_before_hash"]),
            candidate_after_hash=str(existing["candidate_after_hash"]),
            corrected_value_id=corrected_value_id,
        )

    @staticmethod
    def _candidate_from_row(row: Mapping[str, Any]) -> OwnerTruthCandidateSnapshot:
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return OwnerTruthCandidateSnapshot(
            candidate_id=str(row["id"]),
            vault_id=str(row["vault_id"]),
            owner_subject_id=str(row["owner_subject_id"]),
            source_id=str(row["source_id"]),
            memory_kind=str(row["candidate_kind"]),
            perspective_type=str(row["perspective_type"]),
            epistemic_status=str(row["epistemic_status"]),
            sensitivity=str(row["sensitivity"]),
            decision=str(row["decision_status"]),
            policy_version=str(row["policy_version"]),
            authority_epoch=int(row["authority_epoch"]),
            row_version=int(row["row_version"]),
            content_hash=str(row["content_hash"]),
            content_schema_version=str(row["payload_schema_version"]),
            payload=payload or {},
        )

    def _active_vault(self, cursor: Any, *, context: OwnerTruthCommandContext, lock: bool) -> Mapping[str, Any]:
        cursor.execute(
            """
            SELECT owner_subject_id, authority_epoch, status
            FROM owner_truth.vaults
            WHERE vault_id = %s
            """ + ("FOR SHARE" if lock else ""),
            (context.vault_id,),
        )
        vault = cursor.fetchone()
        if (
            vault is None
            or str(vault["owner_subject_id"]) != context.owner_subject_id
            or str(vault["status"]) != "active"
        ):
            raise OwnerTruthCandidateReviewAccessDenied("Vault is not active for this Owner")
        return vault

    def _locked_candidate(
        self,
        cursor: Any,
        *,
        candidate_id: str,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthCandidateSnapshot:
        cursor.execute(
            """
            SELECT id, vault_id, owner_subject_id, source_id,
                candidate_kind, perspective_type, epistemic_status, sensitivity,
                decision_status, policy_version, authority_epoch, row_version,
                content_hash, payload_schema_version, payload
            FROM owner_truth.memory_candidates
            WHERE vault_id = %s AND id = %s AND owner_subject_id = %s
            FOR UPDATE
            """,
            (context.vault_id, candidate_id, context.owner_subject_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise OwnerTruthCandidateReviewAccessDenied("Candidate does not exist in this Owner Vault")
        return self._candidate_from_row(row)

    def _assert_candidate_live(
        self,
        cursor: Any,
        *,
        candidate: OwnerTruthCandidateSnapshot,
        context: OwnerTruthCommandContext,
        vault: Mapping[str, Any],
        expected_source_version: int | None = None,
    ) -> None:
        if candidate.authority_epoch != int(vault["authority_epoch"]):
            raise OwnerTruthCandidateReviewSourceInactive("Candidate authority epoch is stale")
        cursor.execute(
            """
            SELECT owner_subject_id, authority_epoch, state, source_version
            FROM owner_truth.sources
            WHERE vault_id = %s AND id = %s
            FOR SHARE
            """,
            (candidate.vault_id, candidate.source_id),
        )
        source = cursor.fetchone()
        if (
            source is None
            or str(source["owner_subject_id"]) != context.owner_subject_id
            or int(source["authority_epoch"]) != int(vault["authority_epoch"])
            or str(source["state"]) != "active"
            or (
                expected_source_version is not None
                and int(source["source_version"]) != expected_source_version
            )
        ):
            raise OwnerTruthCandidateReviewSourceInactive("Candidate Source is no longer active")

    @staticmethod
    def _assert_candidate_has_no_receipt(cursor: Any, *, candidate: OwnerTruthCandidateSnapshot) -> None:
        cursor.execute(
            """
            SELECT id FROM owner_truth.decision_receipts
            WHERE vault_id = %s AND candidate_id = %s
            FOR UPDATE
            """,
            (candidate.vault_id, candidate.candidate_id),
        )
        if cursor.fetchone() is not None:
            raise OwnerTruthCandidateReviewConflict("Candidate already has an immutable DecisionReceipt")

    def _receipt_by_command(self, cursor: Any, *, vault_id: str, command_id_hash: str) -> Mapping[str, Any] | None:
        cursor.execute(
            """
            SELECT id, candidate_id, decision, actor_subject_id, policy_version,
                payload_hash, expected_candidate_version,
                candidate_before_hash, candidate_after_hash, authorization_evidence,
                expected_change_set_id, expected_proposal_hash
            FROM owner_truth.decision_receipts
            WHERE vault_id = %s AND command_id_hash = %s
            FOR UPDATE
            """,
            (vault_id, command_id_hash),
        )
        return cursor.fetchone()

    @staticmethod
    def _receipt_by_id(
        cursor: Any,
        *,
        vault_id: str,
        receipt_id: str,
    ) -> Mapping[str, Any] | None:
        cursor.execute(
            """
            SELECT id, candidate_id, decision, expected_candidate_version,
                candidate_after_hash,
                expected_change_set_id, expected_proposal_hash
            FROM owner_truth.decision_receipts
            WHERE vault_id = %s AND id = %s
            FOR UPDATE
            """,
            (vault_id, receipt_id),
        )
        return cursor.fetchone()

    @staticmethod
    def _corrected_value_by_receipt(
        cursor: Any,
        *,
        vault_id: str,
        receipt_id: str,
    ) -> Mapping[str, Any] | None:
        cursor.execute(
            """
            SELECT content_schema_version, content_hash, content
            FROM owner_truth.candidate_decision_values
            WHERE vault_id = %s AND decision_receipt_id = %s
            FOR UPDATE
            """,
            (vault_id, receipt_id),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        content = row["content"]
        if isinstance(content, str):
            content = json.loads(content)
        return {
            "content": content or {},
            "content_hash": str(row["content_hash"]),
            "content_schema_version": str(row["content_schema_version"]),
        }

    @staticmethod
    def _memory_by_receipt(
        cursor: Any,
        *,
        vault_id: str,
        receipt_id: str,
    ) -> Mapping[str, Any] | None:
        cursor.execute(
            """
            SELECT id, content_hash
            FROM owner_truth.memories
            WHERE vault_id = %s AND decision_receipt_id = %s
            FOR UPDATE
            """,
            (vault_id, receipt_id),
        )
        return cursor.fetchone()

    @staticmethod
    def _current_memory_version(
        cursor: Any,
        *,
        vault_id: str,
        memory_id: str,
    ) -> Mapping[str, Any] | None:
        cursor.execute(
            """
            SELECT id, version_number, content_hash
            FROM owner_truth.memory_versions
            WHERE vault_id = %s AND memory_id = %s AND is_current = TRUE
            FOR UPDATE
            """,
            (vault_id, memory_id),
        )
        return cursor.fetchone()

    @staticmethod
    def _adapt_params(values: tuple[Any, ...]) -> tuple[Any, ...]:
        try:
            from psycopg.types.json import Jsonb
        except ImportError:  # pragma: no cover - production dependency
            return tuple(
                _canonical_json(value)
                if isinstance(value, (Mapping, list, tuple))
                else value
                for value in values
            )
        return tuple(
            Jsonb(value) if isinstance(value, (Mapping, list, tuple)) else value
            for value in values
        )

    @classmethod
    def _adapt_changeset_operation_params(
        cls,
        values: tuple[Any, ...],
        *,
        dependencies: list[int],
    ) -> tuple[Any, ...]:
        # Proposal assertions are JSONB, but dependency indexes are a native
        # PostgreSQL integer[] and must not pass through the JSONB adapter.
        return cls._adapt_params(values) + (list(dependencies),)

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - production dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)


class OwnerTruthCandidateReviewService:
    def __init__(self, store: OwnerTruthCandidateReviewStore):
        self._store = store

    def list_pending(self, *, context: OwnerTruthCommandContext) -> tuple[OwnerTruthCandidateInboxItem, ...]:
        _assert_owner_context(context)
        with self._request_unit_of_work(
            correlation_id=f"owner-truth-candidate-inbox-{context.vault_id}",
            command_id="ownerTruthCandidateInbox",
        ):
            return self._store.owner_truth_candidate_review_repository().list_pending(context=context)

    def memory_revision(self, *, context: OwnerTruthCommandContext) -> int:
        """Read the Vault-wide revision used for an optimistic review write."""

        _assert_owner_context(context)
        with self._request_unit_of_work(
            correlation_id=f"owner-truth-memory-revision-{context.vault_id}",
            command_id="ownerTruthMemoryRevision",
        ):
            repository = self._store.owner_truth_candidate_review_repository()
            reader = getattr(repository, "memory_revision", None)
            if not callable(reader):
                return 0
            return int(reader(context=context))

    def list_review_history(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> tuple[OwnerTruthCandidateReviewHistoryItem, ...]:
        _assert_owner_context(context)
        with self._request_unit_of_work(
            correlation_id=f"owner-truth-candidate-review-history-{context.vault_id}",
            command_id="ownerTruthCandidateReviewHistory",
        ):
            return self._store.owner_truth_candidate_review_repository().list_review_history(
                context=context
            )

    def list_memory_version_history(
        self,
        *,
        memory_id: str,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthMemoryVersionHistory:
        _assert_owner_context(context)
        normalized_memory_id = str(memory_id or "").strip()
        if not normalized_memory_id:
            raise OwnerTruthCandidateReviewAccessDenied(
                "Memory does not exist in this Owner Vault"
            )
        with self._request_unit_of_work(
            correlation_id=f"owner-truth-memory-version-history-{context.vault_id}",
            command_id="ownerTruthMemoryVersionHistory",
        ):
            return self._store.owner_truth_candidate_review_repository().list_memory_version_history(
                memory_id=normalized_memory_id,
                context=context,
            )

    def preview_changeset(
        self,
        *,
        candidate_id: str,
        context: OwnerTruthCommandContext,
        corrected_value: Mapping[str, Any] | None = None,
        corrected_value_schema_version: str | None = None,
    ) -> OwnerTruthMemoryChangeSetProposal | None:
        """Return a pre-review ChangeSet that a later decision must bind."""

        _assert_owner_context(context)
        normalized_candidate_id = str(candidate_id or "").strip()
        if not normalized_candidate_id:
            raise OwnerTruthCandidateReviewAccessDenied(
                "Candidate does not exist in this Owner Vault"
            )
        with self._request_unit_of_work(
            correlation_id=(
                f"owner-truth-candidate-changeset-preview-{normalized_candidate_id}"
            ),
            command_id=f"ownerTruthCandidateChangesetPreview:{normalized_candidate_id}",
        ):
            repository = self._store.owner_truth_candidate_review_repository()
            preview = getattr(repository, "preview_changeset", None)
            if not callable(preview):
                raise OwnerTruthCandidateReviewConflict(
                    "Candidate ChangeSet preview is unavailable"
                )
            return preview(
                candidate_id=normalized_candidate_id,
                context=context,
                corrected_value=corrected_value,
                corrected_value_schema_version=corrected_value_schema_version,
            )

    def preview_changeset_result(
        self,
        *,
        candidate_id: str,
        context: OwnerTruthCommandContext,
        corrected_value: Mapping[str, Any] | None = None,
        corrected_value_schema_version: str | None = None,
    ) -> OwnerTruthCandidateChangeSetPreview:
        """Return a proposal and its source/correction binding from one read."""

        _assert_owner_context(context)
        normalized_candidate_id = str(candidate_id or "").strip()
        if not normalized_candidate_id:
            raise OwnerTruthCandidateReviewAccessDenied(
                "Candidate does not exist in this Owner Vault"
            )
        with self._request_unit_of_work(
            correlation_id=(
                f"owner-truth-candidate-changeset-preview-{normalized_candidate_id}"
            ),
            command_id=f"ownerTruthCandidateChangesetPreview:{normalized_candidate_id}",
        ):
            repository = self._store.owner_truth_candidate_review_repository()
            preview = getattr(repository, "preview_changeset_result", None)
            if not callable(preview):
                raise OwnerTruthCandidateReviewConflict(
                    "Candidate ChangeSet preview binding is unavailable"
                )
            return preview(
                candidate_id=normalized_candidate_id,
                context=context,
                corrected_value=corrected_value,
                corrected_value_schema_version=corrected_value_schema_version,
            )

    def decide(
        self,
        *,
        command: OwnerTruthCandidateReviewCommand,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthCandidateReviewResult:
        _assert_owner_context(context)
        with self._request_unit_of_work(
            correlation_id=f"owner-truth-candidate-decision-{command.command_id_hash}",
            command_id=command.command_id_hash,
        ):
            return self._store.owner_truth_candidate_review_repository().decide(
                command=command,
                context=context,
            )

    def lookup_decision_result(
        self,
        *,
        candidate_id: str,
        command_id: str,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthCandidateDecisionLookupResult:
        """Read durable outcome for one command without replaying its write."""

        _assert_owner_context(context)
        normalized_candidate_id = str(candidate_id or "").strip()
        normalized_command_id = str(command_id or "").strip()
        if not normalized_candidate_id:
            raise OwnerTruthCandidateReviewAccessDenied(
                "Candidate does not exist in this Owner Vault"
            )
        allowed = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.:-"
        if (
            not normalized_command_id
            or len(normalized_command_id) > 128
            or any(character not in allowed for character in normalized_command_id)
        ):
            raise OwnerTruthCandidateReviewError(
                "command_id must be an opaque identifier"
            )
        command_id_hash = sha256(normalized_command_id.encode("utf-8")).hexdigest()
        with self._request_unit_of_work(
            correlation_id=f"owner-truth-decision-result-{command_id_hash}",
            command_id=f"lookup:{command_id_hash}",
        ):
            repository = self._store.owner_truth_candidate_review_repository()
            lookup = getattr(repository, "lookup_decision_result", None)
            if not callable(lookup):
                raise OwnerTruthCandidateReviewConflict(
                    "Candidate decision result lookup is unavailable"
                )
            return lookup(
                candidate_id=normalized_candidate_id,
                command_id_hash=command_id_hash,
                context=context,
            )

    def decide_and_activate(
        self,
        *,
        command: OwnerTruthCandidateReviewCommand,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthCandidateDecisionActivationResult:
        """Commit the review receipt and initial MemoryVersion as one UoW.

        The review port remains callable on its own for the earlier 01-04
        contract.  The public-facing QA route uses this method once 01-05 is
        enabled, so a fresh accepted/corrected decision cannot commit without
        its receipt-derived immutable memory.
        """

        _assert_owner_context(context)
        with self._request_unit_of_work(
            correlation_id=f"owner-truth-decision-memory-{command.command_id_hash}",
            command_id=command.command_id_hash,
        ):
            repository = self._store.owner_truth_candidate_review_repository()
            transaction = getattr(repository, "transaction", None)
            scope = transaction() if callable(transaction) else nullcontext()
            with scope:
                review = repository.decide(command=command, context=context)
                activation = repository.activate_memory_version(
                    receipt_id=review.receipt_id,
                    context=context,
                    expected_memory_revision=command.expected_memory_revision,
                )
                projection_effect = self._write_projection_rebuild_effect(
                    context=context,
                    activation=activation,
                )
                revision_reader = getattr(repository, "memory_revision", None)
                memory_revision = (
                    int(revision_reader(context=context))
                    if callable(revision_reader)
                    else None
                )
            return OwnerTruthCandidateDecisionActivationResult(
                review=review,
                memory_activation=activation,
                projection_effect=projection_effect,
                memory_revision=memory_revision,
            )

    def _write_projection_rebuild_effect(
        self,
        *,
        context: OwnerTruthCommandContext,
        activation: OwnerTruthMemoryActivationResult,
    ) -> EffectReceiptSummary | None:
        """Persist a disabled compatibility-rebuild intent when the kernel exists.

        Production ``PostgresStore`` exposes this writer only while the same
        request UoW is active, so a failed intent write rolls the DecisionReceipt
        and MemoryVersion back. Lightweight legacy test doubles may omit the
        kernel until their own migration path opts into it.
        """

        if activation.outcome not in {"created", "revised", "deduplicated"} or activation.memory_version_id is None:
            return None
        factory = getattr(self._store, "effect_kernel_repository", None)
        if not callable(factory):
            return None
        return factory().accept(
            build_memory_projection_rebuild_effect_intent(
                context=context,
                activation=activation,
            )
        )

    def _request_unit_of_work(
        self,
        *,
        correlation_id: str,
        command_id: str,
    ) -> ContextManager[Any]:
        """Use Postgres UoW when present; semantic doubles own their own lock."""

        factory = getattr(self._store, "request_unit_of_work", None)
        if callable(factory):
            return factory(correlation_id=correlation_id, command_id=command_id)
        return nullcontext()


__all__ = [
    "InMemoryOwnerTruthCandidateReviewRepository",
    "OwnerTruthCandidateDecisionActivationResult",
    "OwnerTruthCandidateInboxItem",
    "OwnerTruthCandidateReviewHistoryItem",
    "OwnerTruthCandidateReviewResult",
    "OwnerTruthCandidateReviewService",
    "OwnerTruthMemoryVersionHistory",
    "OwnerTruthMemoryVersionHistoryItem",
    "PostgresOwnerTruthCandidateReviewRepository",
]
