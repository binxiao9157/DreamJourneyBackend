"""Value-free async acceptance for private VoiceProfile deletion.

The API revokes local synthesis access before accepting this effect.  A worker
or Provider adapter may execute/reconcile the upstream deletion later, but an
accepted local outbox record must never be presented as an upstream receipt.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Mapping

from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectTarget, EffectReceiptSummary
from app.async_effects.provider_effect_repository import ProviderEffectPersistenceSummary
from app.async_effects.provider_effects import (
    ProviderEffectIntent,
    ProviderEffectReceipt,
    ProviderEffectState,
)
from app.services.voice_profile_lifecycle import (
    VoiceProfileLifecycleState,
    canonical_lifecycle_state,
)


VOICE_PROFILE_DELETION_EFFECT_SCHEMA_VERSION = "voice-profile-deletion-effect-v1"
VOICE_PROFILE_PROVIDER_DELETE_RECEIPT_SCHEMA_VERSION = "voice-profile-provider-delete-receipt-v1"
VOICE_PROFILE_DELETION_OPERATION_TYPE = "voiceProfile.delete"
VOICE_PROFILE_DELETION_EVENT_TYPE = "voiceProfile.deletionRequested"
VOICE_PROFILE_DELETION_JOB_TYPE = "voiceProfile.delete"
VOICE_PROFILE_DELETION_MAX_ATTEMPTS = 3
VOICE_PROFILE_DELETION_PROVIDER = "volcengineVoiceClone"
VOICE_PROFILE_DELETION_CAPABILITY = "voiceCloneDeletion"


class VoiceProfileDeletionEffectError(RuntimeError):
    """Voice deletion cannot be safely accepted as an external effect."""


@dataclass(frozen=True)
class VoiceProfileDeletionEffectResult:
    """Accepted local effect and Provider-effect observation for one profile."""

    deletion_effect: EffectReceiptSummary
    provider_effect: ProviderEffectPersistenceSummary

    def public_receipt(self, *, recorded_at: str) -> dict[str, object]:
        """Return a bounded receipt without Provider IDs, samples or payloads."""

        return {
            "schemaVersion": VOICE_PROFILE_PROVIDER_DELETE_RECEIPT_SCHEMA_VERSION,
            "state": self.provider_effect.effective_state.value,
            "providerReceiptPresent": False,
            "reasonCode": "voiceProfileDeletionQueued",
            "recordedAt": recorded_at,
            "effectId": self.provider_effect.intent.provider_effect_id,
            "operationId": self.deletion_effect.operation_id,
            "outboxEventId": self.deletion_effect.outbox_event_id,
            "jobId": self.deletion_effect.job_id,
            "providerEffectKey": self.provider_effect.intent.provider_effect_key,
            "receiptHash": self.provider_effect.receipt_hash,
        }


def _canonical_hash(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def _opaque_identifier(prefix: str, value: object) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise VoiceProfileDeletionEffectError(f"{prefix} identity is required")
    return f"{prefix}-{sha256(normalized.encode('utf-8')).hexdigest()[:32]}"


def _profile_version(profile: Mapping[str, Any]) -> int:
    try:
        version = int(profile.get("profileVersion") or 0)
    except (TypeError, ValueError) as exc:
        raise VoiceProfileDeletionEffectError("voice profile version is invalid") from exc
    if version < 1:
        raise VoiceProfileDeletionEffectError("voice profile version is required")
    return version


def build_voice_profile_deletion_effect_intent(
    *,
    user_id: str,
    profile: Mapping[str, Any],
    authority_epoch: int,
) -> AsyncEffectIntent:
    """Build one deterministic deletion effect without copying Provider data."""

    if canonical_lifecycle_state(profile) not in {
        VoiceProfileLifecycleState.DELETING,
        VoiceProfileLifecycleState.DELETED,
    }:
        raise VoiceProfileDeletionEffectError("voice profile deletion requires locally revoked access")
    profile_user_id = str(profile.get("userId") or user_id).strip()
    if not profile_user_id or profile_user_id != str(user_id or "").strip():
        raise VoiceProfileDeletionEffectError("voice profile deletion owner does not match")
    voice_profile_id = str(profile.get("voiceProfileId") or "").strip()
    deletion_requested_at = str(profile.get("deletionRequestedAt") or "").strip()
    if not voice_profile_id or not deletion_requested_at:
        raise VoiceProfileDeletionEffectError("voice profile deletion target is incomplete")
    if isinstance(authority_epoch, bool) or not isinstance(authority_epoch, int) or authority_epoch < 0:
        raise VoiceProfileDeletionEffectError("voice profile deletion authority epoch is invalid")
    version = _profile_version(profile)
    profile_identity_hash = _canonical_hash(
        {
            "ownerSubjectId": user_id,
            "voiceProfileId": voice_profile_id,
            "schemaVersion": VOICE_PROFILE_DELETION_EFFECT_SCHEMA_VERSION,
        }
    )
    return AsyncEffectIntent(
        operation_type=VOICE_PROFILE_DELETION_OPERATION_TYPE,
        target=AsyncEffectTarget(
            owner_subject_id=user_id,
            vault_id=_opaque_identifier("voiceVault", user_id),
            resource_type="voiceProfile",
            resource_id=_opaque_identifier("voiceProfile", voice_profile_id),
            resource_version=version,
            purpose="privateVoiceDeletion",
            authority_epoch=authority_epoch,
        ),
        payload_hash=_canonical_hash(
            {
                "deletionRequestedAt": deletion_requested_at,
                "profileIdentityHash": profile_identity_hash,
                "profileVersion": version,
                "schemaVersion": VOICE_PROFILE_DELETION_EFFECT_SCHEMA_VERSION,
            }
        ),
        event_type=VOICE_PROFILE_DELETION_EVENT_TYPE,
        job_type=VOICE_PROFILE_DELETION_JOB_TYPE,
        max_attempts=VOICE_PROFILE_DELETION_MAX_ATTEMPTS,
    )


def enqueue_voice_profile_deletion_effect(
    *,
    store: Any,
    user_id: str,
    profile: Mapping[str, Any],
    authority_epoch: int,
) -> VoiceProfileDeletionEffectResult:
    """Persist an idempotent outbox request and its local acceptance receipt."""

    intent = build_voice_profile_deletion_effect_intent(
        user_id=user_id,
        profile=profile,
        authority_epoch=authority_epoch,
    )
    deletion_effect = store.effect_kernel_repository().accept(intent)
    provider_intent = build_voice_profile_deletion_provider_effect_intent(
        user_id=user_id,
        profile=profile,
        authority_epoch=authority_epoch,
    )
    provider_effect = store.provider_effect_repository().record(
        ProviderEffectReceipt(
            intent=provider_intent,
            state=ProviderEffectState.ACCEPTED,
            reason_code="voiceProfileDeletionQueued",
            observation_origin="localAcceptance",
        )
    )
    return VoiceProfileDeletionEffectResult(
        deletion_effect=deletion_effect,
        provider_effect=provider_effect,
    )


def build_voice_profile_deletion_provider_effect_intent(
    *,
    user_id: str,
    profile: Mapping[str, Any],
    authority_epoch: int,
) -> ProviderEffectIntent:
    """Rebuild the deterministic provider-effect identity for a worker.

    The async job carries only opaque target coordinates.  The worker loads
    the locally tombstoned profile by its accepted operation id, then uses
    this helper to verify it is still bound to the original provider effect.
    """

    intent = build_voice_profile_deletion_effect_intent(
        user_id=user_id,
        profile=profile,
        authority_epoch=authority_epoch,
    )
    return ProviderEffectIntent(
        effect_intent=intent,
        provider=VOICE_PROFILE_DELETION_PROVIDER,
        capability=VOICE_PROFILE_DELETION_CAPABILITY,
        request_hash=_canonical_hash(
            {
                "operationStableKey": intent.stable_key,
                "profileVersion": intent.target.resource_version,
                "schemaVersion": VOICE_PROFILE_DELETION_EFFECT_SCHEMA_VERSION,
            }
        ),
    )
__all__ = [
    "VOICE_PROFILE_DELETION_CAPABILITY",
    "VOICE_PROFILE_DELETION_EFFECT_SCHEMA_VERSION",
    "VOICE_PROFILE_DELETION_EVENT_TYPE",
    "VOICE_PROFILE_DELETION_JOB_TYPE",
    "VOICE_PROFILE_DELETION_MAX_ATTEMPTS",
    "VOICE_PROFILE_DELETION_OPERATION_TYPE",
    "VOICE_PROFILE_DELETION_PROVIDER",
    "VOICE_PROFILE_PROVIDER_DELETE_RECEIPT_SCHEMA_VERSION",
    "VoiceProfileDeletionEffectError",
    "VoiceProfileDeletionEffectResult",
    "build_voice_profile_deletion_effect_intent",
    "build_voice_profile_deletion_provider_effect_intent",
    "enqueue_voice_profile_deletion_effect",
]
