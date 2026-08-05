"""Append-only, value-minimized receipts for external data-rights effects.

The receipt is intentionally a *linkable observation*, not a Provider request.
It binds a data-rights request to a domain-local effect identity using hashes
only.  Raw Provider IDs, object keys, media URLs and credential material are
never accepted by this contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any, Dict, Optional

from app.services.data_rights_external_effect_projection import (
    DATA_RIGHTS_EXTERNAL_EFFECT_DOMAINS,
    DataRightsExternalEffectObservation,
)


DATA_RIGHTS_EXTERNAL_EFFECT_RECEIPT_SCHEMA_VERSION = 1
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_RECEIPT_ID_PATTERN = re.compile(r"^dre_[0-9a-f]{40}$")
_STATES = frozenset(
    {"pending", "accepted", "completed", "failed", "unknown", "unsupported"}
)


class DataRightsExternalEffectReceiptError(ValueError):
    """Raised when an external-effect receipt is not value-minimized or valid."""


def _required_identifier(value: Any, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise DataRightsExternalEffectReceiptError(f"{field} must be an opaque identifier")
    return normalized


def _required_hash(value: Any, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _HASH_PATTERN.fullmatch(normalized):
        raise DataRightsExternalEffectReceiptError(f"{field} must be a SHA-256 digest")
    return normalized


def _timestamp(value: Any, *, field: str) -> str:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise DataRightsExternalEffectReceiptError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DataRightsExternalEffectReceiptError(f"{field} must include timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _canonical_hash(value: Dict[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DataRightsExternalEffectReceipt:
    """One append-only observation for an external cleanup domain."""

    request_id: str
    owner_subject_hash: str
    domain: str
    effect_identity_hash: str
    state: str
    provider_receipt_present: bool
    reason_code: str
    observed_at: str
    evidence_hash: Optional[str] = None
    retention_until: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _required_identifier(self.request_id, field="requestId"))
        object.__setattr__(self, "owner_subject_hash", _required_hash(self.owner_subject_hash, field="ownerSubjectHash"))
        domain = _required_identifier(self.domain, field="domain")
        if domain not in DATA_RIGHTS_EXTERNAL_EFFECT_DOMAINS:
            raise DataRightsExternalEffectReceiptError("domain is unsupported")
        object.__setattr__(self, "domain", domain)
        object.__setattr__(self, "effect_identity_hash", _required_hash(self.effect_identity_hash, field="effectIdentityHash"))
        state = str(self.state or "").strip().lower()
        if state not in _STATES:
            raise DataRightsExternalEffectReceiptError("state is unsupported")
        object.__setattr__(self, "state", state)
        if not isinstance(self.provider_receipt_present, bool):
            raise DataRightsExternalEffectReceiptError("providerReceiptPresent must be boolean")
        object.__setattr__(self, "reason_code", _required_identifier(self.reason_code, field="reasonCode"))
        object.__setattr__(self, "observed_at", _timestamp(self.observed_at, field="observedAt"))
        if self.evidence_hash is not None:
            object.__setattr__(self, "evidence_hash", _required_hash(self.evidence_hash, field="evidenceHash"))
        if self.retention_until is not None:
            retention_until = _timestamp(self.retention_until, field="retentionUntil")
            if retention_until < self.observed_at:
                raise DataRightsExternalEffectReceiptError(
                    "retentionUntil must not precede observedAt"
                )
            object.__setattr__(self, "retention_until", retention_until)

    @property
    def observation_hash(self) -> str:
        return _canonical_hash(
            {
                "domain": self.domain,
                "effectIdentityHash": self.effect_identity_hash,
                "evidenceHash": self.evidence_hash,
                "ownerSubjectHash": self.owner_subject_hash,
                "providerReceiptPresent": self.provider_receipt_present,
                "reasonCode": self.reason_code,
                "requestId": self.request_id,
                "schemaVersion": DATA_RIGHTS_EXTERNAL_EFFECT_RECEIPT_SCHEMA_VERSION,
                "state": self.state,
            }
        )

    @property
    def receipt_id(self) -> str:
        return "dre_" + self.observation_hash[:40]

    def persistence_payload(self) -> Dict[str, Any]:
        """Return the only data allowed to enter the receipt repository."""

        return {
            "id": self.receipt_id,
            "requestId": self.request_id,
            "ownerSubjectHash": self.owner_subject_hash,
            "domain": self.domain,
            "effectIdentityHash": self.effect_identity_hash,
            "state": self.state,
            "providerReceiptPresent": self.provider_receipt_present,
            "reasonCode": self.reason_code,
            "observationHash": self.observation_hash,
            "observedAt": self.observed_at,
            "evidenceHash": self.evidence_hash,
            "retentionUntil": self.retention_until,
        }

    def projection_observation(self) -> DataRightsExternalEffectObservation:
        """Return a redacted observation with an in-process owner binding."""

        return DataRightsExternalEffectObservation(
            request_id=self.request_id,
            owner_subject_hash=self.owner_subject_hash,
            domain=self.domain,
            effect_identity_hash=self.effect_identity_hash,
            state=self.state,
            provider_receipt_present=self.provider_receipt_present,
            reason_codes=[self.reason_code],
            observed_at=self.observed_at,
        )


def receipt_from_persistence(record: Dict[str, Any]) -> DataRightsExternalEffectReceipt:
    """Rehydrate a validated receipt without accepting extra stored values."""

    receipt = DataRightsExternalEffectReceipt(
        request_id=record.get("requestId"),
        owner_subject_hash=record.get("ownerSubjectHash"),
        domain=record.get("domain"),
        effect_identity_hash=record.get("effectIdentityHash"),
        state=record.get("state"),
        provider_receipt_present=record.get("providerReceiptPresent"),
        reason_code=record.get("reasonCode"),
        observed_at=record.get("observedAt"),
        evidence_hash=record.get("evidenceHash"),
        retention_until=record.get("retentionUntil"),
    )
    expected = receipt.persistence_payload()
    if str(record.get("id") or "") != expected["id"] or str(record.get("observationHash") or "") != expected["observationHash"]:
        raise DataRightsExternalEffectReceiptError("persisted receipt identity is inconsistent")
    return receipt


__all__ = [
    "DATA_RIGHTS_EXTERNAL_EFFECT_RECEIPT_SCHEMA_VERSION",
    "DataRightsExternalEffectReceipt",
    "DataRightsExternalEffectReceiptError",
    "receipt_from_persistence",
]
