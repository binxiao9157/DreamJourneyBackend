"""Explicit, directional, read-only family access to confirmed memory.

No relationship alone authorizes a read. This module never writes Owner Truth.
"""
from typing import Any, Mapping
from app.services.delegated_access import DelegatedAccessService, AccessGrantPurpose, GrantOperation, ResourceScopeType


def authorize_family_echo(store: Any, *, viewer: str, owner: str, member: str, expected_grant: str = "", record_receipt: bool = True) -> dict:
    if not viewer or not owner or not member or viewer == owner:
        raise ValueError("echoFamilyQueryNotAuthorized")
    decision = DelegatedAccessService(store).authorize(
        owner_subject_id=owner, grantee_subject_id=viewer, family_member_id=member,
        purpose=AccessGrantPurpose.FAMILY_PERSONA, operation=GrantOperation.READ,
        resource_type=ResourceScopeType.FAMILY_MEMBER, resource_id=member,
        record_receipt=record_receipt)
    if not decision.allowed or (expected_grant and decision.grant_id != expected_grant):
        raise ValueError("echoFamilyQueryNotAuthorized")
    return {"viewerSubjectId": viewer, "ownerSubjectId": owner, "familyMemberId": member,
            "grantId": decision.grant_id, "scope": "confirmedFormalMemoryReadOnly"}


def binding_is_current(store: Any, binding: Mapping[str, Any]) -> bool:
    if not binding.get("grantId"):
        return False
    try:
        authorize_family_echo(store, viewer=str(binding.get("viewerSubjectId") or ""),
            owner=str(binding.get("ownerSubjectId") or ""), member=str(binding.get("familyMemberId") or ""),
            expected_grant=str(binding["grantId"]), record_receipt=False)
        return True
    except (ValueError, TypeError):
        return False


def received_echo_members(store: Any, viewer: str) -> list[dict]:
    service = DelegatedAccessService(store)
    result = []
    for relationship in store.list_family_relationships_for_participant(viewer):
        owner = str(relationship.get("ownerSubjectId") or "")
        member = str(relationship.get("familyMemberId") or "")
        if relationship.get("memberSubjectId") != viewer or relationship.get("status") != "accepted":
            continue
        try:
            binding = authorize_family_echo(store, viewer=viewer, owner=owner, member=member, record_receipt=False)
        except ValueError:
            continue
        profile = store.get_profile(owner) or {}
        grants = service.list_relationship_grants(owner_subject_id=owner, relationship_id=relationship["id"])
        result.append({"id": member, "userId": viewer, "viewerUserId": viewer, "ownerUserId": owner,
            "name": str(profile.get("nickname") or profile.get("name") or "家人"),
            "relation": "对方授权的回响", "personaScope": "family", "digitalHumanId": owner,
            "digitalHumanMode": "sunlight", "accessStatus": "active", "invitationStatus": "accepted",
            "relationshipId": relationship["id"], "relationshipStatus": "accepted",
            "relationshipEpoch": relationship["relationshipEpoch"], "grantEpoch": relationship["grantEpoch"],
            "memberSubjectId": viewer, "accessGrants": [g for g in grants if g["id"] == binding["grantId"]],
            "lastUpdated": "仅可读取已确认的正式记忆"})
    return result
