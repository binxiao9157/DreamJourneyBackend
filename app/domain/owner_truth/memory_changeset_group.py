"""Atomic, owner-visible review groups for related V5 Candidates.

The existing ChangeSet contract deliberately models one Candidate at a time.
This module composes those immutable previews into an explicit dependency
group without inventing another formal-memory authority.  A group preview is
calculated against one base revision, then each member is simulated in
topological order so the Owner sees the exact before/after facts that will be
applied together.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import json
from typing import Any, Iterable, Mapping
from uuid import UUID, uuid5

from .candidate_decisions import (
    CandidateReviewAction,
    OwnerTruthCandidateReviewCommand,
    OwnerTruthCandidateSnapshot,
)
from .contracts import CandidateDecision, OwnerTruthContractError, require_nonblank, require_uuid
from .memory_changeset import (
    OwnerTruthCurrentFormalMemory,
    OwnerTruthMemoryChangeSetProposal,
    build_memory_changeset_proposal,
)
from .memory_changeset_activation import (
    OwnerTruthMemoryChangeSetActivationPlan,
    build_memory_changeset_activation_plan,
)
from .ontology import OWNER_TRUTH_SCHEMA_VERSION_V5
from .source_commands import OwnerTruthCommandContext


OWNER_TRUTH_MEMORY_CHANGESET_GROUP_SCHEMA_VERSION = "owner-truth-memory-changeset-group-v1"
_GROUP_PROPOSAL_NAMESPACE = UUID("f8cbf4bc-606b-4f19-9e0b-ec9c1d9d47cc")
_GROUP_RECEIPT_NAMESPACE = UUID("d35ac024-1a96-4c8c-9b68-5f0d4ffda9fe")


class OwnerTruthMemoryChangeSetGroupError(OwnerTruthContractError):
    """A related Candidate group cannot safely be previewed or committed."""


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise OwnerTruthMemoryChangeSetGroupError(
            "ChangeSet group values must be JSON serializable"
        ) from exc


def _digest(value: Any) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _json_copy(value: Any, *, field: str) -> Any:
    try:
        return json.loads(_canonical_json(value))
    except OwnerTruthMemoryChangeSetGroupError as exc:
        raise OwnerTruthMemoryChangeSetGroupError(f"{field} must be JSON serializable") from exc


@dataclass(frozen=True)
class OwnerTruthMemoryChangeSetGroupSelection:
    """One Candidate decision requested as part of an atomic dependency group."""

    candidate_id: str
    expected_candidate_version: int
    action: CandidateReviewAction
    corrected_value: Mapping[str, Any] | None
    corrected_value_schema_version: str | None
    reason_code: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_id", require_uuid(self.candidate_id, field="candidate_id"))
        if (
            not isinstance(self.expected_candidate_version, int)
            or isinstance(self.expected_candidate_version, bool)
            or self.expected_candidate_version < 1
        ):
            raise OwnerTruthMemoryChangeSetGroupError(
                "expected_candidate_version must be positive"
            )
        try:
            object.__setattr__(self, "action", CandidateReviewAction(self.action))
        except ValueError as exc:
            raise OwnerTruthMemoryChangeSetGroupError("group action is unsupported") from exc
        object.__setattr__(self, "reason_code", require_nonblank(self.reason_code, field="reason_code"))
        if self.action is CandidateReviewAction.CORRECT:
            if not isinstance(self.corrected_value, Mapping):
                raise OwnerTruthMemoryChangeSetGroupError(
                    "correct action requires corrected_value"
                )
            schema = require_nonblank(
                self.corrected_value_schema_version,
                field="corrected_value_schema_version",
            )
            copied = _json_copy(dict(self.corrected_value), field="corrected_value")
            if not isinstance(copied, dict):  # pragma: no cover - JSON invariant
                raise OwnerTruthMemoryChangeSetGroupError("corrected_value must be an object")
            object.__setattr__(self, "corrected_value", copied)
            object.__setattr__(self, "corrected_value_schema_version", schema)
        elif self.corrected_value is not None or self.corrected_value_schema_version is not None:
            raise OwnerTruthMemoryChangeSetGroupError(
                "only correct action may include corrected_value"
            )

    def payload(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "candidateId": self.candidate_id,
            "expectedCandidateVersion": self.expected_candidate_version,
            "action": self.action.value,
            "reasonCode": self.reason_code,
        }
        if self.corrected_value is not None:
            value["correctedValue"] = _json_copy(self.corrected_value, field="corrected_value")
            value["correctedValueSchemaVersion"] = self.corrected_value_schema_version
        return value


@dataclass(frozen=True)
class OwnerTruthMemoryChangeSetGroupDependency:
    """A before/after edge over group selections, not a client-side hint."""

    before_candidate_id: str
    after_candidate_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "before_candidate_id",
            require_uuid(self.before_candidate_id, field="before_candidate_id"),
        )
        object.__setattr__(
            self,
            "after_candidate_id",
            require_uuid(self.after_candidate_id, field="after_candidate_id"),
        )
        if self.before_candidate_id == self.after_candidate_id:
            raise OwnerTruthMemoryChangeSetGroupError(
                "group dependency cannot target the same Candidate"
            )

    def payload(self) -> dict[str, str]:
        return {
            "beforeCandidateId": self.before_candidate_id,
            "afterCandidateId": self.after_candidate_id,
        }


@dataclass(frozen=True)
class OwnerTruthMemoryChangeSetGroupCommand:
    """Owner command used for both group preview and terminal confirmation."""

    command_id: str
    selections: tuple[OwnerTruthMemoryChangeSetGroupSelection, ...]
    dependencies: tuple[OwnerTruthMemoryChangeSetGroupDependency, ...]
    expected_memory_revision: int | None = None
    expected_group_proposal_id: str | None = None
    expected_group_proposal_hash: str | None = None

    def __post_init__(self) -> None:
        normalized_command_id = str(self.command_id or "").strip()
        if not normalized_command_id or len(normalized_command_id) > 128:
            raise OwnerTruthMemoryChangeSetGroupError("group command_id is invalid")
        object.__setattr__(self, "command_id", normalized_command_id)
        selections = tuple(self.selections)
        if len(selections) < 2:
            raise OwnerTruthMemoryChangeSetGroupError(
                "an atomic ChangeSet group requires at least two Candidates"
            )
        if any(
            not isinstance(selection, OwnerTruthMemoryChangeSetGroupSelection)
            for selection in selections
        ):
            raise OwnerTruthMemoryChangeSetGroupError("group selection is malformed")
        candidate_ids = [selection.candidate_id for selection in selections]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise OwnerTruthMemoryChangeSetGroupError("group cannot repeat a Candidate")
        object.__setattr__(self, "selections", selections)
        dependencies = tuple(self.dependencies)
        if not dependencies:
            raise OwnerTruthMemoryChangeSetGroupError(
                "an atomic ChangeSet group requires an explicit dependency"
            )
        if any(
            not isinstance(dependency, OwnerTruthMemoryChangeSetGroupDependency)
            for dependency in dependencies
        ):
            raise OwnerTruthMemoryChangeSetGroupError("group dependency is malformed")
        candidate_set = set(candidate_ids)
        if any(
            dependency.before_candidate_id not in candidate_set
            or dependency.after_candidate_id not in candidate_set
            for dependency in dependencies
        ):
            raise OwnerTruthMemoryChangeSetGroupError(
                "group dependency references a Candidate outside the group"
            )
        deduplicated_dependencies = tuple(
            sorted(
                {
                    (dependency.before_candidate_id, dependency.after_candidate_id)
                    for dependency in dependencies
                }
            )
        )
        object.__setattr__(
            self,
            "dependencies",
            tuple(
                OwnerTruthMemoryChangeSetGroupDependency(
                    before_candidate_id=before,
                    after_candidate_id=after,
                )
                for before, after in deduplicated_dependencies
            ),
        )
        if self.expected_memory_revision is not None and (
            not isinstance(self.expected_memory_revision, int)
            or isinstance(self.expected_memory_revision, bool)
            or self.expected_memory_revision < 0
        ):
            raise OwnerTruthMemoryChangeSetGroupError(
                "expected_memory_revision must be non-negative when provided"
            )
        if (self.expected_group_proposal_id is None) != (
            self.expected_group_proposal_hash is None
        ):
            raise OwnerTruthMemoryChangeSetGroupError(
                "expected group proposal ID and hash must be provided together"
            )
        if self.expected_group_proposal_id is not None:
            object.__setattr__(
                self,
                "expected_group_proposal_id",
                require_uuid(
                    self.expected_group_proposal_id,
                    field="expected_group_proposal_id",
                ),
            )
            normalized_hash = str(self.expected_group_proposal_hash or "").strip().lower()
            if len(normalized_hash) != 64 or any(
                character not in "0123456789abcdef" for character in normalized_hash
            ):
                raise OwnerTruthMemoryChangeSetGroupError(
                    "expected_group_proposal_hash must be a SHA-256 digest"
                )
            object.__setattr__(self, "expected_group_proposal_hash", normalized_hash)

    @property
    def command_id_hash(self) -> str:
        return sha256(self.command_id.encode("utf-8")).hexdigest()

    @property
    def review_intent_hash(self) -> str:
        """Stable identity for the reviewed graph, independent of request id.

        Preview and confirm are deliberately two different requests.  A group
        simulation nevertheless has to use the same child receipt identifiers
        on both requests, because an earlier child activation contributes
        evidence to a dependent child's exact before/after preview.  The
        request command id is therefore unsafe here; this fingerprint binds
        only the owner-visible selections and their dependency graph.
        """

        return _digest(
            {
                "selections": [selection.payload() for selection in self.selections],
                "dependencies": [dependency.payload() for dependency in self.dependencies],
            }
        )

    @property
    def payload_hash(self) -> str:
        return _digest(
            {
                "selections": [selection.payload() for selection in self.selections],
                "dependencies": [dependency.payload() for dependency in self.dependencies],
                "expectedMemoryRevision": self.expected_memory_revision,
                "expectedGroupProposalId": self.expected_group_proposal_id,
                "expectedGroupProposalHash": self.expected_group_proposal_hash,
            }
        )

    def child_command(
        self,
        *,
        selection: OwnerTruthMemoryChangeSetGroupSelection,
        proposal: OwnerTruthMemoryChangeSetProposal,
    ) -> OwnerTruthCandidateReviewCommand:
        """Build a deterministic child command bound to the simulated preview."""

        child_command_id = (
            f"group.{self.review_intent_hash}.{selection.candidate_id.replace('-', '')}"
        )
        return OwnerTruthCandidateReviewCommand(
            command_id=child_command_id,
            candidate_id=selection.candidate_id,
            expected_candidate_version=selection.expected_candidate_version,
            action=selection.action,
            corrected_value=selection.corrected_value,
            corrected_value_schema_version=(
                selection.corrected_value_schema_version or OWNER_TRUTH_SCHEMA_VERSION_V5
            ),
            reason_code=selection.reason_code,
            expected_memory_revision=proposal.change_set.base_memory_revision,
            expected_change_set_id=proposal.change_set.change_set_id,
            expected_proposal_hash=proposal.proposal_hash,
        )


@dataclass(frozen=True)
class OwnerTruthMemoryChangeSetGroupMember:
    operation_index: int
    selection: OwnerTruthMemoryChangeSetGroupSelection
    proposal: OwnerTruthMemoryChangeSetProposal
    anticipated_outcome: str
    applied_memory_revision: int

    def __post_init__(self) -> None:
        if self.operation_index < 0:
            raise OwnerTruthMemoryChangeSetGroupError("group operation index must be non-negative")
        if not isinstance(self.selection, OwnerTruthMemoryChangeSetGroupSelection):
            raise OwnerTruthMemoryChangeSetGroupError("group member selection is invalid")
        if not isinstance(self.proposal, OwnerTruthMemoryChangeSetProposal):
            raise OwnerTruthMemoryChangeSetGroupError("group member proposal is invalid")
        if self.selection.candidate_id != self.proposal.change_set.candidate_id:
            raise OwnerTruthMemoryChangeSetGroupError(
                "group member proposal does not match its Candidate"
            )
        if self.applied_memory_revision < self.proposal.change_set.base_memory_revision:
            raise OwnerTruthMemoryChangeSetGroupError(
                "group member applied revision precedes its proposal"
            )

    def payload(self) -> dict[str, Any]:
        return {
            "operationIndex": self.operation_index,
            "candidateId": self.selection.candidate_id,
            "action": self.selection.action.value,
            "reasonCode": self.selection.reason_code,
            "candidateVersion": self.selection.expected_candidate_version,
            "proposedChangeSet": self.proposal.payload(),
            "anticipatedOutcome": self.anticipated_outcome,
            "appliedMemoryRevision": self.applied_memory_revision,
        }


@dataclass(frozen=True)
class OwnerTruthMemoryChangeSetGroupProposal:
    proposal_id: str
    proposal_hash: str
    vault_id: str
    owner_subject_id: str
    base_memory_revision: int
    members: tuple[OwnerTruthMemoryChangeSetGroupMember, ...]
    dependencies: tuple[OwnerTruthMemoryChangeSetGroupDependency, ...]
    schema_version: str = OWNER_TRUTH_MEMORY_CHANGESET_GROUP_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "proposal_id", require_uuid(self.proposal_id, field="proposal_id"))
        hash_value = str(self.proposal_hash or "").strip().lower()
        if len(hash_value) != 64 or any(character not in "0123456789abcdef" for character in hash_value):
            raise OwnerTruthMemoryChangeSetGroupError("proposal_hash must be a SHA-256 digest")
        object.__setattr__(self, "proposal_hash", hash_value)
        object.__setattr__(self, "vault_id", require_nonblank(self.vault_id, field="vault_id"))
        object.__setattr__(
            self,
            "owner_subject_id",
            require_nonblank(self.owner_subject_id, field="owner_subject_id"),
        )
        if self.base_memory_revision < 0:
            raise OwnerTruthMemoryChangeSetGroupError("base_memory_revision must be non-negative")
        members = tuple(self.members)
        if len(members) < 2 or any(
            not isinstance(member, OwnerTruthMemoryChangeSetGroupMember) for member in members
        ):
            raise OwnerTruthMemoryChangeSetGroupError("group proposal members are invalid")
        if [member.operation_index for member in members] != list(range(len(members))):
            raise OwnerTruthMemoryChangeSetGroupError("group proposal member ordering is invalid")
        candidate_ids = [member.selection.candidate_id for member in members]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise OwnerTruthMemoryChangeSetGroupError("group proposal repeats a Candidate")
        dependencies = tuple(self.dependencies)
        if not dependencies:
            raise OwnerTruthMemoryChangeSetGroupError("group proposal requires dependencies")
        object.__setattr__(self, "members", members)
        object.__setattr__(self, "dependencies", dependencies)
        if self.schema_version != OWNER_TRUTH_MEMORY_CHANGESET_GROUP_SCHEMA_VERSION:
            raise OwnerTruthMemoryChangeSetGroupError("group proposal schema version is unsupported")

    def payload(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "groupProposalId": self.proposal_id,
            "groupProposalHash": self.proposal_hash,
            "vaultId": self.vault_id,
            "baseMemoryRevision": self.base_memory_revision,
            "members": [member.payload() for member in self.members],
            "dependencies": [dependency.payload() for dependency in self.dependencies],
        }


def _topological_selections(
    *,
    selections: Iterable[OwnerTruthMemoryChangeSetGroupSelection],
    dependencies: Iterable[OwnerTruthMemoryChangeSetGroupDependency],
) -> tuple[OwnerTruthMemoryChangeSetGroupSelection, ...]:
    """Return a stable dependency order and reject cycles/disconnected groups."""

    ordered = tuple(selections)
    by_id = {selection.candidate_id: selection for selection in ordered}
    original_index = {selection.candidate_id: index for index, selection in enumerate(ordered)}
    predecessors: dict[str, set[str]] = {candidate_id: set() for candidate_id in by_id}
    successors: dict[str, set[str]] = {candidate_id: set() for candidate_id in by_id}
    for dependency in dependencies:
        predecessors[dependency.after_candidate_id].add(dependency.before_candidate_id)
        successors[dependency.before_candidate_id].add(dependency.after_candidate_id)

    # A group is meaningful only when every selection participates in the one
    # dependency component.  Unrelated candidates remain independently reviewable.
    reachable = {ordered[0].candidate_id}
    frontier = list(reachable)
    while frontier:
        current = frontier.pop()
        for neighbor in successors[current] | predecessors[current]:
            if neighbor not in reachable:
                reachable.add(neighbor)
                frontier.append(neighbor)
    if len(reachable) != len(ordered):
        raise OwnerTruthMemoryChangeSetGroupError(
            "unrelated Candidates must be submitted as separate review groups"
        )

    remaining = {candidate_id: set(values) for candidate_id, values in predecessors.items()}
    ready = sorted(
        (candidate_id for candidate_id, values in remaining.items() if not values),
        key=original_index.__getitem__,
    )
    result: list[OwnerTruthMemoryChangeSetGroupSelection] = []
    while ready:
        candidate_id = ready.pop(0)
        result.append(by_id[candidate_id])
        for successor in sorted(successors[candidate_id], key=original_index.__getitem__):
            remaining[successor].discard(candidate_id)
            if not remaining[successor] and successor not in ready:
                ready.append(successor)
                ready.sort(key=original_index.__getitem__)
    if len(result) != len(ordered):
        raise OwnerTruthMemoryChangeSetGroupError("group dependency contains a cycle")
    return tuple(result)


def _virtual_current_memories(
    *,
    current: tuple[OwnerTruthCurrentFormalMemory, ...],
    plan: OwnerTruthMemoryChangeSetActivationPlan,
    candidate: OwnerTruthCandidateSnapshot,
) -> tuple[OwnerTruthCurrentFormalMemory, ...]:
    if not plan.writes_memory_version:
        return current
    if (
        not plan.memory_id
        or not plan.memory_version_id
        or plan.memory_version is None
        or plan.payload is None
        or plan.content_schema_version is None
    ):
        raise OwnerTruthMemoryChangeSetGroupError("group activation plan is incomplete")
    content = plan.payload.get("content") if isinstance(plan.payload, Mapping) else None
    refs = plan.payload.get("evidenceRefs") if isinstance(plan.payload, Mapping) else None
    if not isinstance(content, Mapping) or not isinstance(refs, list):
        raise OwnerTruthMemoryChangeSetGroupError("group activation payload is invalid")
    replacement = OwnerTruthCurrentFormalMemory(
        memory_id=plan.memory_id,
        memory_version_id=plan.memory_version_id,
        vault_id=candidate.vault_id,
        owner_subject_id=candidate.owner_subject_id,
        version_number=plan.memory_version,
        memory_kind=plan.memory_kind or candidate.memory_kind.value,
        content_schema_version=plan.content_schema_version,
        content=content,
        evidence_refs=tuple(item for item in refs if isinstance(item, Mapping)),
    )
    survivors = [item for item in current if item.memory_id != replacement.memory_id]
    survivors.append(replacement)
    return tuple(sorted(survivors, key=lambda item: (item.memory_id, item.version_number)))


def build_memory_changeset_group_proposal(
    *,
    command: OwnerTruthMemoryChangeSetGroupCommand,
    candidates: Iterable[OwnerTruthCandidateSnapshot],
    current_memories: Iterable[OwnerTruthCurrentFormalMemory],
    base_memory_revision: int,
    context: OwnerTruthCommandContext,
) -> OwnerTruthMemoryChangeSetGroupProposal:
    """Build the exact group preview that a terminal command must bind.

    The function makes no writes.  It is shared by preview and confirm so a
    fresh lock/recalculation can reject any interleaving formal-memory change.
    """

    if base_memory_revision < 0:
        raise OwnerTruthMemoryChangeSetGroupError("base_memory_revision must be non-negative")
    if context.vault_id == "" or context.owner_subject_id == "":
        raise OwnerTruthMemoryChangeSetGroupError("owner context is required")
    candidate_by_id = {candidate.candidate_id: candidate for candidate in candidates}
    if len(candidate_by_id) != len(command.selections):
        raise OwnerTruthMemoryChangeSetGroupError("group Candidates are incomplete or duplicated")
    ordered = _topological_selections(
        selections=command.selections,
        dependencies=command.dependencies,
    )
    virtual_current = tuple(current_memories)
    virtual_revision = base_memory_revision
    members: list[OwnerTruthMemoryChangeSetGroupMember] = []
    for operation_index, selection in enumerate(ordered):
        candidate = candidate_by_id.get(selection.candidate_id)
        if candidate is None:
            raise OwnerTruthMemoryChangeSetGroupError("group Candidate is unavailable")
        if candidate.content_schema_version != OWNER_TRUTH_SCHEMA_VERSION_V5:
            raise OwnerTruthMemoryChangeSetGroupError(
                "atomic group review requires V5 Candidate payloads"
            )
        if candidate.row_version != selection.expected_candidate_version:
            raise OwnerTruthMemoryChangeSetGroupError(
                "group Candidate version changed; reload the group preview"
            )
        if candidate.decision is not CandidateDecision.PENDING:
            raise OwnerTruthMemoryChangeSetGroupError(
                "terminal Candidate cannot enter a new review group"
            )
        proposal = build_memory_changeset_proposal(
            candidate=candidate,
            current_memories=virtual_current,
            base_memory_revision=virtual_revision,
            resolved_content=selection.corrected_value,
            resolved_content_schema_version=selection.corrected_value_schema_version,
        )
        child_command = command.child_command(selection=selection, proposal=proposal)
        write_record = child_command.write_record(candidate=candidate, context=context)
        terminal_candidate = replace(
            candidate,
            decision=selection.action.terminal_decision,
            row_version=candidate.row_version + 1,
        )
        plan = build_memory_changeset_activation_plan(
            candidate=terminal_candidate,
            receipt_id=write_record.receipt_id,
            receipt_decision=selection.action.terminal_decision,
            receipt_after_hash=write_record.candidate_after_hash,
            current_memories=virtual_current,
            base_memory_revision=virtual_revision,
            resolved_content=write_record.corrected_value,
            resolved_content_schema_version=write_record.corrected_value_schema_version,
        )
        if plan.writes_memory_version:
            virtual_current = _virtual_current_memories(
                current=virtual_current,
                plan=plan,
                candidate=terminal_candidate,
            )
            virtual_revision += 1
        members.append(
            OwnerTruthMemoryChangeSetGroupMember(
                operation_index=operation_index,
                selection=selection,
                proposal=proposal,
                anticipated_outcome=plan.outcome,
                applied_memory_revision=virtual_revision,
            )
        )

    payload_without_identity = {
        "schemaVersion": OWNER_TRUTH_MEMORY_CHANGESET_GROUP_SCHEMA_VERSION,
        "vaultId": context.vault_id,
        "ownerSubjectId": context.owner_subject_id,
        "baseMemoryRevision": base_memory_revision,
        "members": [member.payload() for member in members],
        "dependencies": [dependency.payload() for dependency in command.dependencies],
    }
    proposal_hash = _digest(payload_without_identity)
    proposal_id = str(
        uuid5(
            _GROUP_PROPOSAL_NAMESPACE,
            _canonical_json(
                {
                    "vaultId": context.vault_id,
                    "ownerSubjectId": context.owner_subject_id,
                    "baseMemoryRevision": base_memory_revision,
                    "proposalHash": proposal_hash,
                }
            ),
        )
    )
    return OwnerTruthMemoryChangeSetGroupProposal(
        proposal_id=proposal_id,
        proposal_hash=proposal_hash,
        vault_id=context.vault_id,
        owner_subject_id=context.owner_subject_id,
        base_memory_revision=base_memory_revision,
        members=tuple(members),
        dependencies=command.dependencies,
    )


def group_receipt_id(*, vault_id: str, command_id_hash: str) -> str:
    return str(uuid5(_GROUP_RECEIPT_NAMESPACE, f"{vault_id}:{command_id_hash}"))


__all__ = [
    "OWNER_TRUTH_MEMORY_CHANGESET_GROUP_SCHEMA_VERSION",
    "OwnerTruthMemoryChangeSetGroupCommand",
    "OwnerTruthMemoryChangeSetGroupDependency",
    "OwnerTruthMemoryChangeSetGroupError",
    "OwnerTruthMemoryChangeSetGroupMember",
    "OwnerTruthMemoryChangeSetGroupProposal",
    "OwnerTruthMemoryChangeSetGroupSelection",
    "build_memory_changeset_group_proposal",
    "group_receipt_id",
]
