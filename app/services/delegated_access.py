from __future__ import annotations

import hashlib
import uuid
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.user_identity import stable_user_id


class AccessGrantPurpose(str, Enum):
    FAMILY_PERSONA = "family.persona"
    CARE_SNAPSHOT = "care.snapshot"
    TIME_LETTER_READ = "timeLetter.read"


class ResourceScopeType(str, Enum):
    FAMILY_MEMBER = "familyMember"
    CARE_SNAPSHOT = "careSnapshot"
    TIME_LETTER = "timeLetter"


class GrantOperation(str, Enum):
    READ = "read"


class RelationshipOperation(str, Enum):
    PAUSE = "pause"
    RESUME = "resume"
    REVOKE = "revoke"


PURPOSE_RESOURCE_TYPES = {
    AccessGrantPurpose.FAMILY_PERSONA: ResourceScopeType.FAMILY_MEMBER,
    AccessGrantPurpose.CARE_SNAPSHOT: ResourceScopeType.CARE_SNAPSHOT,
    AccessGrantPurpose.TIME_LETTER_READ: ResourceScopeType.TIME_LETTER,
}


class AccessGrantCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grantorSubjectId: str = Field(min_length=1, max_length=128)
    relationshipId: str = Field(min_length=1, max_length=128)
    granteeSubjectId: str = Field(min_length=1, max_length=128)
    purpose: AccessGrantPurpose
    resourceType: ResourceScopeType
    resourceId: Optional[str] = Field(default=None, max_length=160)
    operations: List[GrantOperation] = Field(min_length=1, max_length=4)
    expiresAt: Optional[datetime] = None

    @model_validator(mode="after")
    def validate_scope(self) -> "AccessGrantCommand":
        if PURPOSE_RESOURCE_TYPES[self.purpose] != self.resourceType:
            raise ValueError("grant purpose and resource type do not match")
        normalized_operations = list(dict.fromkeys(self.operations))
        if normalized_operations != self.operations:
            raise ValueError("grant operations must be unique")
        resource_id = (self.resourceId or "").strip()
        if self.purpose in {
            AccessGrantPurpose.FAMILY_PERSONA,
            AccessGrantPurpose.TIME_LETTER_READ,
        } and not resource_id:
            raise ValueError(f"{self.purpose.value} grant requires resourceId")
        self.resourceId = resource_id or None
        return self


class RevokeAccessGrantCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grantorSubjectId: str = Field(min_length=1, max_length=128)
    grantId: str = Field(min_length=1, max_length=128)
    expectedVersion: int = Field(ge=1)
    reason: str = Field(default="ownerRequested", min_length=1, max_length=80)


class RelationshipLifecycleCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ownerSubjectId: str = Field(min_length=1, max_length=128)
    relationshipId: str = Field(min_length=1, max_length=128)
    operation: RelationshipOperation
    expectedEpoch: int = Field(ge=1)


@dataclass(frozen=True)
class DelegatedAccessDecision:
    allowed: bool
    reason: str
    relationship_id: Optional[str] = None
    grant_id: Optional[str] = None
    receipt_id: Optional[str] = None


