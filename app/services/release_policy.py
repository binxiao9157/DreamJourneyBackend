from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Literal, Optional, Set

from pydantic import BaseModel, ConfigDict


ReleaseAudience = Literal["owner", "family", "visitor", "qa"]
Gate = Literal["G0", "G1", "G2", "G3", "G4"]


class ReleasePolicyVersionDowngrade(RuntimeError):
    def __init__(self, *, known_revision: int, server_revision: int):
        super().__init__("client knows a newer release policy revision")
        self.known_revision = known_revision
        self.server_revision = server_revision


class ReleasePolicyFeatureDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    feature: str
    enabled: bool
    releaseVisible: bool
    audience: ReleaseAudience
    cohort: str
    requiredGates: tuple[Gate, ...]
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
    ) -> None:
        self.policy_revision = max(1, policy_revision)
        self.min_client_build = max(1, min_client_build)
        self.ttl_seconds = max(60, ttl_seconds)
        self.emergency_revision = max(0, emergency_revision)
        self.emergency_disabled_features: Set[str] = set(emergency_disabled_features or ())

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
            "shadowMode": True,
        }

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
            shadowMode=True,
            snapshotDecision="clientBelowMinimum" if client_below_minimum else "shadowAllowlist",
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
            reason=reason,
        )
