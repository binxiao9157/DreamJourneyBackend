from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Callable, Deque, Iterable, Literal, Mapping, Optional, Set

from pydantic import BaseModel, ConfigDict

from app.observability.events import map_release_policy_operation_event


ReleaseAudience = Literal["owner", "family", "visitor", "qa"]
Gate = Literal["G0", "G1", "G2", "G3", "G4"]
ReleasePolicyCommandMode = Literal["observe", "enforce"]
ReleaseStage = Literal["M0", "M1", "M2", "M3", "M4", "unknown"]


def parse_release_policy_feature_set(value: Optional[str]) -> Set[str]:
    return {
        item.strip()
        for item in (value or "").split(",")
        if item.strip()
    }


def normalize_release_policy_audience(
    requested: str,
    *,
    environment: str,
    principal_kind: str,
) -> ReleaseAudience:
    normalized = requested.strip()
    if normalized == "qa":
        is_production = environment.strip().lower() in {"production", "prod"}
        if not is_production and principal_kind == "system":
            return "qa"
        return "owner"
    if normalized in {"owner", "family", "visitor"}:
        return normalized  # type: ignore[return-value]
    return "owner"


class ReleasePolicyVersionDowngrade(RuntimeError):
    def __init__(self, *, known_revision: int, server_revision: int):
        super().__init__("client knows a newer release policy revision")
        self.known_revision = known_revision
        self.server_revision = server_revision


class ReleasePolicyFeatureAccessDenied(RuntimeError):
    def __init__(self, *, feature: str, reason: str, policy_revision: int):
        super().__init__(f"release policy denied {feature}: {reason}")
        self.feature = feature
        self.reason = reason
        self.policy_revision = policy_revision


class ReleasePolicyDecisionEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    feature: str
    policyVersion: str
    clientBuild: int
    decision: str
    reason: str
    route: str
    occurredAt: datetime


