from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Set

from app.services.route_ownership import RouteOwnershipCategory, RouteOwnershipRegistry
from app.services.resource_authorization import ResourceAuthorityResolver, ResourceType
from app.services.delegated_access import (
    AccessGrantPurpose,
    DelegatedAccessService,
    GrantOperation,
    ResourceScopeType,
)
from app.services.time_letters import is_time_letter_open, time_letter_recipient_records
from app.services.user_identity import stable_user_id


OWNER_AUTHORITY_CLAIM_KEYS = frozenset(
    {
        "authenticatedUserId",
        "ownerId",
        "ownerUserId",
        "requesterUserId",
        "uploadedByUserId",
        "uploaderUserId",
        "userId",
        "viewerUserId",
    }
)


def owner_authority_claims(value: Any) -> Set[str]:
    """Collect actor/owner claims recursively without treating recipients as owners."""
    claims: Set[str] = set()

    def visit(candidate: Any) -> None:
        if isinstance(candidate, Mapping):
            for key, nested in candidate.items():
                if str(key) in OWNER_AUTHORITY_CLAIM_KEYS:
                    normalized = str(nested or "").strip()
                    if normalized:
                        claims.add(normalized)
                if isinstance(nested, (Mapping, list, tuple)):
                    visit(nested)
        elif isinstance(candidate, (list, tuple)):
            for nested in candidate:
                visit(nested)

    visit(value)
    return claims


@dataclass(frozen=True)
class AuthorizationPolicyDecision:
    policy_id: str
    decision: str
    reason: str
    allowed: Optional[bool]
    delegated: bool = False
    terminal: bool = True
    principal_bound: bool = False
    grant_id: Optional[str] = None
    grant_receipt_id: Optional[str] = None

    def header_values(self) -> Dict[str, str]:
        values = {
            "policy": self.policy_id,
            "decision": self.decision,
            "reason": self.reason,
        }
        if self.grant_id:
            values["grantId"] = self.grant_id
        if self.grant_receipt_id:
            values["grantReceiptId"] = self.grant_receipt_id
        return values


