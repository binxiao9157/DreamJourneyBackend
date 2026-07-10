from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Set

from app.services.route_ownership import RouteOwnershipCategory, RouteOwnershipRegistry
from app.services.time_letters import time_letter_recipient_records
from app.services.user_identity import stable_user_id


@dataclass(frozen=True)
class AuthorizationPolicyDecision:
    policy_id: str
    decision: str
    reason: str
    allowed: Optional[bool]
    delegated: bool = False
    terminal: bool = True
    principal_bound: bool = False

    def header_values(self) -> Dict[str, str]:
        return {
            "policy": self.policy_id,
            "decision": self.decision,
            "reason": self.reason,
        }


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
            return self._evaluate_owner_write(
                policy_id=rule.policy_id,
                owner_user_id=route_match.owner_user_id(dict(payload)),
                principal_user_id=principal_user_id,
            )
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
    ) -> AuthorizationPolicyDecision:
        if not owner_user_id:
            return self._defer(policy_id, "routeValidationPending")
        if owner_user_id == principal_user_id:
            return self._allow_owner(policy_id)
        return self._deny(policy_id, "ownerPrincipalMismatch")

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
        return AuthorizationPolicyDecision(
            policy_id="careSnapshotRead",
            decision="allowFamily",
            reason="activeFamilyPrincipal",
            allowed=True,
            delegated=True,
            principal_bound=True,
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
        for recipient in time_letter_recipient_records(item):
            recipient_id = str(recipient.get("id") or "").strip()
            if not recipient_id or recipient_id == "self":
                continue
            member = self._family_member(owner_user_id, recipient_id)
            if member is None:
                continue
            if not self._is_active_family_member(member):
                continue
            if principal_user_id in self._family_member_principal_ids(member):
                return AuthorizationPolicyDecision(
                    policy_id="timeLetterDetail",
                    decision="allowRecipient",
                    reason="activeTimeLetterRecipient",
                    allowed=True,
                    delegated=True,
                    principal_bound=True,
                )
        return self._deny("timeLetterDetail", "viewerNotRecipient")

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
        if owner_user_id == principal_user_id:
            return self._allow_owner("familyMemberAccept")
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
    def _allow_owner(policy_id: str) -> AuthorizationPolicyDecision:
        return AuthorizationPolicyDecision(
            policy_id=policy_id,
            decision="allowOwner",
            reason="ownerPrincipal",
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
