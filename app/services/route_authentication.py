from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Any, Deque, Dict, Iterable, Optional

from app.services.route_ownership import (
    RouteAuthenticationMode,
    RouteOwnershipRegistry,
    route_keys,
)


PRODUCTION_ENVIRONMENTS = frozenset({"prod", "production"})
USER_API_AUDIENCE = "dreamjourney-user"
USER_API_SCOPE = "user:api"
MACHINE_API_AUDIENCE = "dreamjourney-backend"
MACHINE_SYSTEM_SCOPES = frozenset(
    {
        "account:purge",
        "echo:dispatch",
        "mailbox:deliver",
        "releasePolicy:observe",
        "timeLetter:dispatch",
    }
)


class PrincipalKind(str, Enum):
    ANONYMOUS = "anonymous"
    USER = "user"
    MACHINE = "machine"


@dataclass(frozen=True)
class RequestPrincipal:
    kind: PrincipalKind
    principal_id: Optional[str] = None
    session_id: Optional[str] = None
    token_family_id: Optional[str] = None
    session_version: Optional[int] = None
    audience: Optional[str] = None
    scopes: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if self.kind == PrincipalKind.ANONYMOUS:
            if any(
                value is not None
                for value in (
                    self.principal_id,
                    self.session_id,
                    self.token_family_id,
                    self.session_version,
                    self.audience,
                )
            ) or self.scopes:
                raise ValueError("anonymous principal cannot carry identity fields")
            return
        if not str(self.principal_id or "").strip():
            raise ValueError("authenticated principal id is required")
        if not str(self.audience or "").strip():
            raise ValueError("authenticated principal audience is required")
        if not self.scopes:
            raise ValueError("authenticated principal scopes are required")
        if self.kind == PrincipalKind.USER:
            if not str(self.session_id or "").strip():
                raise ValueError("user principal session id is required")
            if not str(self.token_family_id or "").strip():
                raise ValueError("user principal token family id is required")
            if int(self.session_version or 0) < 1:
                raise ValueError("user principal session version is required")

    @classmethod
    def anonymous(cls) -> "RequestPrincipal":
        return cls(kind=PrincipalKind.ANONYMOUS)

    @classmethod
    def user(
        cls,
        *,
        principal_id: str,
        session_id: str,
        token_family_id: str,
        session_version: int,
    ) -> "RequestPrincipal":
        return cls(
            kind=PrincipalKind.USER,
            principal_id=principal_id,
            session_id=session_id,
            token_family_id=token_family_id,
            session_version=session_version,
            audience=USER_API_AUDIENCE,
            scopes=frozenset({USER_API_SCOPE}),
        )

    @classmethod
    def machine(
        cls,
        *,
        principal_id: str,
        audience: str,
        scopes: Iterable[str],
    ) -> "RequestPrincipal":
        return cls(
            kind=PrincipalKind.MACHINE,
            principal_id=principal_id,
            audience=audience,
            scopes=frozenset(str(scope).strip() for scope in scopes if str(scope).strip()),
        )

    def diagnostic_descriptor(self) -> Dict[str, Any]:
        return {
            "kind": self.kind.value,
            "principalIdPresent": self.principal_id is not None,
            "sessionIdPresent": self.session_id is not None,
            "sessionVersion": self.session_version,
            "audience": self.audience,
            "scopes": sorted(self.scopes),
        }

    def get(self, key: str, default: Any = None) -> Any:
        values = {
            "kind": self.kind.value,
            "userId": self.principal_id if self.kind == PrincipalKind.USER else None,
            "principalId": self.principal_id,
            "sessionId": self.session_id,
            "tokenFamilyId": self.token_family_id,
            "sessionVersion": self.session_version,
            "audience": self.audience,
            "scopes": self.scopes,
        }
        return values.get(key, default)

    def __getitem__(self, key: str) -> Any:
        sentinel = object()
        value = self.get(key, sentinel)
        if value is sentinel:
            raise KeyError(key)
        return value


