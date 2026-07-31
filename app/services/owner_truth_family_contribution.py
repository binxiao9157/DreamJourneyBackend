"""Default-off, owner-controlled family contribution admission.

This module deliberately implements only the pre-Memorial M0 lane: an Owner
may grant one accepted family member the ability to contribute a static text
Source to that Owner's existing private Vault.  It does not give the
contributor any read access, Candidate/Memory decision authority, publication
authority, or Voice/Digital Human capability.
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Callable, ContextManager, Mapping, Protocol
from uuid import UUID, uuid5

from app.domain.owner_truth.contracts import (
    OwnerTruthContractError,
    SourceKind,
    require_nonblank,
    require_uuid,
)
from app.domain.owner_truth.source_commands import (
    CreateTextSourceCommand,
    OwnerTruthCommandContext,
    OwnerTruthSourceCommandResult,
)


OWNER_TRUTH_FAMILY_CONTRIBUTION_SCHEMA_VERSION = "owner-truth-family-contribution-v1"
FAMILY_CONTRIBUTION_SCOPE = "submitTextSource"
_GRANT_NAMESPACE = UUID("7cbbf18a-32a5-434a-a1a8-3d4046bb5ced")


class OwnerTruthFamilyContributionError(OwnerTruthContractError):
    """Stable error envelope for the default-off family contribution lane."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _canonical_json(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise OwnerTruthFamilyContributionError("familyContributionCommandInvalid") from exc


def _sha256(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _now_iso(now_provider: Callable[[], datetime]) -> str:
    current = now_provider()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class CreateFamilyContributionGrantCommand:
    """Owner-only command that binds one accepted family relationship to a Vault."""

    command_id: str
    relationship_id: str
    contributor_subject_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", require_nonblank(self.command_id, field="command_id"))
        object.__setattr__(
            self,
            "relationship_id",
            require_nonblank(self.relationship_id, field="relationship_id"),
        )
        object.__setattr__(
            self,
            "contributor_subject_id",
            require_nonblank(self.contributor_subject_id, field="contributor_subject_id"),
        )

    def write_record(self, *, context: OwnerTruthCommandContext) -> "FamilyContributionGrantWriteRecord":
        command_id_hash = _sha256(self.command_id)
        payload = {
            "schemaVersion": OWNER_TRUTH_FAMILY_CONTRIBUTION_SCHEMA_VERSION,
            "relationshipId": self.relationship_id,
            "contributorSubjectId": self.contributor_subject_id,
            "scope": FAMILY_CONTRIBUTION_SCOPE,
        }
        return FamilyContributionGrantWriteRecord(
            grant_id=str(uuid5(_GRANT_NAMESPACE, f"{context.vault_id}:{command_id_hash}")),
            vault_id=context.vault_id,
            owner_subject_id=context.owner_subject_id,
            contributor_subject_id=self.contributor_subject_id,
            relationship_id=self.relationship_id,
            command_id_hash=command_id_hash,
            payload_hash=_sha256(_canonical_json(payload)),
        )


@dataclass(frozen=True)
class FamilyContributionGrantWriteRecord:
    grant_id: str
    vault_id: str
    owner_subject_id: str
    contributor_subject_id: str
    relationship_id: str
    command_id_hash: str
    payload_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "grant_id", require_uuid(self.grant_id, field="grant_id"))
        for field in (
            "vault_id",
            "owner_subject_id",
            "contributor_subject_id",
            "relationship_id",
            "command_id_hash",
            "payload_hash",
        ):
            object.__setattr__(self, field, require_nonblank(getattr(self, field), field=field))


