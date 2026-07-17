from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class ResourceType(str, Enum):
    ARCHIVE_ITEM = "archiveItem"
    DIGITAL_HUMAN_SESSION = "digitalHumanSession"
    FAMILY_MEMBER = "familyMember"
    MAILBOX_LETTER = "mailboxLetter"
    VOICE_PROFILE = "voiceProfile"


class ResourceOperation(str, Enum):
    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    DELETE = "delete"
    EXECUTE = "execute"


@dataclass(frozen=True)
class ResolvedResourceAuthority:
    resource_type: ResourceType
    resource_id: str
    vault_id: str
    owner_subject_id: str
    row_version: int
    authority_state: str


class ResourceAuthorityResolver:
    """Resolve canonical resource authority without trusting request payload metadata."""

    def __init__(self, store: Any):
        self.store = store

    def resolve(
        self,
        resource_type: ResourceType,
        resource_id: str,
    ) -> Optional[ResolvedResourceAuthority]:
        resolver = getattr(self.store, "resolve_resource_authority", None)
        if not callable(resolver):
            return None
        record = resolver(resource_type.value, resource_id)
        if record is None:
            return None
        return ResolvedResourceAuthority(
            resource_type=resource_type,
            resource_id=resource_id,
            vault_id=str(record.get("vaultId") or "").strip(),
            owner_subject_id=str(record.get("ownerSubjectId") or "").strip(),
            row_version=max(1, int(record.get("rowVersion") or 1)),
            authority_state=str(record.get("authorityState") or "active").strip(),
        )
