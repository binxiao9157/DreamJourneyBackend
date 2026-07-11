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


@dataclass(frozen=True)
class RouteOwnershipRule:
    method: str
    path_template: str
    category: RouteOwnershipCategory
    policy_id: str
    owner_body_field: Optional[str] = None
    owner_path_parameter: Optional[str] = None


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


def _rule(
    method: str,
    path_template: str,
    category: RouteOwnershipCategory,
    policy_id: str,
    *,
    owner_body_field: Optional[str] = None,
    owner_path_parameter: Optional[str] = None,
) -> RouteOwnershipRule:
    return RouteOwnershipRule(
        method=method.upper(),
        path_template=path_template,
        category=category,
        policy_id=policy_id,
        owner_body_field=owner_body_field,
        owner_path_parameter=owner_path_parameter,
    )


def _owner_body(method: str, path_template: str, policy_id: str) -> RouteOwnershipRule:
    return _rule(
        method,
        path_template,
        RouteOwnershipCategory.OWNER_BODY,
        policy_id,
        owner_body_field="userId",
    )


def _owner_path(
    method: str,
    path_template: str,
    policy_id: str,
    *,
    parameter: str = "user_id",
) -> RouteOwnershipRule:
    return _rule(
        method,
        path_template,
        RouteOwnershipCategory.OWNER_PATH,
        policy_id,
        owner_path_parameter=parameter,
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
            _owner_body("POST", "/digital-human/sessions", "digitalHumanOwner"),
            _owner_body("POST", "/digital-human/sessions/{session_id}/heartbeat", "digitalHumanOwner"),
            _owner_body("POST", "/digital-human/sessions/{session_id}/release", "digitalHumanOwner"),
            _rule("POST", "/auth/login", public, "publicLogin"),
            _rule("POST", "/auth/refresh", public, "publicRefresh"),
            _rule("POST", "/auth/logout", session, "userSession"),
            _owner_body("POST", "/auth/delete", "accountOwner"),
            _rule("POST", "/auth/restore", public, "publicAccountRestore"),
            _rule("POST", "/auth/purge-expired-deletions", system, "systemAccountPurge"),
            _owner_body("POST", "/auth/password", "accountOwner"),
            _owner_body("POST", "/profile", "profileOwner"),
            _owner_path("GET", "/profile/{user_id}", "profileOwner"),
            _rule("GET", "/config/runtime", service, "authenticatedRuntimeConfig"),
            _owner_body("POST", "/context/build", "contextOwner"),
            _owner_body("POST", "/voice/realtime-token", "voiceOwner"),
            _owner_body("POST", "/voice/profiles", "voiceOwner"),
            _owner_path("GET", "/voice/profiles/{user_id}", "voiceOwner"),
            _owner_path("POST", "/voice/profiles/{user_id}/{voice_profile_id}/disable", "voiceOwner"),
            _owner_path("POST", "/voice/profiles/{user_id}/{voice_profile_id}/refresh", "voiceOwner"),
            _owner_path(
                "POST",
                "/voice/profiles/{user_id}/{voice_profile_id}/quality-acceptance",
                "voiceOwner",
            ),
            _owner_body("POST", "/voice/synthesis", "voiceOwner"),
            _owner_path("DELETE", "/voice/profiles/{user_id}/{voice_profile_id}", "voiceOwner"),
            _owner_body("POST", "/tts", "voiceOwner"),
            _rule("GET", "/maps/district", service, "authenticatedDistrictMap"),
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
            _owner_body("POST", "/archive/media/upload-intent", "archiveOwner"),
            _owner_path("GET", "/archive/items/{user_id}", "archiveOwner"),
            _rule(
                "GET",
                "/archive/time-letters/{owner_user_id}/{item_id}/detail",
                delegated,
                "timeLetterViewer",
            ),
            _owner_path("DELETE", "/archive/items/{user_id}/{item_id}", "archiveOwner"),
            _owner_body("POST", "/archive/image-analysis", "archiveOwner"),
            _rule("POST", "/mailbox/letters", system, "systemMailboxDelivery"),
            _owner_path("GET", "/mailbox/letters/{user_id}", "mailboxOwner"),
            _owner_path("POST", "/mailbox/letters/{user_id}/{letter_id}/read", "mailboxOwner"),
            _owner_path("POST", "/mailbox/letters/{user_id}/{letter_id}/archive", "mailboxOwner"),
            _owner_body("POST", "/devices/push-token", "deviceOwner"),
            _owner_body("POST", "/echo/delayed-replies", "echoOwner"),
            _rule("POST", "/echo/delayed-replies/dispatch-due", system, "systemEchoDispatch"),
            _rule("POST", "/archive/time-letters/dispatch-due", system, "systemTimeLetterDispatch"),
            _owner_path("GET", "/echo/delayed-replies/{user_id}", "echoOwner"),
            _owner_body("POST", "/family/invite", "familyOwner"),
            _owner_path("GET", "/family/members/{user_id}", "familyOwner"),
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
            _owner_path("POST", "/family/members/{user_id}/{member_id}/revoke", "familyOwner"),
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
        return {
            "routeCount": len(self.rules),
            "categoryCounts": dict(sorted(category_counts.items())),
            "unclassifiedCount": 0,
            "routes": [
                {
                    "method": rule.method,
                    "pathTemplate": rule.path_template,
                    "category": rule.category.value,
                    "policy": rule.policy_id,
                }
                for rule in self.rules
            ],
        }


def route_keys(rules: Iterable[RouteOwnershipRule]) -> set[tuple[str, str]]:
    return {(rule.method, rule.path_template) for rule in rules}
