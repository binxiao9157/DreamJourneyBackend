from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Callable, Deque, Iterable, Literal, Mapping, Optional, Set

from pydantic import BaseModel, ConfigDict, field_validator

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


def parse_release_policy_subject_id_set(value: Optional[str]) -> Set[str]:
    """Parse a server-owned subject allowlist without accepting blank values."""

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
        if not is_production and principal_kind in {"machine", "system"}:
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
    requiredCapability: Optional[str] = None
    capabilityReady: bool = True


class PublicationDefaultClosedPolicy(BaseModel):
    """The non-promotable publication half of the release policy contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: Literal[False] = False
    livingPublisherRequired: Literal[True] = True
    minorHardDeny: Literal[True] = True
    allowedContent: tuple[str, ...] = ()
    thirdPartyContentMode: Literal["deny"] = "deny"
    ownerQuestionBodyVisibility: Literal["deny"] = "deny"
    aiDisclosureMode: Literal["required"] = "required"
    withdrawalMode: Literal["requiredBeforeEnable"] = "requiredBeforeEnable"
    decisionReceiptMode: Literal["requiredBeforeEnable"] = "requiredBeforeEnable"
    safetyAssessmentMode: Literal["requiredBeforeEnable"] = "requiredBeforeEnable"
    algorithmFilingMode: Literal["requiredBeforeEnable"] = "requiredBeforeEnable"

    @field_validator("allowedContent")
    @classmethod
    def allowed_content_must_remain_empty(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value:
            raise ValueError("G0 publication policy must not allow any content type")
        return value


class VisitorDefaultClosedPolicy(BaseModel):
    """The non-promotable visitor half of the release policy contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: Literal[False] = False
    adultVisitorRequired: Literal[True] = True
    minorHardDeny: Literal[True] = True
    visitorIdentityMode: Literal["required"] = "required"
    sessionTTLSeconds: Literal[604800] = 7 * 24 * 60 * 60
    offlineAccessMode: Literal["deny"] = "deny"
    emergencyContactMode: Literal["required"] = "required"
    continuousUseLimitSeconds: Literal[7200] = 2 * 60 * 60
    dependencyReminderAfterSeconds: Literal[7200] = 2 * 60 * 60
    exitMode: Literal["deterministic"] = "deterministic"
    reportingMode: Literal["required"] = "required"
    forwardingMode: Literal["deny"] = "deny"