class DelegatedAccessError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class DelegatedAccessService:
    """Canonical relationship and purpose-bound delegated access authority."""

    def __init__(
        self,
        store: Any,
        *,
        now_provider: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.store = store
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def ensure_relationship(
        self,
        *,
        owner_subject_id: str,
        family_member_id: str,
        member_subject_id: str,
        status: str,
    ) -> Dict[str, Any]:
        owner = self._required(owner_subject_id, "ownerSubjectId")
        member_id = self._required(family_member_id, "familyMemberId")
        member_subject = self._required(member_subject_id, "memberSubjectId")
        normalized_status = str(status or "pending").strip()
        if normalized_status not in {"pending", "accepted", "paused", "revoked"}:
            raise DelegatedAccessError("relationshipStatusInvalid")
        relationship_id = self.relationship_id(owner, member_id)
        return self.store.upsert_family_relationship(
            {
                "id": relationship_id,
                "vaultId": owner,
                "ownerSubjectId": owner,
                "familyMemberId": member_id,
                "memberSubjectId": member_subject,
                "status": normalized_status,
            }
        )

    def ensure_relationship_for_member(
        self,
        *,
        owner_subject_id: str,
        member: Dict[str, Any],
        accepted_subject_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        member_id = self._required(member.get("id"), "familyMemberId")
        access_status = str(member.get("accessStatus") or "pending").strip()
        invitation_status = str(member.get("invitationStatus") or "pending").strip()
        if "revoked" in {access_status, invitation_status}:
            relationship_status = "revoked"
        elif access_status == "paused":
            relationship_status = "paused"
        elif access_status == "active" and invitation_status == "accepted":
            relationship_status = "accepted"
        else:
            relationship_status = "pending"
        member_subject_id = self._member_subject_id(member, accepted_subject_id)
        return self.ensure_relationship(
            owner_subject_id=owner_subject_id,
            family_member_id=member_id,
            member_subject_id=member_subject_id,
            status=relationship_status,
        )

    def decorate_family_member(
        self,
        *,
        owner_subject_id: str,
        member: Dict[str, Any],
    ) -> Dict[str, Any]:
        relationship = self.ensure_relationship_for_member(
            owner_subject_id=owner_subject_id,
            member=member,
        )
        grants = self.list_relationship_grants(
            owner_subject_id=owner_subject_id,
            relationship_id=str(relationship["id"]),
        )
        decorated = dict(member)
        decorated.update(
            {
                "relationshipId": relationship["id"],
                "relationshipStatus": relationship["status"],
                "relationshipEpoch": int(relationship.get("relationshipEpoch") or 1),
                "memberSubjectId": relationship["memberSubjectId"],
                "accessGrants": grants,
                "grantEpoch": int(relationship.get("grantEpoch") or 0),
            }
        )
        return decorated

    def list_decorated_family_members(self, owner_subject_id: str) -> List[Dict[str, Any]]:
        owner = self._required(owner_subject_id, "ownerSubjectId")
        return [
            self.decorate_family_member(owner_subject_id=owner, member=member)
            for member in self.store.list_family_members(owner)
        ]

    def grant_access(self, command: AccessGrantCommand) -> Dict[str, Any]:
        with self._relationship_scope(
            owner_subject_id=command.grantorSubjectId,
            relationship_id=command.relationshipId,
        ):
            return self._grant_access_locked(command)

    def _grant_access_locked(self, command: AccessGrantCommand) -> Dict[str, Any]:
        now = self._now()
        relationship = self.store.get_family_relationship(
            command.grantorSubjectId,
            command.relationshipId,
        )
        if relationship is None:
            raise DelegatedAccessError("relationshipNotFound")
        if relationship.get("status") != "accepted":
            raise DelegatedAccessError("relationshipInactive")
        if relationship.get("ownerSubjectId") != command.grantorSubjectId:
            raise DelegatedAccessError("relationshipOwnerMismatch")
        if relationship.get("memberSubjectId") != command.granteeSubjectId:
            raise DelegatedAccessError("relationshipSubjectMismatch")
        if command.expiresAt is not None and self._utc(command.expiresAt) <= now:
            raise DelegatedAccessError("grantExpiryMustBeFuture")
        for stored in self.store.list_access_grants(
            owner_subject_id=command.grantorSubjectId,
            relationship_id=command.relationshipId,
        ):
            if not self._grant_scope_matches(
                stored,
                grantee_subject_id=command.granteeSubjectId,
                purpose=command.purpose,
                resource_type=command.resourceType,
                resource_id=command.resourceId,
            ):
                continue
            existing = self._project_grant(stored)
            if existing.get("status") == "expired":
                expired = self.store.revoke_access_grant(
                    command.grantorSubjectId,
                    str(existing.get("id") or ""),
                    expected_version=int(existing.get("rowVersion") or 1),
                    revoked_at_iso=now.isoformat(),
                    reason="expiredBeforeRegrant",
                )
                if expired is None:
                    raise DelegatedAccessError("grantVersionMismatch")
                continue
            if existing.get("status") != "active":
                continue
            same_operations = set(existing.get("operations") or []) == {
                item.value for item in command.operations
            }
            same_expiry = self._parse_datetime(existing.get("expiresAt")) == (
                self._utc(command.expiresAt) if command.expiresAt is not None else None
            )
            if same_operations and same_expiry:
                return existing
            raise DelegatedAccessError("grantScopeAlreadyActive")
        grant = {
            "id": "grant_" + uuid.uuid4().hex,
            "vaultId": command.grantorSubjectId,
            "grantorSubjectId": command.grantorSubjectId,
            "granteeSubjectId": command.granteeSubjectId,
            "relationshipId": command.relationshipId,
            "purpose": command.purpose.value,
            "resourceType": command.resourceType.value,
            "resourceId": command.resourceId,
            "operations": [item.value for item in command.operations],
            "status": "active",
            "expiresAt": self._iso(command.expiresAt),
            "revokedAt": None,
            "rowVersion": 1,
            "createdAt": now.isoformat(),
            "updatedAt": now.isoformat(),
        }
        return self.store.create_access_grant(grant)

    def revoke_access(self, command: RevokeAccessGrantCommand) -> Dict[str, Any]:
        grant = self.store.get_access_grant(command.grantId)
        if grant is None:
            raise DelegatedAccessError("grantNotFound")
        if grant.get("grantorSubjectId") != command.grantorSubjectId:
            raise DelegatedAccessError("grantOwnerMismatch")
        if int(grant.get("rowVersion") or 1) != command.expectedVersion:
            raise DelegatedAccessError("grantVersionMismatch")
        if grant.get("status") == "revoked":
            return self._project_grant(grant)
        revoked = self.store.revoke_access_grant(
            command.grantorSubjectId,
            command.grantId,
            expected_version=command.expectedVersion,
            revoked_at_iso=self._now().isoformat(),
            reason=command.reason,
        )
        if revoked is None:
            raise DelegatedAccessError("grantVersionMismatch")
        return self._project_grant(revoked)

    def revoke_subject_access(self, subject_id: str, *, reason: str) -> Dict[str, Any]:
        subject = self._required(subject_id, "subjectId")
        revoked_at = self._now().isoformat()
        revoked_count = self.store.revoke_all_access_grants_for_subject(
            subject,
            revoked_at_iso=revoked_at,
            reason=self._required(reason, "reason"),
        )
        return {
            "subjectId": subject,
            "revokedGrantCount": int(revoked_count),
            "revokedAt": revoked_at,
            "reason": reason,
        }

    def change_relationship(
        self,
        command: RelationshipLifecycleCommand,
    ) -> Dict[str, Any]:
        with self._relationship_scope(
            owner_subject_id=command.ownerSubjectId,
            relationship_id=command.relationshipId,
        ):
            return self._change_relationship_locked(command)

    def _change_relationship_locked(
        self,
        command: RelationshipLifecycleCommand,
    ) -> Dict[str, Any]:
        relationship = self.store.get_family_relationship(
            command.ownerSubjectId,
            command.relationshipId,
        )
        if relationship is None:
            raise DelegatedAccessError("relationshipNotFound")
        if int(relationship.get("relationshipEpoch") or 1) != command.expectedEpoch:
            raise DelegatedAccessError("relationshipEpochMismatch")
        current = str(relationship.get("status") or "pending")
        target = self._relationship_target(current, command.operation)
        updated = self.store.update_family_relationship_status(
            command.ownerSubjectId,
            command.relationshipId,
            status=target,
            expected_epoch=command.expectedEpoch,
        )
        if updated is None:
            raise DelegatedAccessError("relationshipEpochMismatch")
        if target == "revoked":
            revoke_for_relationship = getattr(
                self.store,
                "revoke_all_access_grants_for_relationship",
                None,
            )
            if callable(revoke_for_relationship):
                revoke_for_relationship(
                    command.ownerSubjectId,
                    command.relationshipId,
                    revoked_at_iso=self._now().isoformat(),
                    reason="relationshipRevoked",
                )
                refreshed = self.store.get_family_relationship(
                    command.ownerSubjectId,
                    command.relationshipId,
                )
                if refreshed is not None:
                    updated = refreshed
        return updated

    def authorize(
        self,
        *,
        owner_subject_id: str,
        grantee_subject_id: str,
        family_member_id: str,
        purpose: AccessGrantPurpose,
        operation: GrantOperation,
        resource_type: ResourceScopeType,
        resource_id: Optional[str] = None,
        record_receipt: bool = True,
    ) -> DelegatedAccessDecision:
        relationship = self.store.get_family_relationship_by_member(
            owner_subject_id,
            family_member_id,
        )
        if relationship is None:
            return DelegatedAccessDecision(False, "relationshipMissing")
        relationship_id = str(relationship.get("id") or "")
        if relationship.get("status") != "accepted":
            return DelegatedAccessDecision(
                False,
                "relationshipInactive",
                relationship_id=relationship_id,
            )
        if relationship.get("memberSubjectId") != grantee_subject_id:
            return DelegatedAccessDecision(
                False,
                "relationshipSubjectMismatch",
                relationship_id=relationship_id,
            )
        for grant in self.list_relationship_grants(
            owner_subject_id=owner_subject_id,
            relationship_id=relationship_id,
        ):
            if self._grant_matches(
                grant,
                grantee_subject_id=grantee_subject_id,
                purpose=purpose,
                operation=operation,
                resource_type=resource_type,
                resource_id=resource_id,
            ):
                receipt_recorder = getattr(
                    self.store,
                    "record_access_grant_receipt",
                    None,
                )
                receipt = (
                    receipt_recorder(
                        grant,
                        actor_subject_id=grantee_subject_id,
                        operation=operation.value,
                    )
                    if record_receipt and callable(receipt_recorder)
                    else None
                )
                return DelegatedAccessDecision(
                    True,
                    "activeGrant",
                    relationship_id=relationship_id,
                    grant_id=str(grant.get("id") or ""),
                    receipt_id=(
                        str(receipt.get("id") or "")
                        if isinstance(receipt, dict)
                        else None
                    ),
                )
        return DelegatedAccessDecision(
            False,
            "activeGrantRequired",
            relationship_id=relationship_id,
        )

    def list_relationship_grants(
        self,
        *,
        owner_subject_id: str,
        relationship_id: str,
    ) -> List[Dict[str, Any]]:
        return [
            self._project_grant(grant)
            for grant in self.store.list_access_grants(
                owner_subject_id=owner_subject_id,
                relationship_id=relationship_id,
            )
        ]

    @staticmethod
    def relationship_id(owner_subject_id: str, family_member_id: str) -> str:
        digest = hashlib.sha256(
            f"{owner_subject_id}:{family_member_id}".encode("utf-8")
        ).hexdigest()[:24]
        return f"relationship_{digest}"

    def _project_grant(self, grant: Dict[str, Any]) -> Dict[str, Any]:
        projected = dict(grant)
        if projected.get("status") == "active":
            expires_at = self._parse_datetime(projected.get("expiresAt"))
            if expires_at is not None and expires_at <= self._now():
                projected["status"] = "expired"
        return projected

    def _relationship_scope(
        self,
        *,
        owner_subject_id: str,
        relationship_id: str,
    ):
        scope = getattr(self.store, "delegated_access_relationship_scope", None)
        if not callable(scope):
            return nullcontext()
        return scope(
            owner_subject_id=owner_subject_id,
            relationship_id=relationship_id,
        )

    def _grant_matches(
        self,
        grant: Dict[str, Any],
        *,
        grantee_subject_id: str,
        purpose: AccessGrantPurpose,
        operation: GrantOperation,
        resource_type: ResourceScopeType,
        resource_id: Optional[str],
    ) -> bool:
        if grant.get("status") != "active":
            return False
        if not self._grant_scope_matches(
            grant,
            grantee_subject_id=grantee_subject_id,
            purpose=purpose,
            resource_type=resource_type,
            resource_id=resource_id,
        ):
            return False
        if operation.value not in set(grant.get("operations") or []):
            return False
        return True

    @staticmethod
    def _grant_scope_matches(
        grant: Dict[str, Any],
        *,
        grantee_subject_id: str,
        purpose: AccessGrantPurpose,
        resource_type: ResourceScopeType,
        resource_id: Optional[str],
    ) -> bool:
        if grant.get("granteeSubjectId") != grantee_subject_id:
            return False
        if grant.get("purpose") != purpose.value:
            return False
        if grant.get("resourceType") != resource_type.value:
            return False
        grant_resource_id = str(grant.get("resourceId") or "").strip()
        requested_resource_id = str(resource_id or "").strip()
        return grant_resource_id == requested_resource_id

    @staticmethod
    def _relationship_target(current: str, operation: RelationshipOperation) -> str:
        transitions = {
            ("accepted", RelationshipOperation.PAUSE): "paused",
            ("paused", RelationshipOperation.RESUME): "accepted",
            ("accepted", RelationshipOperation.REVOKE): "revoked",
            ("paused", RelationshipOperation.REVOKE): "revoked",
        }
        target = transitions.get((current, operation))
        if target is None:
            raise DelegatedAccessError("relationshipTransitionInvalid")
        return target

    @staticmethod
    def _member_subject_id(member: Dict[str, Any], accepted_subject_id: Optional[str]) -> str:
        candidates = (
            accepted_subject_id,
            member.get("memberUserId"),
            member.get("acceptedUserId"),
            member.get("recipientUserId"),
        )
        for candidate in candidates:
            normalized = str(candidate or "").strip()
            if normalized:
                return normalized
        phone = str(member.get("phone") or "").strip()
        if phone:
            return stable_user_id(phone)
        return f"unverified:{str(member.get('id') or '').strip()}"

    def _now(self) -> datetime:
        return self._utc(self._now_provider())

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @classmethod
    def _iso(cls, value: Optional[datetime]) -> Optional[str]:
        return None if value is None else cls._utc(value).isoformat()

    @classmethod
    def _parse_datetime(cls, value: Any) -> Optional[datetime]:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            return cls._utc(datetime.fromisoformat(raw.replace("Z", "+00:00")))
        except ValueError:
            return None

    @staticmethod
    def _required(value: Any, field: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise DelegatedAccessError(f"{field}Required")
        return normalized
