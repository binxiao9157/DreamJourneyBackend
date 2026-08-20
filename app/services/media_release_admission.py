"""Public-versus-internal admission evidence for Owner Truth media.

Operational readiness is sufficient for a separately entitled internal pilot,
but it is never sufficient for ordinary authenticated users. Public admission
also requires a current external verification receipt, and server-local
filesystem storage is permanently excluded from that public path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.core.config import Settings
from app.services.provider_runtime import ProviderRuntimeStatus


MEDIA_PUBLIC_CAPABILITIES = frozenset(
    {"ownerTruthMediaStorage", "ownerTruthMediaProcessing"}
)
MEDIA_EXTERNAL_EVIDENCE_TTL = timedelta(days=30)


@dataclass(frozen=True)
class MediaExternalEvidence:
    external_verified: bool
    evidence_timestamp: Optional[datetime]
    reason: str
    evidence_status: str


def resolve_media_external_evidence(
    *,
    settings: Settings,
    status: ProviderRuntimeStatus,
    storage_status: Optional[ProviderRuntimeStatus] = None,
    now: Optional[datetime] = None,
) -> MediaExternalEvidence:
    """Return a value-free, fail-closed public evidence decision."""

    if status.capability not in MEDIA_PUBLIC_CAPABILITIES:
        return MediaExternalEvidence(False, None, status.reason, status.evidence_status)
    if not status.enabled or not status.provider_ready:
        return MediaExternalEvidence(False, None, status.reason, status.evidence_status)

    instant = _normalized_now(now)
    if status.capability == "ownerTruthMediaStorage":
        if status.provider == "filesystem":
            return MediaExternalEvidence(False, None, "internalProviderOnly", "internalOnly")
        if status.provider not in {"cos", "s3"}:
            return MediaExternalEvidence(False, None, "publicProviderUnavailable", "notVerified")
        configured = settings.owner_truth_media_storage_external_verified
        raw_timestamp = settings.owner_truth_media_storage_evidence_timestamp
    else:
        dependency = storage_status
        if dependency is None:
            return MediaExternalEvidence(
                False,
                None,
                "storageExternalVerificationRequired",
                "notVerified",
            )
        storage_evidence = resolve_media_external_evidence(
            settings=settings,
            status=dependency,
            now=instant,
        )
        if not storage_evidence.external_verified:
            return MediaExternalEvidence(
                False,
                None,
                "storageExternalVerificationRequired",
                "notVerified",
            )
        configured = settings.owner_truth_media_processing_external_verified
        raw_timestamp = settings.owner_truth_media_processing_evidence_timestamp

    if not configured:
        return MediaExternalEvidence(False, None, "externalEvidenceMissing", "notVerified")
    evidence_timestamp = _parse_timestamp(raw_timestamp)
    if evidence_timestamp is None:
        return MediaExternalEvidence(False, None, "externalEvidenceMissing", "notVerified")
    if evidence_timestamp > instant + timedelta(minutes=5):
        return MediaExternalEvidence(False, evidence_timestamp, "externalEvidenceInvalid", "invalid")
    if instant - evidence_timestamp > MEDIA_EXTERNAL_EVIDENCE_TTL:
        return MediaExternalEvidence(False, evidence_timestamp, "externalEvidenceStale", "stale")
    return MediaExternalEvidence(True, evidence_timestamp, "ready", "externallyVerified")


def _normalized_now(value: Optional[datetime]) -> datetime:
    instant = value or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        return instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(timezone.utc)


def _parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
