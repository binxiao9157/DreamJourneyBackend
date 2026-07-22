from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Iterable, Optional


class RouteOwnershipCategory(str, Enum):
    PUBLIC = "public"
    AUTHENTICATED_SERVICE = "authenticatedService"
    USER_SESSION = "userSession"
    OWNER_BODY = "ownerBody"
    OWNER_PATH = "ownerPath"
    DELEGATED = "delegated"
    SYSTEM_ONLY = "systemOnly"


class RouteAuthenticationMode(str, Enum):
    PUBLIC = "public"
    USER = "user"
    MACHINE = "machine"


@dataclass(frozen=True)
class RouteOwnershipRule:
    method: str
    path_template: str
    category: RouteOwnershipCategory
    policy_id: str
    owner_body_field: Optional[str] = None
    owner_path_parameter: Optional[str] = None
    resource_type: Optional[str] = None
    resource_id_body_field: Optional[str] = None
    resource_id_path_parameter: Optional[str] = None
    resource_operation: Optional[str] = None
    requires_existing_resource: bool = False
    auth_mode: RouteAuthenticationMode = RouteAuthenticationMode.USER
    required_audience: Optional[str] = "dreamjourney-user"
    required_scopes: tuple[str, ...] = ("user:api",)


@dataclass(frozen=True)
class RouteOwnershipMatch:
    rule: RouteOwnershipRule
    path_parameters: Dict[str, str]

    def owner_user_id(self, payload: Dict[str, Any]) -> Optional[str]:
        if self.rule.owner_body_field:
            value = payload.get(self.rule.owner_body_field)
        elif self.rule.owner_path_parameter:
            value = self.path_parameters.get(self.rule.owner_path_parameter)
        else:
            return None
        normalized = str(value or "").strip()
        return normalized or None

    def resource_id(self, payload: Dict[str, Any]) -> Optional[str]:
        if self.rule.resource_id_body_field:
            value = payload.get(self.rule.resource_id_body_field)
        elif self.rule.resource_id_path_parameter:
            value = self.path_parameters.get(self.rule.resource_id_path_parameter)
        else:
            return None
        normalized = str(value or "").strip()
        return normalized or None


def _rule(
    method: str,
    path_template: str,
    category: RouteOwnershipCategory,
    policy_id: str,
    *,
    owner_body_field: Optional[str] = None,
    owner_path_parameter: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id_body_field: Optional[str] = None,
    resource_id_path_parameter: Optional[str] = None,
    resource_operation: Optional[str] = None,
    requires_existing_resource: bool = False,
    auth_mode: Optional[RouteAuthenticationMode] = None,
    required_audience: Optional[str] = None,
    required_scopes: Iterable[str] = (),
) -> RouteOwnershipRule:
    resolved_auth_mode = auth_mode
    if resolved_auth_mode is None:
        if category == RouteOwnershipCategory.PUBLIC:
            resolved_auth_mode = RouteAuthenticationMode.PUBLIC
        elif category == RouteOwnershipCategory.SYSTEM_ONLY:
            resolved_auth_mode = RouteAuthenticationMode.MACHINE
        else:
            resolved_auth_mode = RouteAuthenticationMode.USER
    resolved_audience = required_audience
    resolved_scopes = tuple(sorted(set(required_scopes)))
    if resolved_auth_mode == RouteAuthenticationMode.USER:
        resolved_audience = resolved_audience or "dreamjourney-user"
        resolved_scopes = resolved_scopes or ("user:api",)
    elif resolved_auth_mode == RouteAuthenticationMode.MACHINE:
        resolved_audience = resolved_audience or "dreamjourney-backend"
    else:
        resolved_audience = None
        resolved_scopes = ()
    return RouteOwnershipRule(
        method=method.upper(),
        path_template=path_template,
        category=category,
        policy_id=policy_id,
        owner_body_field=owner_body_field,
        owner_path_parameter=owner_path_parameter,
        resource_type=resource_type,
        resource_id_body_field=resource_id_body_field,
        resource_id_path_parameter=resource_id_path_parameter,
        resource_operation=resource_operation,
        requires_existing_resource=requires_existing_resource,
        auth_mode=resolved_auth_mode,
        required_audience=resolved_audience,
        required_scopes=resolved_scopes,
    )