class PublicationVisitorReleasePolicy(BaseModel):
    """Versioned policy metadata; it cannot enable publication or visitors."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal[1] = 1
    policyVersion: Literal["publication-visitor-policy-v1"] = (
        "publication-visitor-policy-v1"
    )
    policyRevision: int
    effectiveAt: datetime
    status: Literal["externalBlocked"] = "externalBlocked"
    requiredApprovers: tuple[
        Literal["product", "privacy", "legal", "security", "operations"], ...
    ] = ("product", "privacy", "legal", "security", "operations")
    publication: PublicationDefaultClosedPolicy = PublicationDefaultClosedPolicy()
    visitor: VisitorDefaultClosedPolicy = VisitorDefaultClosedPolicy()


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
    publicationVisitorPolicy: PublicationVisitorReleasePolicy
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
    PUBLICATION_VISITOR_POLICY_EFFECTIVE_AT = datetime(
        2026,
        7,
        23,
        tzinfo=timezone.utc,
    )
    FEATURE_ALIAS_SUNSET_AT = datetime(2026, 11, 30, tzinfo=timezone.utc)
    _PUBLICATION_FEATURES = {
        "publication",
        "publicationGrantManagement",
        "publicationVisitor",
    }
    _FEATURE_ALIASES: dict[str, tuple[str, ...]] = {
        "publicationManagementM2": ("publication",),
        "publicationGrantManagementM2": ("publicationGrantManagement",),
        "publicationVisitorM2": ("publicationVisitor",),
        # The legacy name covered two different principals. Runtime requests
        # disambiguate it by audience; server configuration expands it to both.
        "visitorAccess": ("publicationGrantManagement", "publicationVisitor"),
    }

    _FEATURE_GATES: dict[str, tuple[Gate, ...]] = {
        "echoTextInput": ("G0", "G1"),
        # Owner-authored text capture is an independent write capability. It
        # must not inherit Echo visibility, because its durable Source/outbox
        # write has a different release and recovery boundary.
        "ownerTextCaptureV1": ("G0", "G1"),
        # Stage 2 media capture writes only a private SourceObject and requires
        # independent integrity/safety/worker evidence. It does not inherit
        # text capture visibility or expose a public media browser.
        "ownerMediaCaptureV1": ("G0", "G1", "G2"),
        "ownerMediaProcessingV1": ("G0", "G1", "G2"),
        "ownerTruthCandidateReview": ("G0", "G1", "G2"),
        # This is a read-only, display-safe presentation of the server-owned
        # M0-B selector. It remains default closed until its product Gate is
        # explicitly approved; it must not inherit echoTextInput visibility.
        "echoGuidedRecommendations": ("G0", "G1", "G2"),
        # The life map is a separate read-only M0-B surface. It intentionally
        # has no visibility inheritance from natural input or recommendations.
        "ownerTruthLifeMap": ("G0", "G1", "G2"),
        # Owner-only recall search uses the confirmed-memory projection but
        # remains separately default closed until its result-display boundary
        # has G0/G1/G2 evidence. It does not inherit life-map visibility.
        "ownerTruthMemorySearch": ("G0", "G1", "G2"),
        # The interview ending summary is a separate Owner-only read surface.
        # It reports only durable counts and continuation availability; it must
        # not inherit visibility from the QA outcome read or natural input.
        "ownerTruthInterviewOutcome": ("G0", "G1", "G2"),
        "echoImageInput": ("G0", "G1", "G2"),
        "timeLetters": ("G0", "G1", "G2", "G4"),
        "echoDelayedReplies": ("G0", "G1", "G2", "G4"),
        "profileSettings": ("G0", "G1"),
        "personaSettings": ("G0", "G1", "G4"),
        "archiveAudioUpload": ("G0", "G1", "G2", "G3", "G4"),
        "archiveVideoUpload": ("G0", "G1", "G2", "G3", "G4"),
        "archiveRemoteFetch": ("G0", "G1", "G2"),
        "archiveLocalAnalysis": ("G0", "G1", "G4"),
        "familyManagement": ("G0", "G1", "G2", "G4"),
        # A family relationship grants nothing by itself. This separately
        # controlled feature admits only static source contributions under an
        # explicit Owner grant; it never exposes private Vault reads.
        "ownerTruthFamilyContribution": ("G0", "G1", "G2", "G4"),
        "familySpace": ("G0", "G1", "G2", "G4"),
        "legalCenter": ("G0", "G1"),
        "accountDeletion": ("G0", "G1", "G2"),
        "accountDataExport": ("G0", "G1", "G2"),
        "formalMemoryMarkdownExport": ("G0", "G1", "G2"),
        "kbliteUserSurface": ("G0", "G1", "G2"),
        "accountPasswordChange": ("G0", "G1", "G2"),
        "careDashboard": ("G0", "G1", "G2", "G4"),
        "careDoctorContact": ("G0", "G1", "G2", "G4"),
        "voiceCloneShell": ("G0", "G1", "G2", "G3", "G4"),
        "digitalHumanLivePanel": ("G0", "G1", "G2", "G3", "G4"),
        "publication": ("G0", "G1", "G4"),
        "publicationGrantManagement": ("G0", "G1", "G4"),
        "publicationVisitor": ("G0", "G1", "G4"),
        "digitalInheritance": ("G0", "G1", "G2", "G3", "G4"),
        "knowledgeLicensing": ("G0", "G1", "G2", "G3", "G4"),
        "beneficiarySettlement": ("G0", "G1", "G2", "G3", "G4"),
    }
    _FEATURE_STAGES: dict[str, ReleaseStage] = {
        **{feature: "M0" for feature in _FEATURE_GATES},
        "ownerMediaCaptureV1": "M1",
        "ownerMediaProcessingV1": "M1",
        "voiceCloneShell": "M1",
        "personaSettings": "M2",
        "digitalHumanLivePanel": "M2",
        "familySpace": "M2",
        "publication": "M2",
        "publicationGrantManagement": "M2",
        "publicationVisitor": "M2",
        "careDashboard": "M3",
        "careDoctorContact": "M3",
        "digitalInheritance": "M4",
        "knowledgeLicensing": "M4",
        "beneficiarySettlement": "M4",
    }
    # Compatibility-only stage metadata remains in responses for older
    # clients. Authorization uses this explicit stable-feature set instead.
    _DEFAULT_ENFORCED_FEATURES = {
        "ownerMediaCaptureV1",
        "ownerMediaProcessingV1",
        "voiceCloneShell",
        "personaSettings",
        "digitalHumanLivePanel",
        "familySpace",
        "publication",
        "publicationGrantManagement",
        "publicationVisitor",
        "careDashboard",
        "careDoctorContact",
        "digitalInheritance",
        "knowledgeLicensing",
        "beneficiarySettlement",
    }
    _CLOSED_PILOT_OWNER_VISIBLE = {
        "echoTextInput",
        "profileSettings",
        "legalCenter",
        "accountDeletion",
    }
    # Family management is a normal signed-in product capability. It remains
    # server-policy controlled for emergency revocation and minimum-client
    # enforcement, but it must not depend on Closed Pilot membership.
    _AUTHENTICATED_OWNER_VISIBLE = {
        "familyManagement",
        "familySpace",
        "careDashboard",
        "formalMemoryMarkdownExport",
    }
    # The private V4 production chain is a normal authenticated-owner
    # capability. It is independent from login allowlists and from the
    # explicit closed-pilot cohort. Provider-backed media steps still fail
    # closed through their capability bindings.
    _AUTHENTICATED_OWNER_V4_FEATURES = {
        "echoTextInput",
        "ownerTextCaptureV1",
        "ownerMediaCaptureV1",
        "ownerMediaProcessingV1",
        "ownerTruthCandidateReview",
        "echoGuidedRecommendations",
        "ownerTruthLifeMap",
        "ownerTruthMemorySearch",
        "ownerTruthInterviewOutcome",
        "ownerTruthFamilyContribution",
        "personaSettings",
        "voiceCloneShell",
        "profileSettings",
        "legalCenter",
        "accountDeletion",
    }
    _CLOSED_PILOT_OPT_IN_FEATURES = {
        "ownerTextCaptureV1",
        "ownerMediaCaptureV1",
        "ownerMediaProcessingV1",
        "ownerTruthCandidateReview",
        # Both M0-B read surfaces have their own typed, value-minimized
        # product routes. Keep them default closed, but allow a server-owned
        # closed-pilot rollout once their release gates are approved.
        "echoGuidedRecommendations",
        "ownerTruthLifeMap",
        "ownerTruthFamilyContribution",
    }
    _FEATURE_CAPABILITIES = {
        "ownerMediaCaptureV1": "ownerTruthMediaStorage",
        "ownerMediaProcessingV1": "ownerTruthMediaProcessing",
        "voiceCloneShell": "voiceCloneShell",
        "digitalHumanLivePanel": "digitalHumanLivePanel",
    }
    _PUBLIC_EVIDENCE_REQUIRED_FEATURES = {
        "ownerMediaCaptureV1",
        "ownerMediaProcessingV1",
    }
    # Product-confirmed exclusions override provider readiness, rollout
    # cohorts, client claims, and default-stage shadow behavior.
    _PRODUCT_CLOSED_FEATURES = {
        "accountDataExport",
        "archiveAudioUpload",
        "archiveVideoUpload",
        "digitalHumanLivePanel",
        "echoDelayedReplies",
        "kbliteUserSurface",
        "timeLetters",
    }

    @classmethod
    def feature_names(cls) -> tuple[str, ...]:
        """Return the stable server-owned feature vocabulary."""

        return tuple(sorted(cls._FEATURE_GATES))

    @classmethod
    def canonical_feature_name(
        cls,
        feature: str,
        *,
        audience: Optional[ReleaseAudience] = None,
    ) -> str:
        normalized = feature.strip()
        aliases = cls._FEATURE_ALIASES.get(normalized)
        if aliases is None:
            return normalized
        if normalized == "visitorAccess":
            return aliases[1] if audience == "visitor" else aliases[0]
        return aliases[0]

    @classmethod
    def _normalize_configured_features(cls, features: Iterable[str]) -> Set[str]:
        normalized: Set[str] = set()
        for feature in features:
            value = feature.strip()
            aliases = cls._FEATURE_ALIASES.get(value)
            if aliases is None:
                if value:
                    normalized.add(value)
            else:
                normalized.update(aliases)
        return normalized

    def __init__(
        self,
        *,
        policy_revision: int = 1,
        min_client_build: int = 1,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        emergency_revision: int = 0,
        emergency_disabled_features: Optional[Iterable[str]] = None,
        enforced_features: Optional[Iterable[str]] = None,
        closed_pilot_enabled_features: Optional[Iterable[str]] = None,
        authenticated_owner_v4_enabled: bool = False,
        capability_resolver: Optional[Callable[[str], bool]] = None,
        public_capability_resolver: Optional[Callable[[str], bool]] = None,
        shadow_mode: bool = True,
        enforce_default_closed_stages: bool = True,
    ) -> None:
        self.policy_revision = max(1, policy_revision)
        self.min_client_build = max(1, min_client_build)
        self.ttl_seconds = max(60, ttl_seconds)
        self.emergency_revision = max(0, emergency_revision)
        self.emergency_disabled_features = self._normalize_configured_features(
            emergency_disabled_features or ()
        )
        self.enforced_features = self._normalize_configured_features(enforced_features or ())
        self.closed_pilot_enabled_features = self._normalize_configured_features(
            closed_pilot_enabled_features or ()
        )
        self.authenticated_owner_v4_enabled = bool(authenticated_owner_v4_enabled)
        self.capability_resolver = capability_resolver
        self.public_capability_resolver = public_capability_resolver
        unknown_rollout_features = (
            self.emergency_disabled_features
            | self.enforced_features
            | self.closed_pilot_enabled_features
        ).difference(self._FEATURE_GATES)
        if unknown_rollout_features:
            raise ValueError(
                "unknown release policy rollout feature(s): "
                + ", ".join(sorted(unknown_rollout_features))
            )
        unsupported_closed_pilot_features = self.closed_pilot_enabled_features.difference(
            self._CLOSED_PILOT_OPT_IN_FEATURES
        )
        if unsupported_closed_pilot_features:
            raise ValueError(
                "unsupported closed-pilot feature(s): "
                + ", ".join(sorted(unsupported_closed_pilot_features))
            )
        self.shadow_mode = shadow_mode
        self.enforce_default_closed_stages = enforce_default_closed_stages

    def command_mode_for(self, feature: str) -> ReleasePolicyCommandMode:
        canonical_feature = self.canonical_feature_name(feature)
        if self.is_product_closed(canonical_feature):
            return "enforce"
        if canonical_feature in self.emergency_disabled_features:
            return "enforce"
        if self.requires_default_enforcement(canonical_feature):
            return "enforce"
        if not self.shadow_mode or canonical_feature in self.enforced_features:
            return "enforce"
        return "observe"

    @classmethod
    def release_stage_for(cls, feature: str) -> ReleaseStage:
        return cls._FEATURE_STAGES.get(cls.canonical_feature_name(feature), "unknown")

    def requires_default_enforcement(self, feature: str) -> bool:
        return bool(
            self.enforce_default_closed_stages
            and self.canonical_feature_name(feature) in self._DEFAULT_ENFORCED_FEATURES
        )

    def minimum_client_access_mode(self, feature: str) -> str:
        canonical_feature = self.canonical_feature_name(feature)
        if self.is_product_closed(canonical_feature):
            return "deny"
        owner_visible = (
            self._closed_pilot_owner_visible_features
            | self._authenticated_owner_visible_features
        )
        return "readOnly" if canonical_feature in owner_visible else "deny"

    @classmethod
    def is_product_closed(cls, feature: str) -> bool:
        return cls.canonical_feature_name(feature) in cls._PRODUCT_CLOSED_FEATURES

    @property
    def _closed_pilot_owner_visible_features(self) -> Set[str]:
        return self._CLOSED_PILOT_OWNER_VISIBLE | self.closed_pilot_enabled_features

    @property
    def _authenticated_owner_visible_features(self) -> Set[str]:
        features = set(self._AUTHENTICATED_OWNER_VISIBLE)
        if self.authenticated_owner_v4_enabled:
            features.update(self._AUTHENTICATED_OWNER_V4_FEATURES)
        return features

    def authenticated_owner_feature_enabled(self, feature: str) -> bool:
        """Return the server-owned production decision for a signed-in Owner."""

        decision = self.build_snapshot(
            audience="owner",
            cohort="authenticatedOwner",
            client_build=self.min_client_build,
            requested_feature=feature,
        ).features[0]
        return bool(decision.enabled)

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
            "closedPilotOwnerFeatures": sorted(
                self._closed_pilot_owner_visible_features
            ),
            "authenticatedOwnerFeatures": sorted(
                self._authenticated_owner_visible_features
            ),
            "featureAliases": {
                alias: list(canonical)
                for alias, canonical in sorted(self._FEATURE_ALIASES.items())
            },
            "featureAliasSunsetAt": self.FEATURE_ALIAS_SUNSET_AT.isoformat(),
            "productClosedFeatures": sorted(self._PRODUCT_CLOSED_FEATURES),
            "killSwitchFeatures": sorted(self.emergency_disabled_features),
            "defaultClosedFeatures": sorted(self._DEFAULT_ENFORCED_FEATURES),
            "defaultClosedStages": ["M1", "M2", "M3", "M4"],
            "defaultClosedStageEffectsEnforced": False,
            "defaultClosedFeatureEffectsEnforced": self.enforce_default_closed_stages,
            "capabilityBindings": dict(sorted(self._FEATURE_CAPABILITIES.items())),
            "publicEvidenceRequiredFeatures": sorted(
                self._PUBLIC_EVIDENCE_REQUIRED_FEATURES
            ),
            "publicationVisitorPolicy": self.publication_visitor_policy().model_dump(
                mode="json"
            ),
        }

    def publication_visitor_policy(self) -> PublicationVisitorReleasePolicy:
        """Return fixed Visitor requirements without creating publication authority."""

        return PublicationVisitorReleasePolicy(
            policyRevision=self.policy_revision,
            effectiveAt=self.PUBLICATION_VISITOR_POLICY_EFFECTIVE_AT,
        )

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
            publicationVisitorPolicy=self.publication_visitor_policy(),
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
        canonical_feature = self.canonical_feature_name(feature, audience=audience)
        required_gates = self._FEATURE_GATES.get(canonical_feature)
        required_capability = self._FEATURE_CAPABILITIES.get(canonical_feature)
        operational_capability_ready = self._capability_ready(required_capability)
        public_evidence_required = canonical_feature in self._PUBLIC_EVIDENCE_REQUIRED_FEATURES
        public_capability_ready = (
            self._public_capability_ready(required_capability)
            if public_evidence_required
            else operational_capability_ready
        )
        capability_ready = operational_capability_ready
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
                requiredCapability=None,
                capabilityReady=False,
            )
        if self.is_product_closed(canonical_feature):
            reason = "productClosed"
            allowed = False
        elif canonical_feature in self.emergency_disabled_features:
            reason = "emergencyRevoked"
            allowed = False
        elif client_below_minimum:
            reason = "clientBelowMinimum"
            allowed = False
        elif (
            public_evidence_required
            and audience == "owner"
            and cohort == "closedPilotAdultSelf"
            and canonical_feature in self._closed_pilot_owner_visible_features
        ):
            if required_capability is not None and not operational_capability_ready:
                reason = "capabilityUnavailable"
                allowed = False
            else:
                reason = "closedPilotOwnerCore"
                allowed = True
        elif (
            audience == "owner"
            and cohort in {"authenticatedOwner", "closedPilotAdultSelf"}
            and canonical_feature in self._authenticated_owner_visible_features
        ):
            if required_capability is not None and not operational_capability_ready:
                reason = "capabilityUnavailable"
                allowed = False
            elif public_evidence_required and not public_capability_ready:
                reason = "externalVerificationRequired"
                allowed = False
                capability_ready = False
            else:
                reason = "authenticatedOwnerCore"
                allowed = True
                capability_ready = public_capability_ready
        elif (
            audience == "owner"
            and cohort == "closedPilotAdultSelf"
            and canonical_feature in self._closed_pilot_owner_visible_features
        ):
            if required_capability is not None and not capability_ready:
                reason = "capabilityUnavailable"
                allowed = False
            else:
                reason = "closedPilotOwnerCore"
                allowed = True
        elif canonical_feature in self._PUBLICATION_FEATURES:
            reason = "publicationVisitorNotApproved"
            allowed = False
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
            releaseStage=self.release_stage_for(canonical_feature),
            reason=reason,
            requiredCapability=required_capability,
            capabilityReady=capability_ready,
        )

    def _capability_ready(self, capability: Optional[str]) -> bool:
        if capability is None:
            return True
        if self.capability_resolver is None:
            return False
        try:
            return self.capability_resolver(capability) is True
        except Exception:
            return False

    def _public_capability_ready(self, capability: Optional[str]) -> bool:
        if capability is None:
            return True
        if self.public_capability_resolver is None:
            return False
        try:
            return self.public_capability_resolver(capability) is True
        except Exception:
            return False


class ReleasePolicyCommandGate:
    """Captures client policy metadata but always re-evaluates server authority."""

    _PREFIX_FEATURES: tuple[tuple[str, str], ...] = (
        ("/digital-human/", "digitalHumanLivePanel"),
        ("/voice/realtime-token", "echoTextInput"),
        ("/voice/", "voiceCloneShell"),
        ("/tts", "voiceCloneShell"),
        ("/family/", "familyManagement"),
        ("/care/", "careDashboard"),
        ("/archive/time-letters/", "timeLetters"),
        ("/archive/image-analysis", "archiveLocalAnalysis"),
        ("/archive/photos", "archiveRemoteFetch"),
        ("/profile", "profileSettings"),
        ("/context/build", "echoTextInput"),
        ("/echo/answers", "echoTextInput"),
        ("/echo/delayed-replies", "echoDelayedReplies"),
        ("/auth/delete", "accountDeletion"),
        ("/auth/data-export", "accountDataExport"),
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
        canonical_feature = self.policy_service.canonical_feature_name(
            feature,
            audience=audience,
        )
        snapshot = self.policy_service.build_snapshot(
            audience=audience,
            cohort=cohort,
            client_build=client_build,
            requested_feature=canonical_feature,
            now=now,
        )
        decision = snapshot.features[0]
        if not decision.enabled:
            self._deny(decision.feature, decision.reason, snapshot.policyRevision)

        normalized_version = (client_policy_version or "").strip()
        normalized_generation = (client_account_generation or "").strip()
        normalized_decision_id = (client_decision_id or "").strip()
        normalized_client_feature = (client_feature or "").strip()
        if normalized_client_feature and self.policy_service.canonical_feature_name(
            normalized_client_feature,
            audience=audience,
        ) != canonical_feature:
            self._deny(canonical_feature, "featureMetadataMismatch", snapshot.policyRevision)
        if require_client_capture:
            if (
                not normalized_version
                or not normalized_decision_id
                or client_policy_revision is None
                or not normalized_generation
                or client_allowed is not True
            ):
                self._deny(canonical_feature, "missingCapturedPolicy", snapshot.policyRevision)
            if normalized_version != snapshot.policyVersion:
                self._deny(canonical_feature, "policyVersionMismatch", snapshot.policyRevision)
            if client_policy_revision > snapshot.policyRevision:
                self._deny(canonical_feature, "policyRevisionAheadOfServer", snapshot.policyRevision)
            normalized_expected_generation = (expected_account_generation or "").strip()
            if (
                normalized_expected_generation
                and normalized_generation != normalized_expected_generation
            ):
                self._deny(canonical_feature, "accountGenerationMismatch", snapshot.policyRevision)
        else:
            normalized_decision_id = (
                f"server:{snapshot.policyVersion}:{snapshot.policyRevision}:{canonical_feature}"
            )
            normalized_version = snapshot.policyVersion
            normalized_generation = (expected_account_generation or "system").strip() or "system"
            client_policy_revision = snapshot.policyRevision

        return ReleasePolicyCommandCapture(
            decision_id=normalized_decision_id,
            feature=canonical_feature,
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
        normalized_method = method.upper()
        if (
            normalized_method == "POST"
            and normalized_path.startswith("/digital-human/sessions/")
            and normalized_path.endswith("/release")
        ):
            # Preserve a cleanup path for leases created before the product
            # closure. No new session or heartbeat is allowed.
            return None
        if normalized_method == "GET" and (
            normalized_path.startswith("/archive/time-letters/")
            or normalized_path.startswith("/mailbox/letters/")
            or normalized_path.startswith("/echo/delayed-replies/")
        ):
            # Product closure stops new creation and delivery. Existing
            # metadata remains readable so a closure never strands a user or
            # turns a retained record into an authorization regression.
            return None
        if (
            normalized_method == "POST"
            and normalized_path.startswith("/mailbox/letters/")
            and normalized_path.endswith(("/read", "/archive"))
        ):
            # Read/archive are cleanup operations over retained mailbox rows;
            # neither creates nor delivers a new message.
            return None
        publication_feature = self._publication_formal_feature(normalized_path)
        if publication_feature is not None:
            return publication_feature
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
        if (
            normalized_method == "POST"
            and normalized_path.startswith("/v2/vaults/")
            and normalized_path.endswith("/source-objects/upload-intents")
        ):
            return self._owner_truth_media_feature(body) or "ownerMediaCaptureV1"
        if (
            method.upper() == "POST"
            and normalized_path.startswith("/v2/vaults/")
            and normalized_path.endswith("/processing-retries")
            and "/source-objects/" in normalized_path
        ):
            return "ownerMediaProcessingV1"
        if (
            normalized_path.startswith("/v2/vaults/")
            and "/source-objects" in normalized_path
        ):
            return "ownerMediaCaptureV1"
        if (
            method.upper() in {"GET", "POST"}
            and normalized_path.startswith("/v2/vaults/")
            and normalized_path.endswith("/sources")
        ):
            return "ownerTextCaptureV1"
        memory_segments = tuple(segment for segment in normalized_path.split("/") if segment)
        if (
            len(memory_segments) >= 5
            and memory_segments[:2] == ("v2", "vaults")
            and memory_segments[3:5] == ("memory-exports", "jobs")
        ):
            return "formalMemoryMarkdownExport"
        if (
            len(memory_segments) == 4
            and memory_segments[:2] == ("v2", "vaults")
            and memory_segments[3] == "memories"
            and normalized_method == "GET"
        ) or (
            len(memory_segments) == 5
            and memory_segments[:2] == ("v2", "vaults")
            and memory_segments[3] == "memories"
            and normalized_method == "GET"
        ) or (
            len(memory_segments) == 6
            and memory_segments[:2] == ("v2", "vaults")
            and memory_segments[3] == "memories"
            and memory_segments[5] == "revisions"
            and normalized_method == "POST"
        ):
            return "ownerTruthCandidateReview"
        if (
            method.upper() in {"GET", "POST"}
            and normalized_path.startswith("/v2/vaults/")
            and (
                normalized_path.endswith("/guided-recommendations")
                or normalized_path.endswith("/guided-recommendations/feedback")
                or normalized_path.endswith("/guided-recommendations/activate")
            )
        ):
            return "echoGuidedRecommendations"
        if (
            method.upper() == "GET"
            and normalized_path.startswith("/v2/vaults/")
            and normalized_path.endswith("/life-map")
        ):
            return "ownerTruthLifeMap"
        if (
            method.upper() == "POST"
            and normalized_path.startswith("/v2/vaults/")
            and normalized_path.endswith("/memory-search")
        ):
            return "ownerTruthMemorySearch"
        if (
            method.upper() == "GET"
            and normalized_path.startswith("/v2/vaults/")
            and normalized_path.endswith("/outcome")
        ):
            return "ownerTruthInterviewOutcome"
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
        publication_label = self._publication_formal_route_label(
            normalized_method,
            normalized_path,
        )
        if publication_label is not None:
            return publication_label
        for prefix, _ in self._PREFIX_FEATURES:
            if normalized_path == prefix:
                return f"{normalized_method} {prefix}"
            if prefix.endswith("/") and normalized_path.startswith(prefix):
                return f"{normalized_method} {prefix}*"
            if not prefix.endswith("/") and normalized_path.startswith(f"{prefix}/"):
                return f"{normalized_method} {prefix}/*"
        if normalized_path in {"/archive/media/upload-intent", "/archive/items"}:
            return f"{normalized_method} {normalized_path}"
        if (
            normalized_path.startswith("/v2/vaults/")
            and "/source-objects" in normalized_path
        ):
            suffix = normalized_path.split("/source-objects", 1)[1]
            if suffix.endswith("/upload-intents"):
                return f"{normalized_method} /v2/vaults/*/source-objects/upload-intents"
            if suffix.startswith("/upload-intents/") and suffix.endswith("/content"):
                return f"{normalized_method} /v2/vaults/*/source-objects/upload-intents/*/content"
            if suffix.endswith("/content"):
                return f"{normalized_method} /v2/vaults/*/source-objects/*/content"
            if suffix.endswith("/processing-retries"):
                return f"{normalized_method} /v2/vaults/*/source-objects/*/processing-retries"
            if suffix.endswith("/deletion-retries"):
                return f"{normalized_method} /v2/vaults/*/source-objects/*/deletion-retries"
            if suffix.endswith("/deletions"):
                return f"{normalized_method} /v2/vaults/*/source-objects/*/deletions"
            return f"{normalized_method} /v2/vaults/*/source-objects/*"
        if (
            normalized_method == "POST"
            and normalized_path.startswith("/v2/vaults/")
            and normalized_path.endswith("/sources")
        ):
            return "POST /v2/vaults/*/sources"
        memory_segments = tuple(segment for segment in normalized_path.split("/") if segment)
        if (
            len(memory_segments) >= 5
            and memory_segments[:2] == ("v2", "vaults")
            and memory_segments[3:5] == ("memory-exports", "jobs")
        ):
            suffix = "/*" if len(memory_segments) > 5 else ""
            if len(memory_segments) > 6:
                suffix += f"/{memory_segments[-1]}"
            return f"{normalized_method} /v2/vaults/*/memory-exports/jobs{suffix}"
        if (
            normalized_method == "GET"
            and len(memory_segments) == 4
            and memory_segments[:2] == ("v2", "vaults")
            and memory_segments[3] == "memories"
        ):
            return "GET /v2/vaults/*/memories"
        if (
            normalized_method == "GET"
            and len(memory_segments) == 5
            and memory_segments[:2] == ("v2", "vaults")
            and memory_segments[3] == "memories"
        ):
            return "GET /v2/vaults/*/memories/*"
        if (
            normalized_method == "POST"
            and len(memory_segments) == 6
            and memory_segments[:2] == ("v2", "vaults")
            and memory_segments[3] == "memories"
            and memory_segments[5] == "revisions"
        ):
            return "POST /v2/vaults/*/memories/*/revisions"
        if (
            normalized_method in {"GET", "POST"}
            and normalized_path.startswith("/v2/vaults/")
            and (
                normalized_path.endswith("/guided-recommendations")
                or normalized_path.endswith("/guided-recommendations/feedback")
                or normalized_path.endswith("/guided-recommendations/activate")
            )
        ):
            if normalized_path.endswith("/guided-recommendations/feedback"):
                suffix = "/guided-recommendations/feedback"
            elif normalized_path.endswith("/guided-recommendations/activate"):
                suffix = "/guided-recommendations/activate"
            else:
                suffix = "/guided-recommendations"
            return f"{normalized_method} /v2/vaults/*{suffix}"
        if (
            normalized_method == "GET"
            and normalized_path.startswith("/v2/vaults/")
            and normalized_path.endswith("/life-map")
        ):
            return "GET /v2/vaults/*/life-map"
        if (
            normalized_method == "POST"
            and normalized_path.startswith("/v2/vaults/")
            and normalized_path.endswith("/memory-search")
        ):
            return "POST /v2/vaults/*/memory-search"
        if normalized_method == "GET" and normalized_path.startswith("/archive/items/"):
            return "GET /archive/items/*"
        feature = self.feature_for_request(method, path, payload)
        return f"{normalized_method} /feature/{feature or 'notApplicable'}"

    @staticmethod
    def _publication_formal_feature(path: str) -> Optional[str]:
        segments = tuple(segment for segment in path.split("/") if segment)
        if segments == ("v2", "publication-invitations"):
            return "publicationVisitor"
        if len(segments) == 4 and segments[:2] == ("v2", "publication-grants") and segments[3] == "sessions":
            return "publicationVisitor"
        if (
            len(segments) == 4
            and segments[:2] == ("v2", "publication-sessions")
            and segments[3] in {"projection", "answers"}
        ):
            return "publicationVisitor"
        if len(segments) < 4 or segments[:2] != ("v2", "vaults"):
            return None
        suffix = segments[3:]
        if suffix == ("publication-grants",) or (
            len(suffix) == 3
            and suffix[0] == "publication-grants"
            and suffix[2] == "revoke"
        ):
            return "publicationGrantManagement"
        if suffix == ("publications",) or (
            len(suffix) == 3
            and suffix[0] == "publications"
            and suffix[2] in {"versions", "drafts"}
        ) or (
            len(suffix) == 4
            and suffix[0] == "publication-drafts"
            and suffix[2] == "confirm"
        ) or (
            len(suffix) == 3
            and suffix[0] == "publications"
            and suffix[2] in {"withdraw", "suspend"}
        ) or suffix == ("publication-drafts",):
            return "publication"
        return None

    @classmethod
    def _publication_formal_route_label(cls, method: str, path: str) -> Optional[str]:
        feature = cls._publication_formal_feature(path)
        if feature is None:
            return None
        segments = tuple(segment for segment in path.split("/") if segment)
        if segments == ("v2", "publication-invitations"):
            return f"{method} /v2/publication-invitations"
        if segments[:2] == ("v2", "publication-grants"):
            return f"{method} /v2/publication-grants/*/sessions"
        if segments[:2] == ("v2", "publication-sessions"):
            return f"{method} /v2/publication-sessions/*/{segments[-1]}"
        suffix = segments[3:]
        if suffix == ("publications",):
            return f"{method} /v2/vaults/*/publications"
        if suffix[0] == "publications" and suffix[-1] == "versions":
            return f"{method} /v2/vaults/*/publications/*/versions"
        if suffix == ("publication-drafts",):
            return f"{method} /v2/vaults/*/publication-drafts"
        if suffix[0] == "publication-drafts":
            return f"{method} /v2/vaults/*/publication-drafts/*/confirm/*"
        if suffix[0] == "publications":
            return f"{method} /v2/vaults/*/publications/*/{suffix[-1]}"
        if suffix == ("publication-grants",):
            return f"{method} /v2/vaults/*/publication-grants"
        return f"{method} /v2/vaults/*/publication-grants/*/revoke"

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
    def _owner_truth_media_feature(payload: Mapping[str, Any]) -> Optional[str]:
        kind = str(payload.get("mediaKind") or "").strip().lower()
        if kind == "audio":
            return "archiveAudioUpload"
        if kind == "video":
            return "archiveVideoUpload"
        return None

    @staticmethod
    def _deny(feature: str, reason: str, policy_revision: int) -> None:
        raise ReleasePolicyFeatureAccessDenied(
            feature=feature,
            reason=reason,
            policy_revision=policy_revision,
        )
