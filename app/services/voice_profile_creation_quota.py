from __future__ import annotations

from hashlib import sha256
from typing import Any, Dict


VOICE_PROFILE_CREATION_LIMIT = 5
VOICE_PROFILE_CREATION_QUOTA_SCHEMA_VERSION = "voice-profile-creation-quota-v1"
VOICE_PROFILE_CREATION_RECEIPT_SCHEMA_VERSION = "voice-profile-creation-receipt-v1"


class VoiceProfileCreationLimitReached(ValueError):
    def __init__(self, quota: Dict[str, Any]) -> None:
        super().__init__("voice profile creation limit reached")
        self.quota = dict(quota)


class VoiceProfileCreationCommandConflict(ValueError):
    pass


def voice_profile_creation_quota_payload(
    *,
    subject_id: str,
    creation_count: int,
) -> Dict[str, Any]:
    normalized_count = max(0, int(creation_count))
    return {
        "schemaVersion": VOICE_PROFILE_CREATION_QUOTA_SCHEMA_VERSION,
        "subjectScope": "authenticatedVoiceSubject",
        "subjectId": str(subject_id or "").strip(),
        "creationLimit": VOICE_PROFILE_CREATION_LIMIT,
        "creationCount": normalized_count,
        "remainingCount": max(0, VOICE_PROFILE_CREATION_LIMIT - normalized_count),
        "limitReached": normalized_count >= VOICE_PROFILE_CREATION_LIMIT,
        "deletionRefundsCreation": False,
    }


def voice_profile_creation_receipt_id(subject_id: str, command_id: str) -> str:
    digest = sha256(f"{subject_id}:{command_id}".encode("utf-8")).hexdigest()
    return f"vpcr_{digest[:32]}"


def voice_profile_creation_reservation_payload(
    *,
    subject_id: str,
    command_id: str,
    voice_profile_id: str,
    creation_ordinal: int,
    creation_count: int,
    accepted_at: str,
    idempotent: bool,
) -> Dict[str, Any]:
    payload = voice_profile_creation_quota_payload(
        subject_id=subject_id,
        creation_count=creation_count,
    )
    payload.update(
        {
            "receiptSchemaVersion": VOICE_PROFILE_CREATION_RECEIPT_SCHEMA_VERSION,
            "receiptId": voice_profile_creation_receipt_id(subject_id, command_id),
            "voiceProfileId": voice_profile_id,
            "creationOrdinal": max(1, int(creation_ordinal)),
            "acceptedAt": accepted_at,
            "idempotent": bool(idempotent),
            "authorizationScope": "voiceSubjectSelf",
        }
    )
    return payload
