"""Canonical, value-minimized lifecycle projection for private VoiceProfiles.

The legacy ``voice_profiles`` JSON payload remains the persistence adapter for
now.  This module gives it one authoritative lifecycle state, consent summary,
and redacted eligibility projection without treating provider readiness as an
owner acceptance.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Mapping

from app.services.safety_policy import (
    HighRiskCapability,
    SubjectEligibilityDecision,
    SubjectEligibilityReason,
)


VOICE_PROFILE_LIFECYCLE_SCHEMA_VERSION = "voice-profile-lifecycle-v1"
VOICE_PROFILE_ELIGIBILITY_POLICY_VERSION = "voice-profile-eligibility-v1"
VOICE_PROFILE_TRAINING_CONSENT_RECEIPT_SCHEMA_VERSION = "voice-training-consent-receipt-v1"
VOICE_PROFILE_CONSENT_DEFAULT_TTL_DAYS = 365
# A test fixture may model a profile, but it must never be treated as a
# production authorization source.  The write path stores ``serverVerified``
# only after the server-owned adult identity/liveness receipt has passed.
_TRUSTED_ELIGIBILITY_PROVENANCES = frozenset({"serverVerified"})


class VoiceProfileLifecycleState(str, Enum):
    DRAFT = "draft"
    UPLOAD_PENDING = "uploadPending"
    TRAINING = "training"
    PREVIEW_READY = "previewReady"
    ACCEPTED = "accepted"
    PAUSED = "paused"
    DELETING = "deleting"
    DELETED = "deleted"
    FAILED = "failed"


_SAMPLE_STATUS_BY_LIFECYCLE = {
    VoiceProfileLifecycleState.DRAFT: "notProvided",
    VoiceProfileLifecycleState.UPLOAD_PENDING: "pending",
    VoiceProfileLifecycleState.TRAINING: "pending",
    VoiceProfileLifecycleState.PREVIEW_READY: "ready",
    VoiceProfileLifecycleState.ACCEPTED: "ready",
    VoiceProfileLifecycleState.PAUSED: "disabled",
    VoiceProfileLifecycleState.DELETING: "pending",
    VoiceProfileLifecycleState.DELETED: "deleted",
    VoiceProfileLifecycleState.FAILED: "failed",
}


def canonical_lifecycle_state(profile: Mapping[str, Any]) -> VoiceProfileLifecycleState:
    """Return an explicit V4 state or a conservative legacy mapping."""

    raw_state = str(profile.get("lifecycleState") or "").strip()
    try:
        return VoiceProfileLifecycleState(raw_state)
    except ValueError:
        pass

    sample_status = str(profile.get("sampleStatus") or "notProvided").strip()
    if str(profile.get("deletionState") or "") == "deleted" or sample_status == "deleted":
        return VoiceProfileLifecycleState.DELETED
    if sample_status == "disabled":
        return VoiceProfileLifecycleState.PAUSED
    if sample_status == "failed":
        return VoiceProfileLifecycleState.FAILED
    if sample_status == "ready":
        if bool(profile.get("qualityAcceptanceRequired", True)):
            return VoiceProfileLifecycleState.PREVIEW_READY
        return VoiceProfileLifecycleState.ACCEPTED
    if sample_status == "pending":
        provider_status = str(profile.get("providerStatus") or "").strip()
        provider_slot_state = str(profile.get("providerSlotState") or "").strip()
        if provider_status not in {"", "mockOnly", "notSubmitted"} or provider_slot_state == "training":
            return VoiceProfileLifecycleState.TRAINING
        return VoiceProfileLifecycleState.UPLOAD_PENDING
    return VoiceProfileLifecycleState.DRAFT


def provider_observed_lifecycle_state(
    profile: Mapping[str, Any],
    provider_sample_status: str,
) -> VoiceProfileLifecycleState:
    """Map a provider observation without silently accepting a profile."""

    observed = str(provider_sample_status or "").strip()
    current = canonical_lifecycle_state(profile)
    # Local owner authority wins over a stale provider status. A profile that
    # was paused or put into deletion cannot be made usable by a later ready
    # observation alone.
    if current in {
        VoiceProfileLifecycleState.PAUSED,
        VoiceProfileLifecycleState.DELETING,
        VoiceProfileLifecycleState.DELETED,
    }:
        return current
    if observed == "ready":
        return (
            VoiceProfileLifecycleState.ACCEPTED
            if current is VoiceProfileLifecycleState.ACCEPTED
            else VoiceProfileLifecycleState.PREVIEW_READY
        )
    if observed == "failed":
        return VoiceProfileLifecycleState.FAILED
    if observed == "disabled":
        return VoiceProfileLifecycleState.PAUSED
    if observed == "deleted":
        return VoiceProfileLifecycleState.DELETED
    if observed == "pending":
        return VoiceProfileLifecycleState.TRAINING
    return current


def make_voice_profile_consent(
    *,
    purpose: str,
    version: str,
    now: datetime,
    expires_at: datetime | None = None,
) -> dict[str, str]:
    """Create the server-owned, value-minimized consent summary."""

    normalized_purpose = str(purpose or "").strip()
    normalized_version = str(version or "").strip()
    if not normalized_purpose:
        raise ValueError("voice profile consent purpose is required")
    if not normalized_version:
        raise ValueError("voice profile consent version is required")
    issued_at = _aware(now)
    resolved_expiry = _aware(expires_at or (issued_at + timedelta(days=VOICE_PROFILE_CONSENT_DEFAULT_TTL_DAYS)))
    if resolved_expiry <= issued_at:
        raise ValueError("voice profile consent expiry must be after issue time")
    return {
        "purpose": normalized_purpose,
        "version": normalized_version,
        # The API never accepts a client-provided consent object.  This marker
        # lets legacy profile blobs (which predate the server receipt flow)
        # stay visible but fail closed for training, preview, and synthesis.
        "source": "serverReceipt",
        "state": "active",
        "issuedAt": issued_at.isoformat(),
        "expiresAt": resolved_expiry.isoformat(),
    }


def make_voice_profile_training_consent_receipt(
    *,
    policy_version: str,
    statement_id: str,
    receipt_hash: str,
    issued_at: datetime,
    expires_at: datetime,
) -> dict[str, str]:
    """Persist only the server-verified training-consent receipt summary."""

    normalized_policy = str(policy_version or "").strip()
    normalized_statement = str(statement_id or "").strip()
    normalized_hash = str(receipt_hash or "").strip()
    start = _aware(issued_at)
    end = _aware(expires_at)
    if not normalized_policy or not normalized_statement or not normalized_hash:
        raise ValueError("voice profile training consent receipt is incomplete")
    if not normalized_hash.startswith("sha256:") or len(normalized_hash) != 71:
        raise ValueError("voice profile training consent receipt hash is invalid")
    if end <= start:
        raise ValueError("voice profile training consent receipt expiry is invalid")
    return {
        "schemaVersion": VOICE_PROFILE_TRAINING_CONSENT_RECEIPT_SCHEMA_VERSION,
        "policyVersion": normalized_policy,
        "statementId": normalized_statement,
        "receiptHash": normalized_hash,
        "issuedAt": start.isoformat(),
        "expiresAt": end.isoformat(),
    }


def apply_voice_profile_lifecycle(
    profile: Mapping[str, Any],
    *,
    state: VoiceProfileLifecycleState,
    now: datetime,
    consent: Mapping[str, Any] | None = None,
    eligibility_decision: SubjectEligibilityDecision | None = None,
    eligibility_provenance: str | None = None,
) -> dict[str, Any]:
    """Apply a lifecycle write while preserving legacy read compatibility."""

    if not isinstance(state, VoiceProfileLifecycleState):
        raise TypeError("voice profile lifecycle state is required")
    updated = dict(profile)
    issued_at = _aware(now)
    normalized_consent = _normalize_consent(consent if consent is not None else updated.get("consent"), now=issued_at)
    if eligibility_decision is not None:
        if eligibility_decision.capability is not HighRiskCapability.CLONED_VOICE:
            raise ValueError("voice profile eligibility must be for cloned voice")
        if eligibility_provenance not in _TRUSTED_ELIGIBILITY_PROVENANCES:
            raise ValueError("voice profile eligibility provenance is required")
        updated["subjectEligibilityDecision"] = eligibility_decision.model_dump(mode="json")
        updated["eligibilityProvenance"] = eligibility_provenance

    eligibility = _eligibility_projection(updated, consent=normalized_consent, now=issued_at)
    previous_version = _positive_int_or_zero(updated.get("profileVersion"))
    updated.update(
        {
            "lifecycleSchemaVersion": VOICE_PROFILE_LIFECYCLE_SCHEMA_VERSION,
            "lifecycleState": state.value,
            "profileVersion": previous_version + 1,
            "stateChangedAt": issued_at.isoformat(),
            "sampleStatus": _SAMPLE_STATUS_BY_LIFECYCLE[state],
            "qualityAcceptanceRequired": state is not VoiceProfileLifecycleState.ACCEPTED,
            "qualityAcceptanceState": "accepted" if state is VoiceProfileLifecycleState.ACCEPTED else "pending",
            "isEnabled": state is VoiceProfileLifecycleState.ACCEPTED and bool(eligibility["allowed"]),
            "consent": normalized_consent,
            "eligibility": eligibility,
        }
    )
    if state is VoiceProfileLifecycleState.DELETING:
        updated["deletionState"] = "pending"
    elif state is VoiceProfileLifecycleState.DELETED:
        updated["deletionState"] = "deleted"
    return updated


def profile_public_projection(
    profile: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return only eligibility/consent summaries that an iOS client may read."""

    evaluated_at = _aware(now or datetime.now(timezone.utc))
    projected = dict(profile)
    state = canonical_lifecycle_state(profile)
    consent = _normalize_consent(profile.get("consent"), now=evaluated_at)
    eligibility = _eligibility_projection(profile, consent=consent, now=evaluated_at)
    projected.update(
        {
            "lifecycleSchemaVersion": VOICE_PROFILE_LIFECYCLE_SCHEMA_VERSION,
            "lifecycleState": state.value,
            "profileVersion": _positive_int_or_zero(profile.get("profileVersion")),
            "stateChangedAt": str(profile.get("stateChangedAt") or profile.get("updatedAt") or ""),
            "sampleStatus": _SAMPLE_STATUS_BY_LIFECYCLE[state],
            "qualityAcceptanceRequired": state is not VoiceProfileLifecycleState.ACCEPTED,
            "qualityAcceptanceState": "accepted" if state is VoiceProfileLifecycleState.ACCEPTED else "pending",
            "isEnabled": state is VoiceProfileLifecycleState.ACCEPTED and bool(eligibility["allowed"]),
            "consent": consent,
            "eligibility": eligibility,
        }
    )
    projected["allowedOperations"] = _allowed_operations(
        state=state,
        eligibility=eligibility,
        consent=consent,
    )
    projected.pop("subjectEligibilityDecision", None)
    projected.pop("eligibilityProvenance", None)
    projected.pop("eligibilityReceipt", None)
    projected.pop("trainingConsentReceipt", None)
    return projected


