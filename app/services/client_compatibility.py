from __future__ import annotations

import re
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Deque, Dict, Optional

from app.services.route_ownership import (
    RouteAuthenticationMode,
    RouteOwnershipRegistry,
)


READ_ONLY_METHODS = frozenset({"GET", "HEAD"})
SAFETY_CLEANUP_ROUTES = frozenset({("POST", "/auth/logout")})
MAX_CLIENT_BUILD = 2_147_483_647


class ClientCompatibilityConfigurationError(RuntimeError):
    pass


def resolve_client_compatibility_mode(configured_mode: str) -> str:
    normalized = str(configured_mode or "").strip().lower()
    if normalized in {"", "observe"}:
        return "observe"
    if normalized == "enforce":
        return "enforce"
    raise ClientCompatibilityConfigurationError(
        f"unsupported client compatibility mode: {normalized}"
    )


@dataclass(frozen=True)
class ClientCompatibilityDecision:
    mode: str
    decision: str
    reason: str
    blocked: bool
    compat_route: bool
    route_label: str
    build_status: str
    client_build: Optional[int]
    minimum_client_build: int

    def header_values(self) -> Dict[str, str]:
        return {
            "mode": self.mode,
            "decision": self.decision,
            "reason": self.reason,
            "minimumClientBuild": str(self.minimum_client_build),
        }


class ClientCompatibilityPolicy:
    """Build fence for registry-classified user routes, independent of features."""

    def __init__(
        self,
        *,
        registry: Optional[RouteOwnershipRegistry] = None,
        minimum_client_build: int = 1,
        mode: str = "observe",
    ) -> None:
        self.registry = registry or RouteOwnershipRegistry()
        self.minimum_client_build = max(1, int(minimum_client_build))
        self.mode = resolve_client_compatibility_mode(mode)

    def evaluate(
        self,
        *,
        method: str,
        path: str,
        client_build_header: Optional[str],
    ) -> ClientCompatibilityDecision:
        normalized_method = str(method or "").upper()
        registry_method = "GET" if normalized_method == "HEAD" else normalized_method
        route_match = self.registry.match(registry_method, path)
        if (
            route_match is None
            or route_match.rule.auth_mode != RouteAuthenticationMode.USER
        ):
            return ClientCompatibilityDecision(
                mode=self.mode,
                decision="notApplicable",
                reason="routeNotUser",
                blocked=False,
                compat_route=False,
                route_label=f"{normalized_method} {path}",
                build_status="notEvaluated",
                client_build=None,
                minimum_client_build=self.minimum_client_build,
            )

        route_label = f"{normalized_method} {route_match.rule.policy_id}"
        build_status, client_build = self._parse_client_build(client_build_header)
        if normalized_method in READ_ONLY_METHODS:
            return self._allow(
                reason="readOnlyMethod",
                route_label=route_label,
                build_status=build_status,
                client_build=client_build,
            )
        if (normalized_method, route_match.rule.path_template) in SAFETY_CLEANUP_ROUTES:
            return self._allow(
                reason="safetyCleanupExempt",
                route_label=route_label,
                build_status=build_status,
                client_build=client_build,
            )
        if build_status == "supported":
            return self._allow(
                reason="clientBuildSupported",
                route_label=route_label,
                build_status=build_status,
                client_build=client_build,
            )

        reason = {
            "missing": "missingClientBuild",
            "invalid": "invalidClientBuild",
            "belowMinimum": "clientBelowMinimum",
        }[build_status]
        blocked = self.mode == "enforce"
        return ClientCompatibilityDecision(
            mode=self.mode,
            decision="deny" if blocked else "observeDeny",
            reason=reason,
            blocked=blocked,
            compat_route=True,
            route_label=route_label,
            build_status=build_status,
            client_build=client_build,
            minimum_client_build=self.minimum_client_build,
        )

    def evaluate_legacy_identity_retirement(
        self,
        *,
        method: str,
        path: str,
        client_build_header: Optional[str],
    ) -> ClientCompatibilityDecision:
        build_status, client_build = self._parse_client_build(client_build_header)
        route_id = {
            "/auth/login": "legacyLogin",
            "/auth/restore": "legacyRestore",
        }.get(path, "legacyIdentity")
        return ClientCompatibilityDecision(
            mode="enforce",
            decision="deny",
            reason="legacyIdentityFlowRetired",
            blocked=True,
            compat_route=True,
            route_label=f"{str(method or '').upper()} {route_id}",
            build_status=build_status,
            client_build=client_build,
            minimum_client_build=self.minimum_client_build,
        )

    def _allow(
        self,
        *,
        reason: str,
        route_label: str,
        build_status: str,
        client_build: Optional[int],
    ) -> ClientCompatibilityDecision:
        return ClientCompatibilityDecision(
            mode=self.mode,
            decision="allow",
            reason=reason,
            blocked=False,
            compat_route=True,
            route_label=route_label,
            build_status=build_status,
            client_build=client_build,
            minimum_client_build=self.minimum_client_build,
        )

    def _parse_client_build(
        self,
        raw_value: Optional[str],
    ) -> tuple[str, Optional[int]]:
        value = str(raw_value or "").strip()
        if not value:
            return "missing", None
        if len(value) > 10 or re.fullmatch(r"[0-9]+", value) is None:
            return "invalid", None
        parsed = int(value)
        if parsed < 1 or parsed > MAX_CLIENT_BUILD:
            return "invalid", None
        if parsed < self.minimum_client_build:
            return "belowMinimum", parsed
        return "supported", parsed