@dataclass(frozen=True)
class RevokeFamilyContributionGrantCommand:
    command_id: str
    grant_id: str
    expected_version: int
    reason: str = "ownerRequested"

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", require_nonblank(self.command_id, field="command_id"))
        object.__setattr__(self, "grant_id", require_uuid(self.grant_id, field="grant_id"))
        if not isinstance(self.expected_version, int) or self.expected_version < 1:
            raise OwnerTruthFamilyContributionError("familyContributionGrantVersionInvalid")
        object.__setattr__(self, "reason", require_nonblank(self.reason, field="reason"))

    def revoke_hashes(self) -> tuple[str, str]:
        command_id_hash = _sha256(self.command_id)
        payload_hash = _sha256(
            _canonical_json(
                {
                    "schemaVersion": OWNER_TRUTH_FAMILY_CONTRIBUTION_SCHEMA_VERSION,
                    "grantId": self.grant_id,
                    "expectedVersion": self.expected_version,
                    "reason": self.reason,
                }
            )
        )
        return command_id_hash, payload_hash


@dataclass(frozen=True)
class SubmitFamilyContributionTextCommand:
    """Contributor-only static Source submission bound to one live grant version."""

    grant_id: str
    expected_grant_version: int
    source_command_id: str
    source_id: str
    text: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "grant_id", require_uuid(self.grant_id, field="grant_id"))
        if not isinstance(self.expected_grant_version, int) or self.expected_grant_version < 1:
            raise OwnerTruthFamilyContributionError("familyContributionGrantVersionInvalid")
        object.__setattr__(
            self,
            "source_command_id",
            require_nonblank(self.source_command_id, field="source_command_id"),
        )
        object.__setattr__(self, "source_id", require_uuid(self.source_id, field="source_id"))
        object.__setattr__(self, "text", require_nonblank(self.text, field="text"))


@dataclass(frozen=True)
class FamilyContributionGrantResult:
    outcome: str
    grant: Mapping[str, Any]

    def public_contract(self) -> dict[str, Any]:
        return {
            "schemaVersion": OWNER_TRUTH_FAMILY_CONTRIBUTION_SCHEMA_VERSION,
            "status": self.outcome,
            "grant": _public_grant(self.grant),
        }


@dataclass(frozen=True)
class FamilyContributionSubmissionResult:
    grant: Mapping[str, Any]
    source: OwnerTruthSourceCommandResult

    def public_contract(self) -> dict[str, Any]:
        return {
            "schemaVersion": OWNER_TRUTH_FAMILY_CONTRIBUTION_SCHEMA_VERSION,
            "grant": _public_grant(self.grant),
            "source": self.source.public_receipt(),
            "candidateExtraction": {"status": "notRequested"},
        }


