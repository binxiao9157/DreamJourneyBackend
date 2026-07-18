"""Candidate Inbox and Owner-only terminal DecisionReceipt application service."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from copy import deepcopy
from dataclasses import dataclass, replace
from hashlib import sha256
import json
from threading import RLock
from typing import Any, ContextManager, Mapping, Protocol

from app.async_effects.contracts import EffectReceiptSummary
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
from app.domain.owner_truth.memory_activation import (
    OwnerTruthMemoryActivationResult,
    build_memory_activation_plan,
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
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_memory_projection_effects import (
    build_memory_projection_rebuild_effect_intent,
)


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _reason_hash(reason_code: str) -> str:
    return sha256(reason_code.encode("utf-8")).hexdigest()


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


def _inbox_item(candidate: OwnerTruthCandidateSnapshot, *, created_at: str | None = None) -> OwnerTruthCandidateInboxItem:
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
    )


class InMemoryOwnerTruthCandidateReviewRepository:
    """Thread-safe semantic double for command/CAS/receipt behavior."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._candidates: dict[str, OwnerTruthCandidateSnapshot] = {}
        self._candidate_created_at: dict[str, str | None] = {}
        self._source_states: dict[tuple[str, str], str] = {}
        self._vault_states: dict[str, tuple[str, str, int]] = {}
        self._receipts: dict[str, dict[str, Any]] = {}
        self._candidate_receipts: dict[str, str] = {}
        self._corrected_values: dict[str, dict[str, Any]] = {}
        self._memory_activations: dict[str, dict[str, Any]] = {}

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

    @contextmanager
    def transaction(self):
        with self._lock:
            yield

    def list_pending(self, *, context: OwnerTruthCommandContext) -> tuple[OwnerTruthCandidateInboxItem, ...]:
        _assert_owner_context(context)
        with self._lock:
            vault = self._vault_states.get(context.vault_id)
            if vault is None or vault[0] != context.owner_subject_id or vault[1] != "active":
                raise OwnerTruthCandidateReviewAccessDenied("Vault is not active for this Owner")
            items = [
                _inbox_item(candidate, created_at=self._candidate_created_at.get(candidate.candidate_id))
                for candidate in self._candidates.values()
                if candidate.vault_id == context.vault_id
                and candidate.owner_subject_id == context.owner_subject_id
                and candidate.decision is CandidateDecision.PENDING
                and candidate.authority_epoch == vault[2]
                and self._source_states.get((candidate.vault_id, candidate.source_id)) == "active"
            ]
        return tuple(sorted(items, key=lambda item: item.candidate_id))

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
            }
            self._candidates[candidate.candidate_id] = decided
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

    def activate_memory_version(
        self,
        *,
        receipt_id: str,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthMemoryActivationResult:
        """Create one deterministic initial version from a terminal receipt."""

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
            existing = self._memory_activations.get(plan.receipt_id)
            if existing is not None:
                if (
                    existing["memoryId"] != plan.memory_id
                    or existing["memoryVersionId"] != plan.memory_version_id
                    or existing["contentHash"] != plan.content_hash
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
                    memory_version=int(existing["memoryVersion"]),
                    authority_epoch=int(existing["authorityEpoch"]),
                    content_hash=plan.content_hash,
                )
            self._memory_activations[plan.receipt_id] = {
                "authorityEpoch": plan.authority_epoch,
                "candidateId": plan.candidate_id,
                "contentHash": plan.content_hash,
                "isCurrent": True,
                "memoryId": plan.memory_id,
                "memoryVersionId": plan.memory_version_id,
                "memoryVersion": 1,
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

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "candidates": {
                    candidate_id: {
                        "decision": candidate.decision.value,
                        "payload": deepcopy(dict(candidate.payload)),
                        "rowVersion": candidate.row_version,
                    }
                    for candidate_id, candidate in self._candidates.items()
                },
                "correctedValues": deepcopy(self._corrected_values),
                "memoryActivations": deepcopy(self._memory_activations),
                "receipts": deepcopy(self._receipts),
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
        return tuple(
            _inbox_item(
                self._candidate_from_row(row),
                created_at=(row.get("created_at").isoformat() if row.get("created_at") else None),
            )
            for row in rows
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
                    candidate_before_hash, candidate_after_hash, decision_basis
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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

    def activate_memory_version(
        self,
        *,
        receipt_id: str,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthMemoryActivationResult:
        """Activate exactly one initial MemoryVersion from one DecisionReceipt."""

        _assert_owner_context(context)
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
                candidate_before_hash, candidate_after_hash
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
            SELECT id, candidate_id, decision, candidate_after_hash
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
            return tuple(_canonical_json(value) if isinstance(value, Mapping) else value for value in values)
        return tuple(Jsonb(dict(value)) if isinstance(value, Mapping) else value for value in values)

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
                )
                projection_effect = self._write_projection_rebuild_effect(
                    context=context,
                    activation=activation,
                )
            return OwnerTruthCandidateDecisionActivationResult(
                review=review,
                memory_activation=activation,
                projection_effect=projection_effect,
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

        if activation.memory_version_id is None:
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
    "OwnerTruthCandidateReviewResult",
    "OwnerTruthCandidateReviewService",
    "PostgresOwnerTruthCandidateReviewRepository",
]