class CrossAccountAuthorizationPolicy:
    """Bind user principals to route owners while preserving delegated policies."""

    _registry = RouteOwnershipRegistry()

    def __init__(self, store: Any):
        self.store = store
        self.registry = self._registry

    def evaluate(
        self,
        *,
        method: str,
        path: str,
        principal_user_id: str,
        query: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> AuthorizationPolicyDecision:
        normalized_method = str(method or "").upper()
        normalized_path = str(path or "")
        principal_user_id = str(principal_user_id or "").strip()

        route_match = self.registry.match(normalized_method, normalized_path)
        if route_match is None:
            return AuthorizationPolicyDecision(
                policy_id="ownershipFallback",
                decision="fallback",
                reason="routeNotClassified",
                allowed=None,
                terminal=False,
            )

        rule = route_match.rule
        if rule.policy_id == "careViewer" and normalized_method == "GET":
            owner_user_id = str(route_match.path_parameters.get("user_id") or "").strip()
            return self._evaluate_care_read(
                owner_user_id=owner_user_id,
                principal_user_id=principal_user_id,
                query=query,
            )

        if rule.policy_id == "careViewer" and normalized_method == "POST":
            return self._evaluate_owner_write(
                policy_id="careSnapshotWrite",
                owner_user_id=str(payload.get("userId") or "").strip(),
                principal_user_id=principal_user_id,
                payload=payload,
            )

        if rule.policy_id == "timeLetterViewer":
            return self._evaluate_time_letter_detail(
                owner_user_id=str(route_match.path_parameters.get("owner_user_id") or "").strip(),
                item_id=str(route_match.path_parameters.get("item_id") or "").strip(),
                principal_user_id=principal_user_id,
                query=query,
            )

        if normalized_path.startswith("/family/invitations/"):
            return self._evaluate_invitation_accept(
                principal_user_id=principal_user_id,
                payload=payload,
            )

        if rule.policy_id == "familyInvitationAcceptance":
            return self._evaluate_family_member_accept(
                owner_user_id=str(route_match.path_parameters.get("user_id") or "").strip(),
                member_id=str(route_match.path_parameters.get("member_id") or "").strip(),
                principal_user_id=principal_user_id,
            )

        if rule.category in {RouteOwnershipCategory.OWNER_BODY, RouteOwnershipCategory.OWNER_PATH}:
            owner_decision = self._evaluate_owner_write(
                policy_id=rule.policy_id,
                owner_user_id=route_match.owner_user_id(dict(payload)),
                principal_user_id=principal_user_id,
                payload=payload,
            )
            if owner_decision.allowed is not True:
                return owner_decision
            if rule.requires_existing_resource:
                return self._evaluate_resource(
                    policy_id=rule.policy_id,
                    resource_type=rule.resource_type,
                    resource_id=route_match.resource_id(dict(payload)),
                    principal_user_id=principal_user_id,
                    expected_version=payload.get("expectedVersion", query.get("expectedVersion")),
                )
            return owner_decision
        if rule.category == RouteOwnershipCategory.SYSTEM_ONLY:
            return self._deny(rule.policy_id, "systemPrincipalRequired")
        if rule.category == RouteOwnershipCategory.DELEGATED:
            return self._deny(rule.policy_id, "delegatedPolicyUnavailable")
        if rule.category == RouteOwnershipCategory.USER_SESSION:
            return AuthorizationPolicyDecision(
                policy_id=rule.policy_id,
                decision="allowSession",
                reason="authenticatedSession",
                allowed=True,
                principal_bound=True,
            )
        if rule.category == RouteOwnershipCategory.AUTHENTICATED_SERVICE:
            return AuthorizationPolicyDecision(
                policy_id=rule.policy_id,
                decision="allowAuthenticated",
                reason="authenticatedPrincipal",
                allowed=True,
            )
        return AuthorizationPolicyDecision(
            policy_id=rule.policy_id,
            decision="allowPublic",
            reason="publicRoute",
            allowed=True,
        )

    def _evaluate_owner_write(
        self,
        *,
        policy_id: str,
        owner_user_id: Optional[str],
        principal_user_id: str,
        payload: Mapping[str, Any],
    ) -> AuthorizationPolicyDecision:
        if owner_user_id and owner_user_id != principal_user_id:
            return self._deny(policy_id, "ownerPrincipalMismatch")
        if owner_authority_claims(payload) - {principal_user_id}:
            return self._deny(policy_id, "ownerClaimMismatch")
        return self._allow_owner(
            policy_id,
            reason="ownerPrincipal" if owner_user_id else "ownerDerivedFromPrincipal",
        )

    def _evaluate_resource(
        self,
        *,
        policy_id: str,
        resource_type: Optional[str],
        resource_id: Optional[str],
        principal_user_id: str,
        expected_version: Any,
    ) -> AuthorizationPolicyDecision:
        if not resource_type or not resource_id:
            return self._deny(policy_id, "resourceIdRequired")
        try:
            resolved_type = ResourceType(resource_type)
            authority = ResourceAuthorityResolver(self.store).resolve(
                resolved_type,
                resource_id,
            )
        except Exception:
            return self._deny(policy_id, "resourceResolverFailure")
        if authority is None:
            return self._deny(policy_id, "resourceNotFound")
        if authority.authority_state != "active":
            return self._deny(policy_id, "resourceQuarantined")
        if expected_version is not None:
            try:
                normalized_expected_version = int(expected_version)
            except (TypeError, ValueError):
                return self._deny(policy_id, "resourceVersionInvalid")
            if normalized_expected_version < 1 or normalized_expected_version != authority.row_version:
                return self._deny(policy_id, "resourceVersionMismatch")
        if (
            authority.vault_id != principal_user_id
            or authority.owner_subject_id != principal_user_id
        ):
            return self._deny(policy_id, "resourceOwnerMismatch")
        return self._allow_owner(policy_id, reason="resourceOwner")

    def _evaluate_care_read(
        self,
        *,
        owner_user_id: str,
        principal_user_id: str,
        query: Mapping[str, Any],
    ) -> AuthorizationPolicyDecision:
        if owner_user_id == principal_user_id:
            return self._allow_owner("careSnapshotRead")

        member_id = str(query.get("viewerFamilyMemberID") or "").strip()
        if not member_id:
            return self._deny("careSnapshotRead", "ownerPrincipalRequired")
        member = self._family_member(owner_user_id, member_id)
        if member is None:
            return self._deny("careSnapshotRead", "familyMemberMissing")
        if not self._is_active_family_member(member):
            return self._deny("careSnapshotRead", "familyAccessInactive")
        if principal_user_id not in self._family_member_principal_ids(member):
            return self._deny("careSnapshotRead", "familyPrincipalMismatch")
        access = DelegatedAccessService(self.store).authorize(
            owner_subject_id=owner_user_id,
            grantee_subject_id=principal_user_id,
            family_member_id=member_id,
            purpose=AccessGrantPurpose.CARE_SNAPSHOT,
            operation=GrantOperation.READ,
            resource_type=ResourceScopeType.CARE_SNAPSHOT,
        )
        if not access.allowed:
            return self._deny("careSnapshotRead", access.reason)
        return AuthorizationPolicyDecision(
            policy_id="careSnapshotRead",
            decision="allowFamily",
            reason="activeFamilyPrincipal",
            allowed=True,
            delegated=True,
            principal_bound=True,
            grant_id=access.grant_id,
            grant_receipt_id=access.receipt_id,
        )

    def _evaluate_time_letter_detail(
        self,
        *,
        owner_user_id: str,
        item_id: str,
        principal_user_id: str,
        query: Mapping[str, Any],
    ) -> AuthorizationPolicyDecision:
        viewer_user_id = str(query.get("viewerUserId") or "").strip()
        if not viewer_user_id:
            return self._defer("timeLetterDetail", "routeValidationPending")
        if viewer_user_id != principal_user_id:
            return self._deny("timeLetterDetail", "viewerPrincipalMismatch")
        if owner_user_id == principal_user_id:
            return self._allow_owner("timeLetterDetail")

        item = self._time_letter(owner_user_id, item_id)
        if item is None:
            return self._defer("timeLetterDetail", "resourceValidationPending")
        if not is_time_letter_open(item):
            return self._deny("timeLetterDetail", "timeLetterNotOpen")
        for recipient in time_letter_recipient_records(item):
            recipient_id = str(recipient.get("id") or "").strip()
            if not recipient_id or recipient_id == "self":
                continue
            member = self._family_member(owner_user_id, recipient_id)
            if member is None:
                continue
            if not self._is_active_family_member(member):
                continue
            if principal_user_id not in self._family_member_principal_ids(member):
                continue
            access = DelegatedAccessService(self.store).authorize(
                owner_subject_id=owner_user_id,
                grantee_subject_id=principal_user_id,
                family_member_id=recipient_id,
                purpose=AccessGrantPurpose.TIME_LETTER_READ,
                operation=GrantOperation.READ,
                resource_type=ResourceScopeType.TIME_LETTER,
                resource_id=item_id,
            )
            if access.allowed:
                return AuthorizationPolicyDecision(
                    policy_id="timeLetterDetail",
                    decision="allowRecipient",
                    reason="activeTimeLetterRecipient",
                    allowed=True,
                    delegated=True,
                    principal_bound=True,
                    grant_id=access.grant_id,
                    grant_receipt_id=access.receipt_id,
                )
        return self._deny("timeLetterDetail", "activeGrantRequired")

    def _evaluate_invitation_accept(
        self,
        *,
        principal_user_id: str,
        payload: Mapping[str, Any],
    ) -> AuthorizationPolicyDecision:
        phone = str(payload.get("phone") or "").strip()
        if not phone:
            return self._defer("familyInvitationAccept", "routeValidationPending")
        if stable_user_id(phone) != principal_user_id:
            return self._deny("familyInvitationAccept", "invitationPrincipalMismatch")
        return AuthorizationPolicyDecision(
            policy_id="familyInvitationAccept",
            decision="allowRecipient",
            reason="invitationPhonePrincipal",
            allowed=True,
            delegated=True,
            principal_bound=True,
        )

    def _evaluate_family_member_accept(
        self,
        *,
        owner_user_id: str,
        member_id: str,
        principal_user_id: str,
    ) -> AuthorizationPolicyDecision:
        member = self._family_member(owner_user_id, member_id)
        if member is None:
            return self._defer("familyMemberAccept", "resourceValidationPending")
        if principal_user_id in self._family_member_principal_ids(member):
                return AuthorizationPolicyDecision(
                policy_id="familyMemberAccept",
                decision="allowRecipient",
                reason="invitationPhonePrincipal",
                allowed=True,
                delegated=True,
                principal_bound=True,
                )
        return self._deny("familyMemberAccept", "invitationPrincipalMismatch")

    def _family_member(self, owner_user_id: str, member_id: str) -> Optional[Dict[str, Any]]:
        for member in self.store.list_family_members(owner_user_id):
            if str(member.get("id") or "").strip() == member_id:
                return member
        return None

    def _time_letter(self, owner_user_id: str, item_id: str) -> Optional[Dict[str, Any]]:
        for item in self.store.list_archive_items(owner_user_id):
            if str(item.get("id") or "").strip() != item_id:
                continue
            if str(item.get("kind") or "").strip() == "timeLetter":
                return item
        return None

    @staticmethod
    def _is_active_family_member(member: Mapping[str, Any]) -> bool:
        return (
            str(member.get("accessStatus") or "") == "active"
            and str(member.get("invitationStatus") or "") == "accepted"
        )

    @staticmethod
    def _family_member_principal_ids(member: Mapping[str, Any]) -> Set[str]:
        candidates = {
            str(member.get("memberUserId") or "").strip(),
            str(member.get("acceptedUserId") or "").strip(),
            str(member.get("recipientUserId") or "").strip(),
        }
        phone = str(member.get("phone") or "").strip()
        if phone:
            candidates.add(stable_user_id(phone))
        return {candidate for candidate in candidates if candidate}

    @staticmethod
    def _allow_owner(
        policy_id: str,
        *,
        reason: str = "ownerPrincipal",
    ) -> AuthorizationPolicyDecision:
        return AuthorizationPolicyDecision(
            policy_id=policy_id,
            decision="allowOwner",
            reason=reason,
            allowed=True,
            principal_bound=True,
        )

    @staticmethod
    def _deny(policy_id: str, reason: str) -> AuthorizationPolicyDecision:
        return AuthorizationPolicyDecision(
            policy_id=policy_id,
            decision="deny",
            reason=reason,
            allowed=False,
            principal_bound=True,
        )

    @staticmethod
    def _defer(policy_id: str, reason: str) -> AuthorizationPolicyDecision:
        return AuthorizationPolicyDecision(
            policy_id=policy_id,
            decision="defer",
            reason=reason,
            allowed=None,
            terminal=False,
        )
