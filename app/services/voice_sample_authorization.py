"""Short-lived, server-signed authorization statements for voice samples."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


VOICE_SAMPLE_AUTHORIZATION_SCHEMA_VERSION = "voice-sample-authorization-v1"
VOICE_SAMPLE_AUTHORIZATION_TTL_SECONDS = 15 * 60
_STATEMENTS: tuple[tuple[str, str], ...] = (
    ("self-private-voice-01", "我确认正在提交的是我本人的声音样本，仅用于我的私有声音复刻。"),
    ("self-private-voice-02", "这是我本人自愿录制的声音样本，我同意仅在我的私有回响中使用。"),
    ("self-private-voice-03", "我已知晓声音样本会用于本人私有声音复刻，并确认由我本人提交。"),
    ("self-private-voice-04", "我确认此声音样本属于我本人，并同意用于本人的私有语音合成。"),
)


class VoiceSampleAuthorizationError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class VoiceSampleAuthorizationChallenge:
    receipt_id: str
    challenge_id: str
    statement_id: str
    statement: str
    expires_at: str

    def public_payload(self) -> dict[str, str]:
        return {
            "schemaVersion": VOICE_SAMPLE_AUTHORIZATION_SCHEMA_VERSION,
            "receiptId": self.receipt_id,
            "challengeId": self.challenge_id,
            "statementId": self.statement_id,
            "statement": self.statement,
            "expiresAt": self.expires_at,
        }


@dataclass(frozen=True)
class VerifiedVoiceSampleAuthorization:
    challenge_id: str
    statement_id: str
    expires_at: str
    receipt_hash: str


def issue_voice_sample_authorization_challenge(
    *,
    secret: str,
    user_id: str,
    voice_profile_id: str,
    now: datetime | None = None,
) -> VoiceSampleAuthorizationChallenge:
    signing_key = _signing_key(secret)
    issued_at = _aware(now or datetime.now(timezone.utc))
    expires_at = issued_at + timedelta(seconds=VOICE_SAMPLE_AUTHORIZATION_TTL_SECONDS)
    statement_id, statement = secrets.choice(_STATEMENTS)
    payload = {
        "challengeId": secrets.token_urlsafe(18),
        "expiresAt": int(expires_at.timestamp()),
        "issuedAt": int(issued_at.timestamp()),
        "profileUserId": str(user_id),
        "schemaVersion": VOICE_SAMPLE_AUTHORIZATION_SCHEMA_VERSION,
        "statementId": statement_id,
        "voiceProfileId": str(voice_profile_id),
    }
    receipt_id = _sign(payload, signing_key)
    return VoiceSampleAuthorizationChallenge(
        receipt_id=receipt_id,
        challenge_id=str(payload["challengeId"]),
        statement_id=statement_id,
        statement=statement,
        expires_at=expires_at.isoformat(),
    )


def verify_voice_sample_authorization_receipt(
    *,
    secret: str,
    receipt_id: str,
    user_id: str,
    voice_profile_id: str,
    now: datetime | None = None,
) -> VerifiedVoiceSampleAuthorization:
    signing_key = _signing_key(secret)
    payload = _verify(str(receipt_id or ""), signing_key)
    if payload.get("schemaVersion") != VOICE_SAMPLE_AUTHORIZATION_SCHEMA_VERSION:
        raise VoiceSampleAuthorizationError("invalidSampleAuthorizationReceipt")
    if payload.get("profileUserId") != str(user_id) or payload.get("voiceProfileId") != str(voice_profile_id):
        raise VoiceSampleAuthorizationError("sampleAuthorizationOwnerMismatch")
    statement_id = str(payload.get("statementId") or "")
    if statement_id not in {statement[0] for statement in _STATEMENTS}:
        raise VoiceSampleAuthorizationError("invalidSampleAuthorizationReceipt")
    challenge_id = str(payload.get("challengeId") or "")
    if not challenge_id:
        raise VoiceSampleAuthorizationError("invalidSampleAuthorizationReceipt")
    try:
        expiry_timestamp = int(payload.get("expiresAt"))
    except (TypeError, ValueError) as exc:
        raise VoiceSampleAuthorizationError("invalidSampleAuthorizationReceipt") from exc
    evaluated_at = _aware(now or datetime.now(timezone.utc))
    expires_at = datetime.fromtimestamp(expiry_timestamp, tz=timezone.utc)
    if expires_at <= evaluated_at:
        raise VoiceSampleAuthorizationError("sampleAuthorizationReceiptExpired")
    return VerifiedVoiceSampleAuthorization(
        challenge_id=challenge_id,
        statement_id=statement_id,
        expires_at=expires_at.isoformat(),
        receipt_hash="sha256:" + hashlib.sha256(receipt_id.encode("utf-8")).hexdigest(),
    )


def _signing_key(secret: str) -> bytes:
    value = str(secret or "").strip()
    if not value:
        raise VoiceSampleAuthorizationError("sampleAuthorizationUnavailable")
    return value.encode("utf-8")


def _sign(payload: dict[str, Any], key: bytes) -> str:
    encoded_payload = _b64url(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(key, encoded_payload.encode("ascii"), hashlib.sha256).digest()
    return encoded_payload + "." + _b64url(signature)


def _verify(receipt_id: str, key: bytes) -> dict[str, Any]:
    pieces = receipt_id.split(".")
    if len(pieces) != 2 or not all(pieces):
        raise VoiceSampleAuthorizationError("invalidSampleAuthorizationReceipt")
    encoded_payload, encoded_signature = pieces
    expected = hmac.new(key, encoded_payload.encode("ascii"), hashlib.sha256).digest()
    try:
        supplied = _b64url_decode(encoded_signature)
    except ValueError as exc:
        raise VoiceSampleAuthorizationError("invalidSampleAuthorizationReceipt") from exc
    if not hmac.compare_digest(expected, supplied):
        raise VoiceSampleAuthorizationError("invalidSampleAuthorizationReceipt")
    try:
        payload = json.loads(_b64url_decode(encoded_payload).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise VoiceSampleAuthorizationError("invalidSampleAuthorizationReceipt") from exc
    if not isinstance(payload, dict):
        raise VoiceSampleAuthorizationError("invalidSampleAuthorizationReceipt")
    return payload


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, binascii.Error) as exc:
        raise ValueError("invalid base64url") from exc


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("sample authorization time must be timezone-aware")
    return value.astimezone(timezone.utc)


__all__ = [
    "VOICE_SAMPLE_AUTHORIZATION_SCHEMA_VERSION",
    "VOICE_SAMPLE_AUTHORIZATION_TTL_SECONDS",
    "VoiceSampleAuthorizationChallenge",
    "VoiceSampleAuthorizationError",
    "VerifiedVoiceSampleAuthorization",
    "issue_voice_sample_authorization_challenge",
    "verify_voice_sample_authorization_receipt",
]