@dataclass(frozen=True)
class RouteAuthenticationDecision:
    policy_id: str
    decision: str
    reason: str
    allowed: bool
    route_label: str
    principal_kind: PrincipalKind

    def header_values(self) -> Dict[str, str]:
        return {
            "policy": self.policy_id,
            "decision": self.decision,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class RouteAuthenticationObservation:
    policy_id: str
    principal_kind: str
    decision: str
    reason: str
    occurred_at: datetime


class RouteAuthenticationDecisionRecorder:
    """Bounded, value-free route authorization numerator and denominator."""

    def __init__(self, *, max_events: int = 1000) -> None:
        self._events: Deque[RouteAuthenticationObservation] = deque(
            maxlen=max(1, max_events)
        )
        self._lock = Lock()

    def record(
        self,
        decision: RouteAuthenticationDecision,
        *,
        occurred_at: Optional[datetime] = None,
    ) -> None:
        instant = occurred_at or datetime.now(timezone.utc)
        if instant.tzinfo is None or instant.utcoffset() is None:
            instant = instant.replace(tzinfo=timezone.utc)
        observation = RouteAuthenticationObservation(
            policy_id=decision.policy_id,
            principal_kind=decision.principal_kind.value,
            decision=decision.decision,
            reason=decision.reason,
            occurred_at=instant.astimezone(timezone.utc),
        )
        with self._lock:
            self._events.append(observation)

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            events = list(self._events)
        return {
            "schemaVersion": 1,
            "eventCount": len(events),
            "decisionCounts": dict(sorted(Counter(item.decision for item in events).items())),
            "reasonCounts": dict(sorted(Counter(item.reason for item in events).items())),
            "policyCounts": dict(sorted(Counter(item.policy_id for item in events).items())),
            "principalKindCounts": dict(
                sorted(Counter(item.principal_kind for item in events).items())
            ),
            "windowStartedAt": events[0].occurred_at.isoformat() if events else None,
            "windowEndedAt": events[-1].occurred_at.isoformat() if events else None,
            "valueFree": True,
        }


class RouteAuthenticationPolicy:
    def __init__(self, registry: Optional[RouteOwnershipRegistry] = None) -> None:
        self.registry = registry or RouteOwnershipRegistry()

    def evaluate(
        self,
        *,
        method: str,
        path: str,
        principal: RequestPrincipal,
    ) -> RouteAuthenticationDecision:
        route_match = self.registry.match(method, path)
        route_label = f"{str(method or '').upper()} {str(path or '')}"
        if route_match is None:
            return self._deny(
                policy_id="routeAuthentication",
                reason="routeNotClassified",
                route_label=route_label,
                principal=principal,
            )

        rule = route_match.rule
        route_label = f"{rule.method} {rule.path_template}"
        if rule.auth_mode == RouteAuthenticationMode.PUBLIC:
            return self._allow(rule.policy_id, "publicRoute", route_label, principal)
        if rule.auth_mode == RouteAuthenticationMode.USER:
            if principal.kind != PrincipalKind.USER:
                return self._deny(rule.policy_id, "userPrincipalRequired", route_label, principal)
            return self._evaluate_contract(rule, principal, route_label, "userPrincipalAuthorized")
        if rule.auth_mode == RouteAuthenticationMode.MACHINE:
            if principal.kind != PrincipalKind.MACHINE:
                return self._deny(rule.policy_id, "machinePrincipalRequired", route_label, principal)
            return self._evaluate_contract(rule, principal, route_label, "machineScopeAuthorized")
        return self._deny(rule.policy_id, "authModeUnsupported", route_label, principal)

    def _evaluate_contract(
        self,
        rule: Any,
        principal: RequestPrincipal,
        route_label: str,
        allowed_reason: str,
    ) -> RouteAuthenticationDecision:
        if rule.required_audience and principal.audience != rule.required_audience:
            return self._deny(
                rule.policy_id,
                "principalAudienceMismatch",
                route_label,
                principal,
            )
        if not set(rule.required_scopes).issubset(principal.scopes):
            return self._deny(
                rule.policy_id,
                "principalScopeMissing",
                route_label,
                principal,
            )
        return self._allow(rule.policy_id, allowed_reason, route_label, principal)

    @staticmethod
    def _allow(
        policy_id: str,
        reason: str,
        route_label: str,
        principal: RequestPrincipal,
    ) -> RouteAuthenticationDecision:
        return RouteAuthenticationDecision(
            policy_id=policy_id,
            decision="allow",
            reason=reason,
            allowed=True,
            route_label=route_label,
            principal_kind=principal.kind,
        )

    @staticmethod
    def _deny(
        policy_id: str,
        reason: str,
        route_label: str,
        principal: RequestPrincipal,
    ) -> RouteAuthenticationDecision:
        return RouteAuthenticationDecision(
            policy_id=policy_id,
            decision="deny",
            reason=reason,
            allowed=False,
            route_label=route_label,
            principal_kind=principal.kind,
        )


class RouteAuthenticationConfigurationError(RuntimeError):
    pass


def resolve_route_authentication_mode(environment: str, configured_mode: str) -> str:
    normalized_environment = str(environment or "").strip().lower()
    normalized_mode = str(configured_mode or "").strip().lower()
    if normalized_mode in {"shadow", "enforce"}:
        return normalized_mode
    if normalized_mode in {"", "auto"}:
        return "enforce" if normalized_environment in PRODUCTION_ENVIRONMENTS else "shadow"
    raise RouteAuthenticationConfigurationError(
        f"unsupported route authentication mode: {normalized_mode}"
    )


def _fastapi_business_route_keys(application: Any) -> set[tuple[str, str]]:
    excluded = {"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}
    result: set[tuple[str, str]] = set()
    for route in application.routes:
        path = str(getattr(route, "path", ""))
        if path in excluded:
            continue
        for method in getattr(route, "methods", set()) or set():
            result.add((str(method).upper(), path))
    return result


def validate_route_authentication_startup(
    application: Any,
    *,
    registry: Any,
    environment: str,
    enforcement_mode: str,
    machine_credential_configured: bool = True,
) -> Dict[str, Any]:
    application_routes = _fastapi_business_route_keys(application)
    registry_routes = route_keys(registry.rules)
    if len(registry.rules) != len(registry_routes):
        raise RouteAuthenticationConfigurationError("route registry contains duplicate entries")
    missing = sorted(application_routes - registry_routes)
    stale = sorted(registry_routes - application_routes)
    if missing or stale:
        raise RouteAuthenticationConfigurationError(
            f"route authentication registry mismatch: missing={missing} stale={stale}"
        )
    for rule in registry.rules:
        if rule.auth_mode == RouteAuthenticationMode.PUBLIC:
            if rule.required_audience is not None or rule.required_scopes:
                raise RouteAuthenticationConfigurationError(
                    f"public route carries credential requirements: {rule.method} {rule.path_template}"
                )
        elif not rule.required_audience or not rule.required_scopes:
            raise RouteAuthenticationConfigurationError(
                f"protected route lacks audience/scope: {rule.method} {rule.path_template}"
            )
    resolved_mode = resolve_route_authentication_mode(environment, enforcement_mode)
    if str(environment or "").strip().lower() in PRODUCTION_ENVIRONMENTS and resolved_mode != "enforce":
        raise RouteAuthenticationConfigurationError(
            "production route authentication must run in enforce mode"
        )
    if (
        str(environment or "").strip().lower() in PRODUCTION_ENVIRONMENTS
        and not machine_credential_configured
    ):
        raise RouteAuthenticationConfigurationError(
            "production machine credential is required"
        )
    return {
        "routeCount": len(application_routes),
        "unclassifiedCount": 0,
        "enforcementMode": resolved_mode,
    }