class ReleasePolicyDecisionRecorder:
    """Bounded, value-free rollout evidence until the S0-07 metrics sink lands."""

    RUNTIME_CONTRACT_VERSION = 2

    def __init__(
        self,
        *,
        max_events: int = 500,
        environment: str = "runtime",
        event_sink: Optional[Callable[..., Mapping[str, Any]]] = None,
        event_summary_source: Optional[Callable[[], Mapping[str, Any]]] = None,
        retention_days: int = 30,
    ) -> None:
        self._events: Deque[ReleasePolicyDecisionEvent] = deque(maxlen=max(1, max_events))
        self._environment = environment.strip() or "runtime"
        self._event_sink = event_sink
        self._event_summary_source = event_summary_source
        self._retention_days = max(8, retention_days)
        self._sink_persisted_count = 0
        self._sink_deduplicated_count = 0
        self._sink_failure_count = 0
        self._source_failure_count = 0
        self._lock = Lock()

    def record(
        self,
        *,
        feature: str,
        policy_version: str,
        client_build: int,
        decision: str,
        reason: str,
        route: str,
        occurred_at: Optional[datetime] = None,
    ) -> None:
        instant = occurred_at or datetime.now(timezone.utc)
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=timezone.utc)
        event = ReleasePolicyDecisionEvent(
            feature=feature,
            policyVersion=policy_version,
            clientBuild=max(0, client_build),
            decision=decision,
            reason=reason,
            route=route,
            occurredAt=instant,
        )
        with self._lock:
            self._events.append(event)
        if self._event_sink is None:
            return
        operation_event = map_release_policy_operation_event(
            feature=event.feature,
            policy_version=event.policyVersion,
            client_build=event.clientBuild,
            decision=event.decision,
            reason=event.reason,
            route=event.route,
            occurred_at=event.occurredAt,
            environment=self._environment,
        )
        try:
            receipt = self._event_sink(
                operation_event.model_dump(mode="json"),
                retention_class="rolloutObservation",
                expires_at_iso=(
                    event.occurredAt + timedelta(days=self._retention_days)
                ).isoformat(),
                legal_hold=False,
            )
            with self._lock:
                if receipt.get("outcome") == "deduplicated":
                    self._sink_deduplicated_count += 1
                else:
                    self._sink_persisted_count += 1
        except Exception:
            with self._lock:
                self._sink_failure_count += 1

    def record_runtime_contract(
        self,
        *,
        client_build: int,
        contract_version: int,
        occurred_at: Optional[datetime] = None,
    ) -> None:
        uses_typed_contract = contract_version >= self.RUNTIME_CONTRACT_VERSION
        self.record(
            feature="runtimeConfig",
            policy_version=ReleasePolicyService.POLICY_VERSION,
            client_build=client_build,
            decision="typedRuntimeContract" if uses_typed_contract else "legacyRuntimeAliasObserved",
            reason=(
                "capabilitySnapshotContract"
                if uses_typed_contract
                else "missingOrOldRuntimeContractVersion"
            ),
            route="GET /config/runtime",
            occurred_at=occurred_at,
        )

    def summary(self) -> dict[str, object]:
        with self._lock:
            events = list(self._events)
            sink_persisted_count = self._sink_persisted_count
            sink_deduplicated_count = self._sink_deduplicated_count
            sink_failure_count = self._sink_failure_count
            source_failure_count = self._source_failure_count
        operation_events = [
            map_release_policy_operation_event(
                feature=item.feature,
                policy_version=item.policyVersion,
                client_build=item.clientBuild,
                decision=item.decision,
                reason=item.reason,
                route=item.route,
                occurred_at=item.occurredAt,
                environment=self._environment,
            ).model_dump(mode="json")
            for item in events
        ]
        decisions = Counter(item.decision for item in events)
        features = Counter(item.feature for item in events)
        event_count = len(events)
        window_started_at: object = events[0].occurredAt if events else None
        window_ended_at: object = events[-1].occurredAt if events else None
        evidence_source = "memory"

        if self._event_summary_source is not None:
            try:
                persisted = self._event_summary_source()
                persisted_count = int(persisted.get("eventCount") or 0)
                if persisted_count > 0 or not events:
                    operation_events = list(persisted.get("events") or [])
                    decisions = Counter(
                        {
                            str(key): int(value)
                            for key, value in dict(
                                persisted.get("decisionCounts") or {}
                            ).items()
                        }
                    )
                    features = Counter(
                        {
                            str(key): int(value)
                            for key, value in dict(
                                persisted.get("featureCounts") or {}
                            ).items()
                        }
                    )
                    event_count = persisted_count
                    window_started_at = persisted.get("windowStartedAt")
                    window_ended_at = persisted.get("windowEndedAt")
                    evidence_source = "persistent"
                else:
                    evidence_source = "memoryFallback"
            except Exception:
                evidence_source = "memoryFallback"
                with self._lock:
                    self._source_failure_count += 1
                    source_failure_count = self._source_failure_count

        compatibility_events = [
            self._compatibility_event_payload(item) for item in operation_events
        ]
        return {
            "schemaVersion": 1,
            "eventEnvelopeSchemaVersion": 1,
            "evidenceStoreContractVersion": 1,
            "runtimeContractVersion": self.RUNTIME_CONTRACT_VERSION,
            "eventCount": event_count,
            "legacyRuntimeAliasHitCount": decisions.get("legacyRuntimeAliasObserved", 0),
            "typedRuntimeContractHitCount": decisions.get("typedRuntimeContract", 0),
            "decisionCounts": dict(sorted(decisions.items())),
            "featureCounts": dict(sorted(features.items())),
            "windowStartedAt": window_started_at,
            "windowEndedAt": window_ended_at,
            "events": compatibility_events,
            "operationEvents": operation_events,
            "evidenceSource": evidence_source,
            "sinkPersistedCount": sink_persisted_count,
            "sinkDeduplicatedCount": sink_deduplicated_count,
            "sinkFailureCount": sink_failure_count,
            "sourceFailureCount": source_failure_count,
        }

    @staticmethod
    def _compatibility_event_payload(event: Mapping[str, Any]) -> dict[str, object]:
        return {
            "feature": event.get("feature"),
            "policyVersion": event.get("policyVersion"),
            "clientBuild": event.get("clientBuild"),
            "decision": event.get("decision"),
            "reason": event.get("reason"),
            "route": event.get("route"),
            "occurredAt": event.get("occurredAt"),
        }


@dataclass(frozen=True)
class ReleasePolicyCommandCapture:
    decision_id: str
    feature: str
    policy_version: str
    policy_revision: int
    emergency_revision: int
    account_generation: str
    audience: ReleaseAudience
    cohort: str
    client_build: int
    expires_at: datetime
    server_reason: str
    client_policy_revision: int
    client_allowed: bool


class ReleasePolicyFeatureDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    feature: str
    enabled: bool
    releaseVisible: bool
    audience: ReleaseAudience
    cohort: str
    requiredGates: tuple[Gate, ...]
    releaseStage: ReleaseStage
    reason: str


class ReleasePolicySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: int
    policyVersion: str
    policyRevision: int
    issuedAt: datetime
    expiresAt: datetime
    minClient: int
    emergencyRevision: int
    audience: ReleaseAudience
    cohort: str
    source: Literal["server"]
    shadowMode: bool
    snapshotDecision: str
    features: tuple[ReleasePolicyFeatureDecision, ...]

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        instant = now or datetime.now(timezone.utc)
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=timezone.utc)
        expiry = self.expiresAt
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return instant >= expiry


class ReleasePolicyService:
    """Server-authoritative shadow policy for WI-S0-06-01.

    This contract records exposure decisions but intentionally does not change
    existing routes or iOS UI. Enforcement and cache behavior belong to the
    subsequent ReleasePolicy work items.
    """

    SCHEMA_VERSION = 1
    POLICY_VERSION = "release-policy-v1"
    DEFAULT_TTL_SECONDS = 300

    _FEATURE_GATES: dict[str, tuple[Gate, ...]] = {
        "echoTextInput": ("G0", "G1"),
        "echoImageInput": ("G0", "G1", "G2"),
        "timeLetters": ("G0", "G1", "G2", "G4"),
        "profileSettings": ("G0", "G1"),
        "personaSettings": ("G0", "G1", "G4"),
        "archiveAudioUpload": ("G0", "G1", "G2", "G3", "G4"),
        "archiveVideoUpload": ("G0", "G1", "G2", "G3", "G4"),
        "archiveRemoteFetch": ("G0", "G1", "G2"),
        "archiveLocalAnalysis": ("G0", "G1", "G4"),
        "familyManagement": ("G0", "G1", "G2", "G4"),
        "familySpace": ("G0", "G1", "G2", "G4"),
        "legalCenter": ("G0", "G1"),
        "accountDeletion": ("G0", "G1", "G2"),
        "accountPasswordChange": ("G0", "G1", "G2"),
        "careDashboard": ("G0", "G1", "G2", "G4"),
        "careDoctorContact": ("G0", "G1", "G2", "G4"),
        "voiceCloneShell": ("G0", "G1", "G2", "G3", "G4"),
        "digitalHumanLivePanel": ("G0", "G1", "G2", "G3", "G4"),
        "digitalInheritance": ("G0", "G1", "G2", "G3", "G4"),
        "knowledgeLicensing": ("G0", "G1", "G2", "G3", "G4"),
        "beneficiarySettlement": ("G0", "G1", "G2", "G3", "G4"),
    }
    _FEATURE_STAGES: dict[str, ReleaseStage] = {
        **{feature: "M0" for feature in _FEATURE_GATES},
        "voiceCloneShell": "M1",
        "personaSettings": "M2",
        "digitalHumanLivePanel": "M2",
        "familySpace": "M2",
        "careDashboard": "M3",
        "careDoctorContact": "M3",
        "digitalInheritance": "M4",
        "knowledgeLicensing": "M4",
        "beneficiarySettlement": "M4",
    }
    _CLOSED_PILOT_OWNER_VISIBLE = {
        "echoTextInput",
        "profileSettings",
        "legalCenter",
        "accountDeletion",
    }

    def __init__(
        self,
        *,
        policy_revision: int = 1,
        min_client_build: int = 1,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        emergency_revision: int = 0,
        emergency_disabled_features: Optional[Iterable[str]] = None,
        enforced_features: Optional[Iterable[str]] = None,
        shadow_mode: bool = True,
        enforce_default_closed_stages: bool = True,
    ) -> None:
        self.policy_revision = max(1, policy_revision)
        self.min_client_build = max(1, min_client_build)
        self.ttl_seconds = max(60, ttl_seconds)
        self.emergency_revision = max(0, emergency_revision)
        self.emergency_disabled_features: Set[str] = set(emergency_disabled_features or ())
        self.enforced_features: Set[str] = set(enforced_features or ())
        unknown_rollout_features = (
            self.emergency_disabled_features | self.enforced_features
        ).difference(self._FEATURE_GATES)
        if unknown_rollout_features:
            raise ValueError(
                "unknown release policy rollout feature(s): "
                + ", ".join(sorted(unknown_rollout_features))
            )
        self.shadow_mode = shadow_mode
        self.enforce_default_closed_stages = enforce_default_closed_stages

    def command_mode_for(self, feature: str) -> ReleasePolicyCommandMode:
        if feature in self.emergency_disabled_features:
            return "enforce"
        if (
            self.enforce_default_closed_stages
            and self.release_stage_for(feature) in {"M1", "M2", "M3", "M4"}
        ):
            return "enforce"
        if not self.shadow_mode or feature in self.enforced_features:
            return "enforce"
        return "observe"

    @classmethod
    def release_stage_for(cls, feature: str) -> ReleaseStage:
        return cls._FEATURE_STAGES.get(feature, "unknown")

    def minimum_client_access_mode(self, feature: str) -> str:
        return "readOnly" if feature in self._CLOSED_PILOT_OWNER_VISIBLE else "deny"

    def public_descriptor(self) -> dict[str, object]:
        return {
            "endpoint": "/v2/release-policy",
            "schemaVersion": self.SCHEMA_VERSION,
            "policyVersion": self.POLICY_VERSION,
            "policyRevision": self.policy_revision,
            "ttlSeconds": self.ttl_seconds,
            "minClient": self.min_client_build,
            "emergencyRevision": self.emergency_revision,
            "source": "server",
            "shadowMode": self.shadow_mode,
            "commandMode": self._descriptor_command_mode(),
            "rolloutContractVersion": 1,
            "runtimeContractVersion": ReleasePolicyDecisionRecorder.RUNTIME_CONTRACT_VERSION,
            "canaryFeatures": sorted(self.enforced_features),
            "killSwitchFeatures": sorted(self.emergency_disabled_features),
            "defaultClosedStages": ["M1", "M2", "M3", "M4"],
            "defaultClosedStageEffectsEnforced": self.enforce_default_closed_stages,
        }

    def _descriptor_command_mode(self) -> str:
        if not self.shadow_mode:
            return "enforce"
        if self.enforced_features or self.emergency_disabled_features:
            return "mixed"
        return "observe"

    def build_snapshot(
        self,
        *,
        audience: ReleaseAudience,
        cohort: str,
        client_build: int,
        known_policy_revision: int = 0,
        requested_feature: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> ReleasePolicySnapshot:
        if known_policy_revision > self.policy_revision:
            raise ReleasePolicyVersionDowngrade(
                known_revision=known_policy_revision,
                server_revision=self.policy_revision,
            )

        issued_at = now or datetime.now(timezone.utc)
        if issued_at.tzinfo is None:
            issued_at = issued_at.replace(tzinfo=timezone.utc)
        normalized_cohort = cohort.strip() or "unassigned"
        normalized_client_build = max(0, client_build)
        client_below_minimum = normalized_client_build < self.min_client_build
        feature_names = (
            (requested_feature.strip() or "unknown") if requested_feature is not None else None
        )
        selected_features = [feature_names] if feature_names is not None else list(self._FEATURE_GATES)
        decisions = tuple(
            self._decision(
                feature=feature,
                audience=audience,
                cohort=normalized_cohort,
                client_below_minimum=client_below_minimum,
            )
            for feature in selected_features
        )
        return ReleasePolicySnapshot(
            schemaVersion=self.SCHEMA_VERSION,
            policyVersion=self.POLICY_VERSION,
            policyRevision=self.policy_revision,
            issuedAt=issued_at,
            expiresAt=issued_at + timedelta(seconds=self.ttl_seconds),
            minClient=self.min_client_build,
            emergencyRevision=self.emergency_revision,
            audience=audience,
            cohort=normalized_cohort,
            source="server",
            shadowMode=self.shadow_mode,
            snapshotDecision=(
                "clientBelowMinimum"
                if client_below_minimum
                else ("shadowAllowlist" if self.shadow_mode else "enforcedAllowlist")
            ),
            features=decisions,
        )

    def _decision(
        self,
        *,
        feature: str,
        audience: ReleaseAudience,
        cohort: str,
        client_below_minimum: bool,
    ) -> ReleasePolicyFeatureDecision:
        required_gates = self._FEATURE_GATES.get(feature)
        if required_gates is None:
            return ReleasePolicyFeatureDecision(
                feature=feature,
                enabled=False,
                releaseVisible=False,
                audience=audience,
                cohort=cohort,
                requiredGates=("G0",),
                releaseStage="unknown",
                reason="unknownFeature",
            )
        if feature in self.emergency_disabled_features:
            reason = "emergencyRevoked"
            allowed = False
        elif client_below_minimum:
            reason = "clientBelowMinimum"
            allowed = False
        elif (
            audience == "owner"
            and cohort == "closedPilotAdultSelf"
            and feature in self._CLOSED_PILOT_OWNER_VISIBLE
        ):
            reason = "closedPilotOwnerCore"
            allowed = True
        else:
            reason = "notApprovedForClosedPilot"
            allowed = False
        return ReleasePolicyFeatureDecision(
            feature=feature,
            enabled=allowed,
            releaseVisible=allowed,
            audience=audience,
            cohort=cohort,
            requiredGates=required_gates,
            releaseStage=self.release_stage_for(feature),
            reason=reason,
        )


class ReleasePolicyCommandGate:
    """Captures client policy metadata but always re-evaluates server authority."""

    _PREFIX_FEATURES: tuple[tuple[str, str], ...] = (
        ("/digital-human/", "digitalHumanLivePanel"),
        ("/voice/", "voiceCloneShell"),
        ("/tts", "voiceCloneShell"),
        ("/family/", "familyManagement"),
        ("/care/", "careDashboard"),
        ("/mailbox/letters", "timeLetters"),
        ("/archive/time-letters/", "timeLetters"),
        ("/archive/image-analysis", "archiveLocalAnalysis"),
        ("/archive/photos", "archiveRemoteFetch"),
        ("/profile", "profileSettings"),
        ("/context/build", "echoTextInput"),
        ("/echo/delayed-replies", "echoTextInput"),
        ("/auth/delete", "accountDeletion"),
        ("/auth/restore", "accountDeletion"),
        ("/auth/purge-expired-deletions", "accountDeletion"),
        ("/auth/password", "accountPasswordChange"),
    )

    def __init__(self, policy_service: ReleasePolicyService):
        self.policy_service = policy_service

    def capture(
        self,
        *,
        feature: str,
        audience: ReleaseAudience,
        cohort: str,
        client_build: int,
        client_policy_version: Optional[str],
        client_policy_revision: Optional[int],
        client_account_generation: Optional[str],
        client_allowed: Optional[bool],
        client_decision_id: Optional[str] = None,
        client_feature: Optional[str] = None,
        expected_account_generation: Optional[str] = None,
        require_client_capture: bool = True,
        now: Optional[datetime] = None,
    ) -> ReleasePolicyCommandCapture:
        snapshot = self.policy_service.build_snapshot(
            audience=audience,
            cohort=cohort,
            client_build=client_build,
            requested_feature=feature,
            now=now,
        )
        decision = snapshot.features[0]
        if not decision.enabled:
            self._deny(decision.feature, decision.reason, snapshot.policyRevision)

        normalized_version = (client_policy_version or "").strip()
        normalized_generation = (client_account_generation or "").strip()
        normalized_decision_id = (client_decision_id or "").strip()
        normalized_client_feature = (client_feature or "").strip()
        if normalized_client_feature and normalized_client_feature != feature:
            self._deny(feature, "featureMetadataMismatch", snapshot.policyRevision)
        if require_client_capture:
            if (
                not normalized_version
                or not normalized_decision_id
                or client_policy_revision is None
                or not normalized_generation
                or client_allowed is not True
            ):
                self._deny(feature, "missingCapturedPolicy", snapshot.policyRevision)
            if normalized_version != snapshot.policyVersion:
                self._deny(feature, "policyVersionMismatch", snapshot.policyRevision)
            if client_policy_revision > snapshot.policyRevision:
                self._deny(feature, "policyRevisionAheadOfServer", snapshot.policyRevision)
            normalized_expected_generation = (expected_account_generation or "").strip()
            if (
                normalized_expected_generation
                and normalized_generation != normalized_expected_generation
            ):
                self._deny(feature, "accountGenerationMismatch", snapshot.policyRevision)
        else:
            normalized_decision_id = (
                f"server:{snapshot.policyVersion}:{snapshot.policyRevision}:{feature}"
            )
            normalized_version = snapshot.policyVersion
            normalized_generation = (expected_account_generation or "system").strip() or "system"
            client_policy_revision = snapshot.policyRevision

        return ReleasePolicyCommandCapture(
            decision_id=normalized_decision_id,
            feature=feature,
            policy_version=snapshot.policyVersion,
            policy_revision=snapshot.policyRevision,
            emergency_revision=snapshot.emergencyRevision,
            account_generation=normalized_generation,
            audience=audience,
            cohort=snapshot.cohort,
            client_build=max(1, client_build),
            expires_at=snapshot.expiresAt,
            server_reason=decision.reason,
            client_policy_revision=client_policy_revision,
            client_allowed=True,
        )

    def revalidate_effect(
        self,
        captured: ReleasePolicyCommandCapture,
        *,
        policy_service: Optional[ReleasePolicyService] = None,
        now: Optional[datetime] = None,
    ) -> ReleasePolicyCommandCapture:
        current_service = policy_service or self.policy_service
        effective_now = now or datetime.now(timezone.utc)
        if captured.expires_at <= effective_now:
            self._deny(
                captured.feature,
                "capturedPolicyExpiredBeforeEffect",
                current_service.policy_revision,
            )
        snapshot = current_service.build_snapshot(
            audience=captured.audience,
            cohort=captured.cohort,
            client_build=captured.client_build,
            requested_feature=captured.feature,
            now=effective_now,
        )
        decision = snapshot.features[0]
        if snapshot.is_expired(effective_now):
            self._deny(captured.feature, "policyExpiredBeforeEffect", snapshot.policyRevision)
        if snapshot.policyVersion != captured.policy_version:
            self._deny(captured.feature, "policyVersionChanged", snapshot.policyRevision)
        if not decision.enabled:
            self._deny(captured.feature, decision.reason, snapshot.policyRevision)
        return captured

    def feature_for_request(
        self,
        method: str,
        path: str,
        payload: Optional[Mapping[str, Any]],
    ) -> Optional[str]:
        normalized_path = (path or "").split("?", 1)[0]
        for prefix, feature in self._PREFIX_FEATURES:
            if normalized_path == prefix:
                return feature
            if prefix.endswith("/") and normalized_path.startswith(prefix):
                return feature
            if not prefix.endswith("/") and normalized_path.startswith(f"{prefix}/"):
                return feature

        body = payload or {}
        if normalized_path == "/archive/media/upload-intent":
            return self._archive_media_feature(body)
        if normalized_path == "/archive/items" and method.upper() == "POST":
            return self._archive_item_feature(body)
        if method.upper() == "GET" and normalized_path.startswith("/archive/items/"):
            return "archiveRemoteFetch"
        return None

    def route_label_for_request(
        self,
        method: str,
        path: str,
        payload: Optional[Mapping[str, Any]],
    ) -> str:
        normalized_method = method.upper()
        normalized_path = (path or "").split("?", 1)[0]
        for prefix, _ in self._PREFIX_FEATURES:
            if normalized_path == prefix:
                return f"{normalized_method} {prefix}"
            if prefix.endswith("/") and normalized_path.startswith(prefix):
                return f"{normalized_method} {prefix}*"
            if not prefix.endswith("/") and normalized_path.startswith(f"{prefix}/"):
                return f"{normalized_method} {prefix}/*"
        if normalized_path in {"/archive/media/upload-intent", "/archive/items"}:
            return f"{normalized_method} {normalized_path}"
        if normalized_method == "GET" and normalized_path.startswith("/archive/items/"):
            return "GET /archive/items/*"
        feature = self.feature_for_request(method, path, payload)
        return f"{normalized_method} /feature/{feature or 'notApplicable'}"

    @staticmethod
    def _archive_media_feature(payload: Mapping[str, Any]) -> str:
        kind = str(
            payload.get("mediaType")
            or payload.get("kind")
            or payload.get("assetKind")
            or ""
        ).strip().lower()
        if kind in {"audio", "voice", "recording"}:
            return "archiveAudioUpload"
        if kind in {"video", "movie"}:
            return "archiveVideoUpload"
        return "archiveRemoteFetch"

    @staticmethod
    def _archive_item_feature(payload: Mapping[str, Any]) -> Optional[str]:
        metadata = payload.get("metadata")
        nested = metadata if isinstance(metadata, Mapping) else {}
        kind = str(
            payload.get("kind")
            or payload.get("type")
            or payload.get("assetKind")
            or nested.get("kind")
            or nested.get("assetKind")
            or ""
        ).strip().lower()
        if kind in {"timeletter", "time_letter", "letter"}:
            return "timeLetters"
        if kind in {"audio", "voice", "recording"}:
            return "archiveAudioUpload"
        if kind in {"video", "movie"}:
            return "archiveVideoUpload"
        return None

    @staticmethod
    def _deny(feature: str, reason: str, policy_revision: int) -> None:
        raise ReleasePolicyFeatureAccessDenied(
            feature=feature,
            reason=reason,
            policy_revision=policy_revision,
        )
