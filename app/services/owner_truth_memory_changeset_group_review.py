"""Atomic review and activation of related Owner Truth Candidate groups."""

from __future__ import annotations

from contextlib import ExitStack, nullcontext
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, ContextManager, Mapping, Protocol

from app.async_effects.contracts import EffectReceiptSummary
from app.domain.owner_truth.candidate_decisions import (
    OwnerTruthCandidateReviewAccessDenied,
    OwnerTruthCandidateReviewConflict,
)
from app.domain.owner_truth.memory_activation import OwnerTruthMemoryActivationResult
from app.domain.owner_truth.memory_changeset_group import (
    OwnerTruthMemoryChangeSetGroupCommand,
    OwnerTruthMemoryChangeSetGroupError,
    OwnerTruthMemoryChangeSetGroupProposal,
    build_memory_changeset_group_proposal,
    group_receipt_id,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_memory_projection_effects import (
    build_memory_projection_rebuild_effect_intent,
)
from app.services.owner_truth_candidate_review import OwnerTruthCandidateReviewResult


@dataclass(frozen=True)
class OwnerTruthMemoryChangeSetGroupCommitMember:
    candidate_id: str
    receipt_id: str
    decision: str
    activation_outcome: str
    memory_id: str | None
    memory_version_id: str | None
    memory_version: int | None

    @classmethod
    def from_results(
        cls,
        *,
        review: OwnerTruthCandidateReviewResult,
        activation: OwnerTruthMemoryActivationResult,
    ) -> "OwnerTruthMemoryChangeSetGroupCommitMember":
        return cls(
            candidate_id=review.candidate_id,
            receipt_id=review.receipt_id,
            decision=review.decision.value,
            activation_outcome=activation.outcome,
            memory_id=activation.memory_id,
            memory_version_id=activation.memory_version_id,
            memory_version=activation.memory_version,
        )

    @classmethod
    def from_persisted(
        cls,
        value: Mapping[str, Any],
    ) -> "OwnerTruthMemoryChangeSetGroupCommitMember":
        try:
            candidate_id = str(value["candidateId"])
            receipt_id = str(value["receiptId"])
            decision = str(value["decision"])
            activation_outcome = str(value["activationOutcome"])
        except (KeyError, TypeError, ValueError) as exc:
            raise OwnerTruthMemoryChangeSetGroupError(
                "persisted group receipt member is malformed"
            ) from exc
        memory_version = value.get("memoryVersion")
        if memory_version is not None:
            try:
                memory_version = int(memory_version)
            except (TypeError, ValueError) as exc:
                raise OwnerTruthMemoryChangeSetGroupError(
                    "persisted group receipt memory version is malformed"
                ) from exc
        return cls(
            candidate_id=candidate_id,
            receipt_id=receipt_id,
            decision=decision,
            activation_outcome=activation_outcome,
            memory_id=(str(value["memoryId"]) if value.get("memoryId") else None),
            memory_version_id=(
                str(value["memoryVersionId"]) if value.get("memoryVersionId") else None
            ),
            memory_version=memory_version,
        )

    def payload(self) -> dict[str, Any]:
        return {
            "candidateId": self.candidate_id,
            "receiptId": self.receipt_id,
            "decision": self.decision,
            "activationOutcome": self.activation_outcome,
            "memoryId": self.memory_id,
            "memoryVersionId": self.memory_version_id,
            "memoryVersion": self.memory_version,
        }


@dataclass(frozen=True)
class OwnerTruthMemoryChangeSetGroupCommitResult:
    outcome: str
    group_receipt_id: str
    group_proposal_id: str
    group_proposal_hash: str
    base_memory_revision: int
    applied_memory_revision: int
    members: tuple[OwnerTruthMemoryChangeSetGroupCommitMember, ...]
    projection_effect_count: int

    @classmethod
    def from_persisted(
        cls,
        value: Mapping[str, Any],
        *,
        outcome: str,
    ) -> "OwnerTruthMemoryChangeSetGroupCommitResult":
        try:
            raw_members = value["members"]
            if not isinstance(raw_members, list):
                raise TypeError("members")
            return cls(
                outcome=outcome,
                group_receipt_id=str(value["groupReceiptId"]),
                group_proposal_id=str(value["groupProposalId"]),
                group_proposal_hash=str(value["groupProposalHash"]),
                base_memory_revision=int(value["baseMemoryRevision"]),
                applied_memory_revision=int(value["appliedMemoryRevision"]),
                members=tuple(
                    OwnerTruthMemoryChangeSetGroupCommitMember.from_persisted(item)
                    for item in raw_members
                    if isinstance(item, Mapping)
                ),
                projection_effect_count=int(value.get("projectionEffectCount") or 0),
            )
        except (KeyError, TypeError, ValueError, OwnerTruthMemoryChangeSetGroupError) as exc:
            if isinstance(exc, OwnerTruthMemoryChangeSetGroupError):
                raise
            raise OwnerTruthMemoryChangeSetGroupError(
                "persisted group receipt is malformed"
            ) from exc

    def payload(self) -> dict[str, Any]:
        return {
            "schemaVersion": "owner-truth-memory-changeset-group-commit-result-v1",
            "status": self.outcome,
            "groupReceiptId": self.group_receipt_id,
            "groupProposalId": self.group_proposal_id,
            "groupProposalHash": self.group_proposal_hash,
            "baseMemoryRevision": self.base_memory_revision,
            "appliedMemoryRevision": self.applied_memory_revision,
            "members": [member.payload() for member in self.members],
            "projectionEffectCount": self.projection_effect_count,
        }


class OwnerTruthMemoryChangeSetGroupReviewStore(Protocol):
    def request_unit_of_work(
        self,
        *,
        correlation_id: str,
        command_id: str,
    ) -> ContextManager[Any]:
        ...

    def owner_truth_candidate_review_repository(self) -> Any:
        ...

    def effect_kernel_repository(self) -> Any:
        ...


def _assert_owner_context(context: OwnerTruthCommandContext) -> None:
    if not isinstance(context, OwnerTruthCommandContext):
        raise OwnerTruthMemoryChangeSetGroupError("owner context is required")
    if context.actor_subject_id != context.owner_subject_id:
        raise OwnerTruthCandidateReviewAccessDenied(
            "only the Vault Owner may confirm a Candidate dependency group"
        )


class OwnerTruthMemoryChangeSetGroupReviewService:
    """Compose preview, CAS, facts, receipts and Outbox in one UoW.

    The repository still owns the only formal-memory write path.  This service
    never calls an external model, and writes derived projection effects only
    after every member fact transition has been accepted inside the same unit
    of work.
    """

    def __init__(self, store: OwnerTruthMemoryChangeSetGroupReviewStore):
        self._store = store

    def preview(
        self,
        *,
        command: OwnerTruthMemoryChangeSetGroupCommand,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthMemoryChangeSetGroupProposal:
        _assert_owner_context(context)
        with self._request_unit_of_work(
            correlation_id=(
                f"owner-truth-memory-changeset-group-preview-{context.vault_id}:"
                f"{command.command_id_hash}"
            ),
            command_id=f"ownerTruthMemoryChangeSetGroupPreview:{command.command_id_hash}",
        ):
            repository = self._store.owner_truth_candidate_review_repository()
            proposal = self._build_fresh_proposal(
                repository=repository,
                command=command,
                context=context,
                lock=False,
            )
            persister = getattr(repository, "persist_changeset_group_proposal", None)
            if not callable(persister):
                raise OwnerTruthMemoryChangeSetGroupError(
                    "Candidate repository does not support group proposal persistence"
                )
            persister(proposal=proposal, context=context)
            return proposal

    def confirm(
        self,
        *,
        command: OwnerTruthMemoryChangeSetGroupCommand,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthMemoryChangeSetGroupCommitResult:
        _assert_owner_context(context)
        if (
            command.expected_memory_revision is None
            or command.expected_group_proposal_id is None
            or command.expected_group_proposal_hash is None
        ):
            raise OwnerTruthMemoryChangeSetGroupError(
                "group confirmation requires the visible proposal and base revision"
            )
        with self._request_unit_of_work(
            correlation_id=(
                f"owner-truth-memory-changeset-group-confirm-{context.vault_id}:"
                f"{command.command_id_hash}"
            ),
            command_id=command.command_id_hash,
        ):
            repository = self._store.owner_truth_candidate_review_repository()
            existing_reader = getattr(repository, "lookup_changeset_group_receipt", None)
            if not callable(existing_reader):
                raise OwnerTruthMemoryChangeSetGroupError(
                    "Candidate repository does not support group receipt replay"
                )
            existing = existing_reader(
                command_id_hash=command.command_id_hash,
                context=context,
            )
            if existing is not None:
                if str(existing.get("payloadHash") or "") != command.payload_hash:
                    raise OwnerTruthCandidateReviewConflict(
                        "group commandId cannot be reused with different content"
                    )
                return OwnerTruthMemoryChangeSetGroupCommitResult.from_persisted(
                    existing,
                    outcome="deduplicated",
                )

            transaction = getattr(repository, "transaction", None)
            candidate_scope = transaction() if callable(transaction) else nullcontext()
            effect_repository = self._effect_repository()
            effect_transaction = getattr(effect_repository, "transaction", None)
            effect_scope = effect_transaction() if callable(effect_transaction) else nullcontext()
            with ExitStack() as stack:
                stack.enter_context(candidate_scope)
                stack.enter_context(effect_scope)
                proposal = self._build_fresh_proposal(
                    repository=repository,
                    command=command,
                    context=context,
                    lock=True,
                )
                self._assert_confirmation_binding(command=command, proposal=proposal)
                self._assert_persisted_preview(
                    repository=repository,
                    proposal=proposal,
                    context=context,
                )

                members: list[OwnerTruthMemoryChangeSetGroupCommitMember] = []
                effects: list[EffectReceiptSummary] = []
                for member in proposal.members:
                    child_command = command.child_command(
                        selection=member.selection,
                        proposal=member.proposal,
                    )
                    review = repository.decide(command=child_command, context=context)
                    activation = repository.activate_memory_version(
                        receipt_id=review.receipt_id,
                        context=context,
                        expected_memory_revision=member.proposal.change_set.base_memory_revision,
                    )
                    if activation.memory_version_id is not None:
                        effects.append(
                            effect_repository.accept(
                                build_memory_projection_rebuild_effect_intent(
                                    context=context,
                                    activation=activation,
                                )
                            )
                        )
                    members.append(
                        OwnerTruthMemoryChangeSetGroupCommitMember.from_results(
                            review=review,
                            activation=activation,
                        )
                    )
                revision_reader = getattr(repository, "memory_revision", None)
                if not callable(revision_reader):
                    raise OwnerTruthMemoryChangeSetGroupError(
                        "Candidate repository does not expose the applied memory revision"
                    )
                applied_revision = int(revision_reader(context=context))
                result = OwnerTruthMemoryChangeSetGroupCommitResult(
                    outcome="created",
                    group_receipt_id=group_receipt_id(
                        vault_id=context.vault_id,
                        command_id_hash=command.command_id_hash,
                    ),
                    group_proposal_id=proposal.proposal_id,
                    group_proposal_hash=proposal.proposal_hash,
                    base_memory_revision=proposal.base_memory_revision,
                    applied_memory_revision=applied_revision,
                    members=tuple(members),
                    projection_effect_count=len(effects),
                )
                receipt_writer = getattr(repository, "persist_changeset_group_receipt", None)
                if not callable(receipt_writer):
                    raise OwnerTruthMemoryChangeSetGroupError(
                        "Candidate repository does not support group receipt persistence"
                    )
                receipt_writer(
                    result=result,
                    command=command,
                    context=context,
                )
                return result

    def lookup_result(
        self,
        *,
        command_id: str,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthMemoryChangeSetGroupCommitResult | None:
        """Read one atomic group receipt without replaying the review write."""

        _assert_owner_context(context)
        normalized_command_id = str(command_id or "").strip()
        allowed = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.:-"
        if (
            not normalized_command_id
            or len(normalized_command_id) > 128
            or any(character not in allowed for character in normalized_command_id)
        ):
            raise OwnerTruthMemoryChangeSetGroupError(
                "command_id must be an opaque identifier"
            )
        command_id_hash = sha256(normalized_command_id.encode("utf-8")).hexdigest()
        with self._request_unit_of_work(
            correlation_id=(
                f"owner-truth-memory-changeset-group-result-{context.vault_id}:"
                f"{command_id_hash}"
            ),
            command_id=f"lookup:{command_id_hash}",
        ):
            repository = self._store.owner_truth_candidate_review_repository()
            reader = getattr(repository, "lookup_changeset_group_receipt", None)
            if not callable(reader):
                raise OwnerTruthMemoryChangeSetGroupError(
                    "Candidate repository does not support group receipt lookup"
                )
            persisted = reader(command_id_hash=command_id_hash, context=context)
            if persisted is None:
                return None
            return OwnerTruthMemoryChangeSetGroupCommitResult.from_persisted(
                persisted,
                outcome="deduplicated",
            )

    def _build_fresh_proposal(
        self,
        *,
        repository: Any,
        command: OwnerTruthMemoryChangeSetGroupCommand,
        context: OwnerTruthCommandContext,
        lock: bool,
    ) -> OwnerTruthMemoryChangeSetGroupProposal:
        reader = getattr(repository, "changeset_group_state", None)
        if not callable(reader):
            raise OwnerTruthMemoryChangeSetGroupError(
                "Candidate repository does not support group review state"
            )
        candidates, current_memories, base_memory_revision = reader(
            candidate_ids=tuple(selection.candidate_id for selection in command.selections),
            context=context,
            lock=lock,
        )
        return build_memory_changeset_group_proposal(
            command=command,
            candidates=candidates,
            current_memories=current_memories,
            base_memory_revision=base_memory_revision,
            context=context,
        )

    @staticmethod
    def _assert_confirmation_binding(
        *,
        command: OwnerTruthMemoryChangeSetGroupCommand,
        proposal: OwnerTruthMemoryChangeSetGroupProposal,
    ) -> None:
        if command.expected_memory_revision != proposal.base_memory_revision:
            raise OwnerTruthCandidateReviewConflict(
                "formal memory revision does not match the proposed ChangeSet group"
            )
        if (
            command.expected_group_proposal_id != proposal.proposal_id
            or command.expected_group_proposal_hash != proposal.proposal_hash
        ):
            raise OwnerTruthCandidateReviewConflict(
                "proposed ChangeSet group is stale; reload the group review diff"
            )

    @staticmethod
    def _assert_persisted_preview(
        *,
        repository: Any,
        proposal: OwnerTruthMemoryChangeSetGroupProposal,
        context: OwnerTruthCommandContext,
    ) -> None:
        reader = getattr(repository, "has_changeset_group_proposal", None)
        if not callable(reader):
            raise OwnerTruthMemoryChangeSetGroupError(
                "Candidate repository does not expose group proposal audit material"
            )
        if not reader(proposal=proposal, context=context):
            raise OwnerTruthCandidateReviewConflict(
                "ChangeSet group preview must be loaded before confirmation"
            )

    def _effect_repository(self) -> Any:
        factory = getattr(self._store, "effect_kernel_repository", None)
        if not callable(factory):
            raise OwnerTruthMemoryChangeSetGroupError(
                "group activation requires the projection effect kernel"
            )
        return factory()

    def _request_unit_of_work(
        self,
        *,
        correlation_id: str,
        command_id: str,
    ) -> ContextManager[Any]:
        factory = getattr(self._store, "request_unit_of_work", None)
        if callable(factory):
            return factory(correlation_id=correlation_id, command_id=command_id)
        return nullcontext()


__all__ = [
    "OwnerTruthMemoryChangeSetGroupCommitMember",
    "OwnerTruthMemoryChangeSetGroupCommitResult",
    "OwnerTruthMemoryChangeSetGroupReviewService",
]