@dataclass(frozen=True)
class ClientCompatibilityObservation:
    decision: str
    reason: str
    route_label: str
    build_status: str
    client_build: Optional[int]
    occurred_at: datetime


class ClientCompatibilityDecisionRecorder:
    """Bounded compatibility metrics without identity, token, or raw headers."""

    def __init__(self, *, max_events: int = 1000) -> None:
        self._events: Deque[ClientCompatibilityObservation] = deque(
            maxlen=max(1, max_events)
        )
        self._upgrade_required_426_count = 0
        self._lock = Lock()

    def record(
        self,
        decision: ClientCompatibilityDecision,
        *,
        occurred_at: Optional[datetime] = None,
    ) -> None:
        if not decision.compat_route:
            return
        instant = occurred_at or datetime.now(timezone.utc)
        if instant.tzinfo is None or instant.utcoffset() is None:
            instant = instant.replace(tzinfo=timezone.utc)
        observation = ClientCompatibilityObservation(
            decision=decision.decision,
            reason=decision.reason,
            route_label=decision.route_label,
            build_status=decision.build_status,
            client_build=decision.client_build,
            occurred_at=instant.astimezone(timezone.utc),
        )
        with self._lock:
            self._events.append(observation)

    def record_upgrade_required_response(self) -> None:
        with self._lock:
            self._upgrade_required_426_count += 1

    def summary(
        self,
        *,
        mode: str,
        minimum_client_build: int,
    ) -> Dict[str, Any]:
        with self._lock:
            events = list(self._events)
            upgrade_required_426_count = self._upgrade_required_426_count
        client_build_counts = Counter(
            str(item.client_build)
            for item in events
            if item.client_build is not None
        )
        build_status_counts = Counter(item.build_status for item in events)
        return {
            "schemaVersion": 1,
            "mode": resolve_client_compatibility_mode(mode),
            "minimumClientBuild": max(1, int(minimum_client_build)),
            "eventCount": len(events),
            "clientBuildCounts": dict(sorted(client_build_counts.items())),
            "belowMinCount": build_status_counts.get("belowMinimum", 0),
            "missingBuildCount": build_status_counts.get("missing", 0),
            "invalidBuildCount": build_status_counts.get("invalid", 0),
            "compatRouteCounts": dict(
                sorted(Counter(item.route_label for item in events).items())
            ),
            "decisionCounts": dict(
                sorted(Counter(item.decision for item in events).items())
            ),
            "reasonCounts": dict(
                sorted(Counter(item.reason for item in events).items())
            ),
            "upgradeRequired426Count": upgrade_required_426_count,
            "windowStartedAt": events[0].occurred_at.isoformat() if events else None,
            "windowEndedAt": events[-1].occurred_at.isoformat() if events else None,
            "valueFree": True,
        }