def is_voice_profile_synthesizable(
    profile: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> bool:
    projection = profile_public_projection(profile, now=now)
    return "synthesize" in projection["allowedOperations"]


def _allowed_operations(
    *,
    state: VoiceProfileLifecycleState,
    eligibility: Mapping[str, Any],
    consent: Mapping[str, Any],
) -> list[str]:
    if not bool(eligibility.get("allowed")) or consent.get("state") != "active":
        return []
    if state is VoiceProfileLifecycleState.DRAFT:
        return ["upload", "delete"]
    if state is VoiceProfileLifecycleState.UPLOAD_PENDING:
        return ["delete"]
    if state is VoiceProfileLifecycleState.TRAINING:
        return ["delete"]
    if state is VoiceProfileLifecycleState.PREVIEW_READY:
        return ["preview", "accept", "delete"]
    if state is VoiceProfileLifecycleState.ACCEPTED:
        if consent.get("purpose") != "private_synthesis":
            return ["preview", "delete"]
        return ["preview", "synthesize", "pause", "delete"]
    if state is VoiceProfileLifecycleState.PAUSED:
        return ["preview", "delete"]
    if state is VoiceProfileLifecycleState.FAILED:
        return ["retry", "delete"]
    return []


def _eligibility_projection(
    profile: Mapping[str, Any],
    *,
    consent: Mapping[str, Any],
    now: datetime,
) -> dict[str, Any]:
    raw_decision = profile.get("subjectEligibilityDecision")
    provenance = str(profile.get("eligibilityProvenance") or "").strip()
    allowed = False
    reason_code = SubjectEligibilityReason.AGE_VERIFICATION_MISSING.value
    if provenance in _TRUSTED_ELIGIBILITY_PROVENANCES and isinstance(raw_decision, Mapping):
        try:
            decision = SubjectEligibilityDecision.model_validate(raw_decision)
        except ValueError:
            decision = None
        if decision is not None and decision.capability is HighRiskCapability.CLONED_VOICE:
            allowed = bool(decision.allowed)
            reason_code = decision.reason.value
    if consent.get("state") != "active":
        allowed = False
        reason_code = SubjectEligibilityReason.PURPOSE_CONSENT_MISSING.value
    elif consent.get("source") != "serverReceipt":
        allowed = False
        reason_code = SubjectEligibilityReason.PURPOSE_CONSENT_MISSING.value
    return {
        "allowed": allowed,
        "reasonCode": reason_code,
        "policyVersion": VOICE_PROFILE_ELIGIBILITY_POLICY_VERSION,
        "evaluatedAt": now.isoformat(),
        "expiresAt": consent.get("expiresAt"),
    }


def _normalize_consent(raw: object, *, now: datetime) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {
            "purpose": None,
            "version": None,
            "source": None,
            "state": "missing",
            "expiresAt": None,
        }
    purpose = str(raw.get("purpose") or "").strip() or None
    version = str(raw.get("version") or "").strip() or None
    source = str(raw.get("source") or "").strip() or None
    expires_at = _parse_datetime(raw.get("expiresAt"))
    issued_at = _parse_datetime(raw.get("issuedAt"))
    if not purpose or not version or expires_at is None:
        return {
            "purpose": purpose,
            "version": version,
            "source": source,
            "state": "missing",
            "expiresAt": str(raw.get("expiresAt") or "") or None,
        }
    if issued_at is not None and expires_at <= issued_at:
        return {
            "purpose": purpose,
            "version": version,
            "source": source,
            "state": "expired",
            "expiresAt": expires_at.isoformat(),
        }
    if expires_at <= now:
        return {
            "purpose": purpose,
            "version": version,
            "state": "expired",
            "expiresAt": expires_at.isoformat(),
        }
    return {
        "purpose": purpose,
        "version": version,
        "source": source,
        "state": "active",
        "expiresAt": expires_at.isoformat(),
    }


def _parse_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("voice profile lifecycle time must be timezone-aware")
    return value.astimezone(timezone.utc)


def _positive_int_or_zero(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        result = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(result, 0)


__all__ = [
    "VOICE_PROFILE_ELIGIBILITY_POLICY_VERSION",
    "VOICE_PROFILE_LIFECYCLE_SCHEMA_VERSION",
    "VOICE_PROFILE_TRAINING_CONSENT_RECEIPT_SCHEMA_VERSION",
    "VoiceProfileLifecycleState",
    "apply_voice_profile_lifecycle",
    "canonical_lifecycle_state",
    "is_voice_profile_synthesizable",
    "make_voice_profile_consent",
    "make_voice_profile_training_consent_receipt",
    "profile_public_projection",
    "provider_observed_lifecycle_state",
]
