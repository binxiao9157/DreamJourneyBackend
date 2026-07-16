from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict


class RuntimeCapabilitySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: int
    capability: str
    implemented: bool
    enabled: bool
    providerReady: bool
    releaseVisible: bool
    externalVerified: bool
    provider: str
    fallbackMode: str
    reason: str
    evidenceTimestamp: Optional[datetime]


@dataclass(frozen=True)
class RuntimeCapabilityInput:
    capability: str
    implemented: bool
    enabled: bool
    provider_ready: bool
    release_visible: bool
    external_verified: bool
    provider: str
    fallback_mode: str
    reason: str
    evidence_timestamp: Optional[datetime] = None


class RuntimeCapabilityComposer:
    """Builds conservative five-axis capability snapshots for public runtime DTOs."""

    SCHEMA_VERSION = 1

    def __init__(
        self,
        *,
        now: Optional[datetime] = None,
        external_evidence_ttl_days: int = 30,
    ) -> None:
        instant = now or datetime.now(timezone.utc)
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=timezone.utc)
        self.now = instant
        self.external_evidence_ttl = timedelta(days=max(1, external_evidence_ttl_days))

    def compose(self, value: RuntimeCapabilityInput) -> RuntimeCapabilitySnapshot:
        evidence_timestamp = self._normalized_timestamp(value.evidence_timestamp)
        external_verified = value.external_verified
        reason = value.reason

        if external_verified and evidence_timestamp is None:
            external_verified = False
            reason = "externalEvidenceMissing"
        elif external_verified and self.now - evidence_timestamp > self.external_evidence_ttl:
            external_verified = False
            reason = "externalEvidenceStale"

        return RuntimeCapabilitySnapshot(
            schemaVersion=self.SCHEMA_VERSION,
            capability=value.capability,
            implemented=value.implemented,
            enabled=value.enabled,
            providerReady=value.provider_ready,
            releaseVisible=value.release_visible,
            externalVerified=external_verified,
            provider=value.provider,
            fallbackMode=value.fallback_mode,
            reason=reason,
            evidenceTimestamp=evidence_timestamp,
        )

    @staticmethod
    def _normalized_timestamp(value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