def _owner_body(
    method: str,
    path_template: str,
    policy_id: str,
    **resource: Any,
) -> RouteOwnershipRule:
    return _rule(
        method,
        path_template,
        RouteOwnershipCategory.OWNER_BODY,
        policy_id,
        owner_body_field="userId",
        **resource,
    )


def _owner_path(
    method: str,
    path_template: str,
    policy_id: str,
    *,
    parameter: str = "user_id",
    **resource: Any,
) -> RouteOwnershipRule:
    return _rule(
        method,
        path_template,
        RouteOwnershipCategory.OWNER_PATH,
        policy_id,
        owner_path_parameter=parameter,
        **resource,
    )


class RouteOwnershipRegistry:
    """Single auditable ownership inventory for every FastAPI business route."""

    def __init__(self) -> None:
        public = RouteOwnershipCategory.PUBLIC
        service = RouteOwnershipCategory.AUTHENTICATED_SERVICE
        session = RouteOwnershipCategory.USER_SESSION
        delegated = RouteOwnershipCategory.DELEGATED
        system = RouteOwnershipCategory.SYSTEM_ONLY
        self.rules = (
            _rule("GET", "/health", public, "publicHealth"),
            _rule("GET", "/live", public, "publicLiveness"),
            _rule("GET", "/ready", public, "publicReadiness"),
            _owner_body("POST", "/digital-human/sessions", "digitalHumanOwner"),
            _owner_body(
                "POST",
                "/digital-human/sessions/{session_id}/heartbeat",
                "digitalHumanOwner",
                resource_type="digitalHumanSession",
                resource_id_path_parameter="session_id",
                resource_operation="update",
                requires_existing_resource=True,
            ),
            _owner_body(
                "POST",
                "/digital-human/sessions/{session_id}/release",
                "digitalHumanOwner",
                resource_type="digitalHumanSession",
                resource_id_path_parameter="session_id",
                resource_operation="update",
                requires_existing_resource=True,
            ),
            _rule(
                "POST",
                "/v2/auth/challenges",
                public,
                "publicIdentityChallenge",
            ),
            _rule(
                "POST",
                "/v2/auth/challenges/{challenge_id}/verify",
                public,
                "publicIdentityChallengeVerify",
            ),
            _rule("POST", "/auth/login", public, "publicLogin"),
            _rule("POST", "/auth/refresh", public, "publicRefresh"),
            _rule("POST", "/auth/logout", session, "userSession"),
            _rule("POST", "/auth/data-export", session, "userDataExport"),
            _owner_body("POST", "/auth/delete", "accountOwner"),
            _rule("POST", "/auth/restore", public, "publicAccountRestore"),
            _rule(
                "POST",
                "/auth/purge-expired-deletions",
                system,
                "systemAccountPurge",
                required_scopes=("account:purge",),
            ),
            _owner_body("POST", "/auth/password", "accountOwner"),
            _owner_body("POST", "/profile", "profileOwner"),
            _owner_path("GET", "/profile/{user_id}", "profileOwner"),
            _rule(
                "GET",
                "/config/runtime",
                service,
                "publicRuntimeConfig",
                auth_mode=RouteAuthenticationMode.PUBLIC,
            ),
            _rule("GET", "/v2/release-policy", public, "publicReleasePolicy"),
            _rule(
                "GET",
                "/v2/vaults/{vault_id}/candidates",
                session,
                "ownerTruthCandidateInbox",
            ),
            _rule(
                "POST",
                "/v2/vaults/{vault_id}/candidates/{candidate_id}/decisions",
                session,
                "ownerTruthCandidateDecision",
            ),
            _rule(
                "POST",
                "/v2/vaults/{vault_id}/memory-versions/{memory_version_id}/knowledge-dimension-confirmations",
                session,
                "ownerTruthKnowledgeDimensionConfirmation",
            ),
            _rule(
                "POST",
                "/v2/vaults/{vault_id}/knowledge-recommendations/read",
                session,
                "ownerTruthKnowledgeRecommendationRead",
            ),
            _rule(
                "GET",
                "/v2/vaults/{vault_id}/interview-sessions/{session_id}/state",
                session,
                "ownerTruthInterviewSessionStateRead",
            ),
            _rule(
                "GET",
                "/v2/vaults/{vault_id}/interview-sessions/{session_id}/presentation",
                session,
                "ownerTruthInterviewSessionPresentationRead",
            ),
            _rule(
                "POST",
                "/v2/vaults/{vault_id}/interview-sessions",
                session,
                "ownerTruthInterviewSessionStart",
            ),
            _rule(
                "POST",
                "/v2/vaults/{vault_id}/interview-sessions/{session_id}/messages",
                session,
                "ownerTruthInterviewSessionAppendMessage",
            ),
            _rule(
                "POST",
                "/v2/vaults/{vault_id}/interview-sessions/{session_id}/boundary",
                session,
                "ownerTruthInterviewSessionBoundary",
            ),
            _rule(
                "POST",
                "/v2/vaults/{vault_id}/interview-sessions/{session_id}/restore-do-not-ask",
                session,
                "ownerTruthInterviewSessionRestoreDoNotAsk",
            ),
            _rule(
                "GET",
                "/v2/vaults/{vault_id}/interview-review-batches/{review_batch_id}/candidate-review",
                session,
                "ownerTruthInterviewCandidateReviewRead",
            ),
            _rule(
                "GET",
                "/v2/vaults/{vault_id}/interview-review-batches/{review_batch_id}/confirmation",
                session,
                "ownerTruthInterviewCandidateConfirmationRead",
            ),
            _rule(
                "POST",
                "/v2/vaults/{vault_id}/interview-review-batches/{review_batch_id}/confirmation/batch-accept",
                session,
                "ownerTruthInterviewCandidateConfirmationBatchDecision",
            ),
            _rule(
                "POST",
                "/v2/vaults/{vault_id}/interview-review-batches/{review_batch_id}/candidate-review/batch-accept",
                session,
                "ownerTruthInterviewCandidateBatchDecision",
            ),
            _rule(
                "POST",
                "/v2/vaults/{vault_id}/interview-review-batches/{review_batch_id}/candidate-review/candidates/{candidate_id}/decision",
                session,
                "ownerTruthInterviewCandidateSingleDecision",
            ),
            _rule(
                "GET",
                "/v2/vaults/{vault_id}/memory-projection",
                session,
                "ownerTruthMemoryProjectionRead",
            ),
            _rule(
                "POST",
                "/v2/vaults/{vault_id}/memory-projection/rebuild",
                session,
                "ownerTruthMemoryProjectionRebuild",
            ),
            _rule(
                "GET",
                "/v2/vaults/{vault_id}/kblite-compatibility",
                session,
                "ownerTruthKBLiteCompatibilityRead",
            ),
            _rule(
                "GET",
                "/v2/vaults/{vault_id}/kblite-compatibility/read-envelope",
                session,
                "ownerTruthKBLiteCompatibilityReadEnvelope",
            ),
            _rule(
                "GET",
                "/v2/vaults/{vault_id}/context-shadow",
                session,
                "ownerTruthContextShadowRead",
            ),
            _rule(
                "POST",
                "/v2/vaults/{vault_id}/context-shadow/build",
                session,
                "ownerTruthContextShadowBuild",
            ),
            _rule(
                "POST",
                "/v2/vaults/{vault_id}/answer-citation-receipts",
                session,
                "ownerTruthAnswerCitationReceipt",
            ),
            _rule(
                "POST",
                "/v2/vaults/{vault_id}/memories/{memory_id}/corrections",
                session,
                "ownerTruthCorrectionRequest",
            ),
            _rule(
                "POST",
                "/v2/vaults/{vault_id}/correction-requests/{correction_request_id}/resolve",
                session,
                "ownerTruthCorrectionResolution",
            ),
            _rule(
                "POST",
                "/v2/vaults/{vault_id}/legacy-migration/inventory",
                session,
                "ownerTruthLegacyMigrationInventory",
            ),
            _rule(
                "POST",
                "/v2/vaults/{vault_id}/legacy-migration/shadow-parity",
                session,
                "ownerTruthLegacyShadowParity",
            ),
            _rule(
                "GET",
                "/ops/release-policy/observations",
                system,
                "systemReleasePolicyObservations",
                required_scopes=("releasePolicy:observe",),
            ),
            _rule(
                "POST",
                "/ops/evidence-manifests",
                system,
                "systemEvidenceManifestIssue",
                required_scopes=("evidenceManifest:issue",),
            ),
            _rule(
                "GET",
                "/ops/evidence-manifests",
                system,
                "systemEvidenceManifestObserve",
                required_scopes=("evidenceManifest:observe",),
            ),
            _rule(
                "GET",
                "/ops/data-rights/requests/{request_id}/evidence",
                system,
                "systemDataRightsEvidenceObserve",
                required_scopes=("rightsEvidence:observe",),
            ),
            _rule(
                "POST",
                "/ops/incidents",
                system,
                "systemIncidentManage",
                required_scopes=("incident:manage",),
            ),
            _rule(
                "GET",
                "/ops/incidents/readiness",
                system,
                "systemIncidentObserve",
                required_scopes=("incident:observe",),
            ),
            _rule(
                "GET",
                "/ops/incidents/{incident_id}",
                system,
                "systemIncidentObserve",
                required_scopes=("incident:observe",),
            ),
            _rule(
                "POST",
                "/ops/incidents/{incident_id}/ack",
                system,
                "systemIncidentManage",
                required_scopes=("incident:manage",),
            ),
            _rule(
                "POST",
                "/ops/incidents/{incident_id}/fence",
                system,
                "systemIncidentManage",
                required_scopes=("incident:manage",),
            ),
            _rule(
                "POST",
                "/ops/incidents/{incident_id}/resolve",
                system,
                "systemIncidentManage",
                required_scopes=("incident:manage",),
            ),
            _rule(
                "POST",
                "/ops/incidents/{incident_id}/reopen",
                system,
                "systemIncidentManage",
                required_scopes=("incident:manage",),
            ),
            _owner_body("POST", "/context/build", "contextOwner"),
            _owner_body("POST", "/voice/realtime-token", "voiceOwner"),
            _owner_body("POST", "/voice/profiles", "voiceOwner"),
            _owner_path("GET", "/voice/profiles/{user_id}", "voiceOwner"),
            _owner_path(
                "POST",
                "/voice/profiles/{user_id}/{voice_profile_id}/disable",
                "voiceOwner",
                resource_type="voiceProfile",
                resource_id_path_parameter="voice_profile_id",
                resource_operation="update",
                requires_existing_resource=True,
            ),
            _owner_path(
                "POST",
                "/voice/profiles/{user_id}/{voice_profile_id}/refresh",
                "voiceOwner",
                resource_type="voiceProfile",
                resource_id_path_parameter="voice_profile_id",
                resource_operation="update",
                requires_existing_resource=True,
            ),
            _owner_path(
                "POST",
                "/voice/profiles/{user_id}/{voice_profile_id}/quality-acceptance",
                "voiceOwner",
                resource_type="voiceProfile",
                resource_id_path_parameter="voice_profile_id",
                resource_operation="update",
                requires_existing_resource=True,
            ),
            _owner_body(
                "POST",
                "/voice/synthesis",
                "voiceOwner",
                resource_type="voiceProfile",
                resource_id_body_field="voiceProfileId",
                resource_operation="execute",
                requires_existing_resource=True,
            ),
            _owner_path(
                "DELETE",
                "/voice/profiles/{user_id}/{voice_profile_id}",
                "voiceOwner",
                resource_type="voiceProfile",
                resource_id_path_parameter="voice_profile_id",
                resource_operation="delete",
                requires_existing_resource=True,
            ),
            _owner_body("POST", "/tts", "voiceOwner"),
            _rule(
                "GET",
                "/maps/district",
                service,
                "authenticatedDistrictMap",
                auth_mode=RouteAuthenticationMode.USER,
            ),
            _owner_body("POST", "/kb/sync", "knowledgeOwner"),
            _owner_body("POST", "/kb/mutations", "knowledgeOwner"),
            _owner_body("POST", "/kb/governance/actions", "knowledgeOwner"),
            _owner_path("GET", "/kb/snapshot/{user_id}", "knowledgeOwner"),
            _owner_path("GET", "/kb/changes/{user_id}", "knowledgeOwner"),
            _owner_path("GET", "/kb/source-ref-audit/{user_id}", "knowledgeOwner"),
            _owner_body("POST", "/kb/extract", "knowledgeOwner"),
            _owner_body("POST", "/memories", "memoryOwner"),
            _owner_path("GET", "/memories/{user_id}", "memoryOwner"),
            _owner_body("POST", "/archive/photos", "archiveOwner"),
            _owner_body("POST", "/archive/items", "archiveOwner"),
            _owner_body(
                "POST",
                "/archive/media/upload-intent",
                "archiveOwner",
                resource_type="archiveItem",
                resource_id_body_field="archiveItemId",
                resource_operation="update",
                requires_existing_resource=True,
            ),
            _owner_path("GET", "/archive/items/{user_id}", "archiveOwner"),
            _rule(
                "GET",
                "/archive/time-letters/{owner_user_id}/{item_id}/detail",
                delegated,
                "timeLetterViewer",
            ),
            _owner_path(
                "DELETE",
                "/archive/items/{user_id}/{item_id}",
                "archiveOwner",
                resource_type="archiveItem",
                resource_id_path_parameter="item_id",
                resource_operation="delete",
                requires_existing_resource=True,
            ),
            _owner_body(
                "POST",
                "/archive/image-analysis",
                "archiveOwner",
                resource_type="archiveItem",
                resource_id_body_field="archiveItemId",
                resource_operation="execute",
                requires_existing_resource=True,
            ),
            _rule(
                "POST",
                "/mailbox/letters",
                system,
                "systemMailboxDelivery",
                required_scopes=("mailbox:deliver",),
            ),
            _owner_path("GET", "/mailbox/letters/{user_id}", "mailboxOwner"),
            _owner_path(
                "POST",
                "/mailbox/letters/{user_id}/{letter_id}/read",
                "mailboxOwner",
                resource_type="mailboxLetter",
                resource_id_path_parameter="letter_id",
                resource_operation="update",
                requires_existing_resource=True,
            ),
            _owner_path(
                "POST",
                "/mailbox/letters/{user_id}/{letter_id}/archive",
                "mailboxOwner",
                resource_type="mailboxLetter",
                resource_id_path_parameter="letter_id",
                resource_operation="update",
                requires_existing_resource=True,
            ),
            _owner_body("POST", "/devices/push-token", "deviceOwner"),
            _owner_body("POST", "/echo/delayed-replies", "echoOwner"),
            _rule(
                "POST",
                "/echo/delayed-replies/dispatch-due",
                system,
                "systemEchoDispatch",
                required_scopes=("echo:dispatch",),
            ),
            _rule(
                "POST",
                "/archive/time-letters/dispatch-due",
                system,
                "systemTimeLetterDispatch",
                required_scopes=("timeLetter:dispatch",),
            ),
            _owner_path("GET", "/echo/delayed-replies/{user_id}", "echoOwner"),
            _owner_path(
                "GET",
                "/echo/delayed-replies/{user_id}/{delayed_reply_id}/answer",
                "echoOwner",
            ),
            _owner_body("POST", "/family/invite", "familyOwner"),
            _owner_path("GET", "/family/members/{user_id}", "familyOwner"),
            _owner_body("POST", "/family/access-grants", "familyGrantOwner"),
            _owner_path("GET", "/family/access-grants/{user_id}", "familyGrantOwner"),
            _owner_path(
                "POST",
                "/family/access-grants/{user_id}/{grant_id}/revoke",
                "familyGrantOwner",
            ),
            _owner_path(
                "POST",
                "/family/relationships/{user_id}/{relationship_id}/lifecycle",
                "familyRelationshipOwner",
            ),
            _rule(
                "POST",
                "/family/members/{user_id}/{member_id}/accept",
                delegated,
                "familyInvitationAcceptance",
            ),
            _rule(
                "POST",
                "/family/invitations/{invitation_code}/accept",
                delegated,
                "familyInvitationAcceptance",
            ),
            _owner_path(
                "POST",
                "/family/members/{user_id}/{member_id}/revoke",
                "familyOwner",
                resource_type="familyMember",
                resource_id_path_parameter="member_id",
                resource_operation="update",
                requires_existing_resource=True,
            ),
            _rule("POST", "/care/snapshots", delegated, "careViewer"),
            _rule("GET", "/care/snapshots/latest/{user_id}", delegated, "careViewer"),
            _rule("GET", "/care/snapshots/{user_id}", delegated, "careViewer"),
        )
        self._compiled = tuple((rule, self._compile_template(rule.path_template)) for rule in self.rules)

    @staticmethod
    def _compile_template(path_template: str) -> re.Pattern[str]:
        cursor = 0
        parts = []
        for match in re.finditer(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", path_template):
            parts.append(re.escape(path_template[cursor : match.start()]))
            parts.append(f"(?P<{match.group(1)}>[^/]+)")
            cursor = match.end()
        parts.append(re.escape(path_template[cursor:]))
        return re.compile("^" + "".join(parts) + "$")

    def match(self, method: str, path: str) -> Optional[RouteOwnershipMatch]:
        normalized_method = method.upper()
        for rule, pattern in self._compiled:
            if rule.method != normalized_method:
                continue
            match = pattern.fullmatch(path)
            if match is not None:
                return RouteOwnershipMatch(rule=rule, path_parameters=match.groupdict())
        return None

    def rule_for_template(self, method: str, path_template: str) -> RouteOwnershipRule:
        normalized_method = method.upper()
        for rule in self.rules:
            if rule.method == normalized_method and rule.path_template == path_template:
                return rule
        raise KeyError(f"unclassified route: {normalized_method} {path_template}")

    def audit_summary(self) -> Dict[str, Any]:
        category_counts = Counter(rule.category.value for rule in self.rules)
        auth_mode_counts = Counter(rule.auth_mode.value for rule in self.rules)
        return {
            "routeCount": len(self.rules),
            "categoryCounts": dict(sorted(category_counts.items())),
            "authModeCounts": dict(sorted(auth_mode_counts.items())),
            "unclassifiedCount": 0,
            "routes": [
                {
                    "method": rule.method,
                    "pathTemplate": rule.path_template,
                    "category": rule.category.value,
                    "policy": rule.policy_id,
                    "authMode": rule.auth_mode.value,
                    "requiredAudience": rule.required_audience,
                    "requiredScopes": list(rule.required_scopes),
                    "resourceType": rule.resource_type,
                    "resourceOperation": rule.resource_operation,
                    "requiresExistingResource": rule.requires_existing_resource,
                }
                for rule in self.rules
            ],
        }


def route_keys(rules: Iterable[RouteOwnershipRule]) -> set[tuple[str, str]]:
    return {(rule.method, rule.path_template) for rule in rules}
