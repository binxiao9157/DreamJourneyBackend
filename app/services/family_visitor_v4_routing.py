"""Fail-closed routing between Owner private V4 and Visitor publication reads."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


def _identifier(value: str | None) -> str:
    return str(value or "").strip()


class FamilyVisitorV4Route(str, Enum):
    OWNER_PRIVATE = "ownerPrivate"
    VISITOR_PUBLIC = "visitorPublic"
    FAMILY_CONTRIBUTION = "familyContribution"
    DENIED = "denied"


@dataclass(frozen=True)
class FamilyVisitorV4RouteDecision:
    route: FamilyVisitorV4Route
    reason: str
    required_authority: str
    private_context_allowed: bool = False
    legacy_fallback_allowed: bool = False

    def denial_payload(self) -> dict[str, object]:
        return {
            "code": "familyPrivateContextDenied",
            "route": self.route.value,
            "reason": self.reason,
            "requiredAuthority": self.required_authority,
            "privateContextAllowed": self.private_context_allowed,
            "legacyFallbackAllowed": self.legacy_fallback_allowed,
        }


class FamilyVisitorV4RoutingPolicy:
    """Selects one authority domain without ever widening a relationship.

    Family relationships are navigation identity only. They do not create an
    Owner command context and cannot authorize reads from Source, Candidate,
    MemoryVersion, private Projection, Archive, or KBLite. A non-owner query is
    either bound to a matching VisitorSession/PublicProjection or redirected to
    the separately granted family-contribution workflow.
    """

    @staticmethod
    def evaluate(
        *,
        principal_subject_id: str,
        target_owner_subject_id: str,
        persona_scope: str,
        relationship_accepted: bool,
        visitor_session_owner_subject_id: str | None,
        visitor_session_active: bool,
    ) -> FamilyVisitorV4RouteDecision:
        principal = _identifier(principal_subject_id)
        target = _identifier(target_owner_subject_id)
        scope = _identifier(persona_scope).lower() or "personal"
        visitor_owner = _identifier(visitor_session_owner_subject_id)

        if not principal or not target:
            return FamilyVisitorV4RouteDecision(
                route=FamilyVisitorV4Route.DENIED,
                reason="identityScopeInvalid",
                required_authority="authenticatedPrincipal",
            )
        if principal == target and scope in {"personal", "self"}:
            return FamilyVisitorV4RouteDecision(
                route=FamilyVisitorV4Route.OWNER_PRIVATE,
                reason="ownerPrincipalMatched",
                required_authority="ownerTruthCommandContext",
                private_context_allowed=True,
            )
        if not relationship_accepted:
            return FamilyVisitorV4RouteDecision(
                route=FamilyVisitorV4Route.DENIED,
                reason="familyRelationshipRequired",
                required_authority="acceptedFamilyRelationship",
            )
        if visitor_session_active and visitor_owner == target:
            return FamilyVisitorV4RouteDecision(
                route=FamilyVisitorV4Route.VISITOR_PUBLIC,
                reason="visitorSessionMatched",
                required_authority="visitorSession",
            )
        return FamilyVisitorV4RouteDecision(
            route=FamilyVisitorV4Route.FAMILY_CONTRIBUTION,
            reason=(
                "visitorSessionOwnerMismatch"
                if visitor_session_active and visitor_owner
                else "shareGrantRequired"
            ),
            required_authority="shareGrantOrContributionGrant",
        )