class OwnerTruthFamilyContributionStore(Protocol):
    def request_unit_of_work(
        self,
        *,
        correlation_id: str,
        command_id: str,
    ) -> ContextManager[Any]:
        ...

    def delegated_access_relationship_scope(
        self,
        *,
        owner_subject_id: str,
        relationship_id: str,
    ) -> ContextManager[Any]:
        ...

    def get_owner_truth_vault(self, vault_id: str) -> Mapping[str, Any] | None:
        ...

    def get_family_relationship(
        self,
        owner_subject_id: str,
        relationship_id: str,
    ) -> Mapping[str, Any] | None:
        ...

    def create_owner_truth_family_contribution_grant(
        self,
        grant: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        ...

    def get_owner_truth_family_contribution_grant(
        self,
        vault_id: str,
        grant_id: str,
    ) -> Mapping[str, Any] | None:
        ...

    def revoke_owner_truth_family_contribution_grant(
        self,
        *,
        vault_id: str,
        owner_subject_id: str,
        grant_id: str,
        expected_version: int,
        revoke_command_id_hash: str,
        revoke_payload_hash: str,
        revoked_at_iso: str,
        reason: str,
    ) -> Mapping[str, Any] | None:
        ...

    def create_owner_truth_source(self, record: Any) -> OwnerTruthSourceCommandResult:
        ...

class OwnerTruthFamilyContributionService:
    """Authorize and admit static family reports without widening Vault access."""

    def __init__(
        self,
        store: OwnerTruthFamilyContributionStore,
        *,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def create_grant(
        self,
        *,
        command: CreateFamilyContributionGrantCommand,
        context: OwnerTruthCommandContext,
    ) -> FamilyContributionGrantResult:
        self._require_owner_context(context)
        record = command.write_record(context=context)
        with self._relationship_scope(
            owner_subject_id=context.owner_subject_id,
            relationship_id=record.relationship_id,
        ):
            with self._store.request_unit_of_work(
                correlation_id=f"owner-truth-family-contribution-grant-{record.grant_id}",
                command_id=record.command_id_hash,
            ):
                self._require_active_vault(context=context)
                relationship = self._require_accepted_relationship(
                    owner_subject_id=context.owner_subject_id,
                    relationship_id=record.relationship_id,
                    contributor_subject_id=record.contributor_subject_id,
                )
                now_iso = _now_iso(self._now_provider)
                grant = {
                    "id": record.grant_id,
                    "vaultId": record.vault_id,
                    "ownerSubjectId": record.owner_subject_id,
                    "contributorSubjectId": record.contributor_subject_id,
                    "relationshipId": record.relationship_id,
                    "relationshipEpoch": int(relationship.get("relationshipEpoch") or 0),
                    "scope": FAMILY_CONTRIBUTION_SCOPE,
                    "status": "active",
                    "rowVersion": 1,
                    "createCommandIdHash": record.command_id_hash,
                    "createPayloadHash": record.payload_hash,
                    "createdAt": now_iso,
                    "updatedAt": now_iso,
                }
                try:
                    persisted = self._store.create_owner_truth_family_contribution_grant(grant)
                except ValueError as exc:
                    raise OwnerTruthFamilyContributionError(
                        "familyContributionGrantCommandConflict"
                    ) from exc
        outcome = "deduplicated" if bool(persisted.get("deduplicated")) else "created"
        return FamilyContributionGrantResult(outcome=outcome, grant=persisted)

    def revoke_grant(
        self,
        *,
        command: RevokeFamilyContributionGrantCommand,
        context: OwnerTruthCommandContext,
    ) -> FamilyContributionGrantResult:
        self._require_owner_context(context)
        existing = self._require_grant(vault_id=context.vault_id, grant_id=command.grant_id)
        self._assert_grant_owner(existing, context.owner_subject_id)
        with self._relationship_scope(
            owner_subject_id=context.owner_subject_id,
            relationship_id=str(existing.get("relationshipId") or ""),
        ):
            with self._store.request_unit_of_work(
                correlation_id=f"owner-truth-family-contribution-revoke-{command.grant_id}",
                command_id=_sha256(command.command_id),
            ):
                persisted = self._require_grant(vault_id=context.vault_id, grant_id=command.grant_id)
                self._assert_grant_owner(persisted, context.owner_subject_id)
                command_hash, payload_hash = command.revoke_hashes()
                revoked = self._store.revoke_owner_truth_family_contribution_grant(
                    vault_id=context.vault_id,
                    owner_subject_id=context.owner_subject_id,
                    grant_id=command.grant_id,
                    expected_version=command.expected_version,
                    revoke_command_id_hash=command_hash,
                    revoke_payload_hash=payload_hash,
                    revoked_at_iso=_now_iso(self._now_provider),
                    reason=command.reason,
                )
                if revoked is None:
                    raise OwnerTruthFamilyContributionError("familyContributionGrantVersionMismatch")
        outcome = "deduplicated" if bool(revoked.get("deduplicated")) else "revoked"
        return FamilyContributionGrantResult(outcome=outcome, grant=revoked)

    def submit_text_source(
        self,
        *,
        command: SubmitFamilyContributionTextCommand,
        context: OwnerTruthCommandContext,
    ) -> FamilyContributionSubmissionResult:
        existing = self._require_grant(vault_id=context.vault_id, grant_id=command.grant_id)
        relationship_id = require_nonblank(
            str(existing.get("relationshipId") or ""),
            field="relationship_id",
        )
        owner_subject_id = require_nonblank(
            str(existing.get("ownerSubjectId") or ""),
            field="owner_subject_id",
        )
        with self._relationship_scope(
            owner_subject_id=owner_subject_id,
            relationship_id=relationship_id,
        ):
            with self._store.request_unit_of_work(
                correlation_id=f"owner-truth-family-contribution-source-{command.source_id}",
                command_id=_sha256(command.source_command_id),
            ):
                grant = self._require_grant(vault_id=context.vault_id, grant_id=command.grant_id)
                self._assert_submission_allowed(
                    grant=grant,
                    context=context,
                    expected_grant_version=command.expected_grant_version,
                )
                source_command = CreateTextSourceCommand(
                    command_id=command.source_command_id,
                    source_id=command.source_id,
                    expected_version=0,
                    text=command.text,
                    metadata={
                        "origin": "familyContributionGrant",
                        "perspectiveType": "familyReport",
                        "epistemicStatus": "reported",
                        "familyContributionGrantId": str(grant["id"]),
                        "familyContributionGrantVersion": int(grant["rowVersion"]),
                        "relationshipId": str(grant["relationshipId"]),
                        "relationshipEpoch": int(grant["relationshipEpoch"]),
                        "candidateExtraction": "defaultOff",
                    },
                    source_kind=SourceKind.TEXT,
                )
                source_context = OwnerTruthCommandContext(
                    vault_id=context.vault_id,
                    owner_subject_id=str(grant["ownerSubjectId"]),
                    actor_subject_id=context.actor_subject_id,
                )
                source_record = source_command.write_record(context=source_context)
                source = self._store.create_owner_truth_source(source_record)
        return FamilyContributionSubmissionResult(grant=grant, source=source)

    def contributor_context(
        self,
        *,
        vault_id: str,
        grant_id: str,
        actor_subject_id: str,
    ) -> OwnerTruthCommandContext:
        """Resolve the Owner server-side before a contributor submits material.

        This resolves only the private command context. It is not an
        authorization decision; ``submit_text_source`` rechecks the grant,
        relationship and epoch while holding the relationship scope.
        """

        grant = self._require_grant(vault_id=vault_id, grant_id=grant_id)
        return OwnerTruthCommandContext(
            vault_id=vault_id,
            owner_subject_id=require_nonblank(
                str(grant.get("ownerSubjectId") or ""),
                field="owner_subject_id",
            ),
            actor_subject_id=actor_subject_id,
        )

    def _require_active_vault(self, *, context: OwnerTruthCommandContext) -> Mapping[str, Any]:
        vault = self._store.get_owner_truth_vault(context.vault_id)
        if vault is None:
            raise OwnerTruthFamilyContributionError("familyContributionVaultNotFound")
        if str(vault.get("ownerSubjectId") or "") != context.owner_subject_id:
            raise OwnerTruthFamilyContributionError("familyContributionVaultOwnerMismatch")
        if str(vault.get("status") or "active") != "active":
            raise OwnerTruthFamilyContributionError("familyContributionVaultInactive")
        return vault

    def _require_accepted_relationship(
        self,
        *,
        owner_subject_id: str,
        relationship_id: str,
        contributor_subject_id: str,
    ) -> Mapping[str, Any]:
        relationship = self._store.get_family_relationship(owner_subject_id, relationship_id)
        if relationship is None:
            raise OwnerTruthFamilyContributionError("familyContributionRelationshipNotFound")
        if str(relationship.get("status") or "") != "accepted":
            raise OwnerTruthFamilyContributionError("familyContributionRelationshipInactive")
        if str(relationship.get("ownerSubjectId") or "") != owner_subject_id:
            raise OwnerTruthFamilyContributionError("familyContributionRelationshipOwnerMismatch")
        if str(relationship.get("memberSubjectId") or "") != contributor_subject_id:
            raise OwnerTruthFamilyContributionError("familyContributionRelationshipSubjectMismatch")
        return relationship

    def _assert_submission_allowed(
        self,
        *,
        grant: Mapping[str, Any],
        context: OwnerTruthCommandContext,
        expected_grant_version: int,
    ) -> None:
        if str(grant.get("status") or "") != "active":
            raise OwnerTruthFamilyContributionError("familyContributionGrantInactive")
        if int(grant.get("rowVersion") or 0) != expected_grant_version:
            raise OwnerTruthFamilyContributionError("familyContributionGrantVersionMismatch")
        if str(grant.get("scope") or "") != FAMILY_CONTRIBUTION_SCOPE:
            raise OwnerTruthFamilyContributionError("familyContributionGrantScopeInvalid")
        if context.actor_subject_id != str(grant.get("contributorSubjectId") or ""):
            raise OwnerTruthFamilyContributionError("familyContributionGrantContributorMismatch")
        if context.owner_subject_id != str(grant.get("ownerSubjectId") or ""):
            raise OwnerTruthFamilyContributionError("familyContributionVaultOwnerMismatch")
        self._require_active_vault(context=context)
        relationship = self._require_accepted_relationship(
            owner_subject_id=str(grant["ownerSubjectId"]),
            relationship_id=str(grant["relationshipId"]),
            contributor_subject_id=str(grant["contributorSubjectId"]),
        )
        if int(relationship.get("relationshipEpoch") or 0) != int(grant.get("relationshipEpoch") or 0):
            raise OwnerTruthFamilyContributionError("familyContributionRelationshipEpochMismatch")

    def _require_grant(self, *, vault_id: str, grant_id: str) -> Mapping[str, Any]:
        grant = self._store.get_owner_truth_family_contribution_grant(vault_id, grant_id)
        if grant is None:
            raise OwnerTruthFamilyContributionError("familyContributionGrantNotFound")
        return grant

    @staticmethod
    def _require_owner_context(context: OwnerTruthCommandContext) -> None:
        if not isinstance(context, OwnerTruthCommandContext):
            raise OwnerTruthFamilyContributionError("familyContributionOwnerContextRequired")
        if context.actor_subject_id != context.owner_subject_id:
            raise OwnerTruthFamilyContributionError("familyContributionGrantOwnerMismatch")

    @staticmethod
    def _assert_grant_owner(grant: Mapping[str, Any], owner_subject_id: str) -> None:
        if str(grant.get("ownerSubjectId") or "") != owner_subject_id:
            raise OwnerTruthFamilyContributionError("familyContributionGrantOwnerMismatch")

    def _relationship_scope(self, *, owner_subject_id: str, relationship_id: str) -> ContextManager[Any]:
        scope = getattr(self._store, "delegated_access_relationship_scope", None)
        if not callable(scope):
            return nullcontext()
        return scope(
            owner_subject_id=owner_subject_id,
            relationship_id=relationship_id,
        )


def _public_grant(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "grantId": str(value.get("id") or ""),
        "vaultId": str(value.get("vaultId") or ""),
        "ownerSubjectId": str(value.get("ownerSubjectId") or ""),
        "contributorSubjectId": str(value.get("contributorSubjectId") or ""),
        "relationshipId": str(value.get("relationshipId") or ""),
        "relationshipEpoch": int(value.get("relationshipEpoch") or 0),
        "scope": str(value.get("scope") or ""),
        "status": str(value.get("status") or ""),
        "rowVersion": int(value.get("rowVersion") or 0),
        "createdAt": value.get("createdAt"),
        "updatedAt": value.get("updatedAt"),
        "revokedAt": value.get("revokedAt"),
        "revocationReason": value.get("revocationReason"),
    }


__all__ = [
    "CreateFamilyContributionGrantCommand",
    "FAMILY_CONTRIBUTION_SCOPE",
    "FamilyContributionGrantResult",
    "FamilyContributionSubmissionResult",
    "OWNER_TRUTH_FAMILY_CONTRIBUTION_SCHEMA_VERSION",
    "OwnerTruthFamilyContributionError",
    "OwnerTruthFamilyContributionService",
    "RevokeFamilyContributionGrantCommand",
    "SubmitFamilyContributionTextCommand",
]
