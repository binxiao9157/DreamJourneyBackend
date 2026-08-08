"""Access-first reconciliation for account-deletion external effects.

The reconciler coordinates existing append-only data-rights receipts. It does
not retain Provider identifiers, object keys, URLs or raw errors. Provider
adapters return only a normalized state and optional evidence digest.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import re
from typing import Any, Dict, Mapping, Optional, Protocol, Sequence

from app.services.data_rights_external_effect_receipts import (
    DataRightsExternalEffectReceipt,
)
from app.services.data_rights_external_effect_projection import (
    DataRightsExternalEffectObservation,
)


DATA_RIGHTS_EXTERNAL_EFFECT_RECONCILER_SCHEMA_VERSION = 1
DATA_RIGHTS_EXTERNAL_EFFECT_RECONCILIATION_DOMAINS = (
    "objectStorage",
    "providerVoice",
    "providerDigitalHuman",
    "notificationDelivery",
    "backupRetention",
)
_ADAPTER_STATES = frozenset({"pending", "completed", "failed", "unknown", "unsupported"})
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_REASON_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_MANUAL_REVIEW_REASON = "externalEffectManualReviewRequired"
_BOUNDARY_REASON_CODES = frozenset(
    {
        "objectStorageNoExternalTarget",
        "providerVoiceExitAdapterNotConfigured",
        "providerDigitalHumanExitAdapterNotConfigured",
        "notificationDeliveryExitAdapterNotConfigured",
        "backupRetentionExternalReceiptPending",
    }
)


class DataRightsExternalEffectReconciliationError(ValueError):
    """Raised when a reconciliation command violates the rights boundary."""


@dataclass(frozen=True)
class DataRightsExternalEffectAdapterObservation:
    state: str
    provider_receipt_present: bool
    reason_code: str
    evidence_hash: Optional[str] = None
    retention_until: Optional[str] = None

    def __post_init__(self) -> None:
        state = str(self.state or "").strip().lower()
        if state not in _ADAPTER_STATES:
            raise DataRightsExternalEffectReconciliationError("adapter state is unsupported")
        if not isinstance(self.provider_receipt_present, bool):
            raise DataRightsExternalEffectReconciliationError(
                "provider receipt presence must be boolean"
            )
        reason_code = str(self.reason_code or "").strip()
        if not _REASON_PATTERN.fullmatch(reason_code):
            raise DataRightsExternalEffectReconciliationError("reason code is invalid")
        evidence_hash = self.evidence_hash
        if evidence_hash is not None and not _HASH_PATTERN.fullmatch(str(evidence_hash)):
            raise DataRightsExternalEffectReconciliationError("evidence hash is invalid")
        if state == "completed" and (
            not self.provider_receipt_present or evidence_hash is None
        ):
            raise DataRightsExternalEffectReconciliationError(
                "completed effects require a Provider-backed evidence digest"
            )
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "reason_code", reason_code)


class DataRightsExternalEffectAdapter(Protocol):
    def observe(
        self,
        *,
        domain: str,
        effect_identity_hash: str,
        attempt: int,
    ) -> DataRightsExternalEffectAdapterObservation:
        ...


class DataRightsExternalEffectReconciler:
    """Append redacted observations after the account access fence is durable."""

    def __init__(
        self,
        store: Any,
        *,
        domains: Sequence[str] = DATA_RIGHTS_EXTERNAL_EFFECT_RECONCILIATION_DOMAINS,
        max_attempts: int = 3,
    ) -> None:
        normalized_domains = tuple(str(domain) for domain in domains)
        if not normalized_domains or any(
            domain not in DATA_RIGHTS_EXTERNAL_EFFECT_RECONCILIATION_DOMAINS
            for domain in normalized_domains
        ):
            raise DataRightsExternalEffectReconciliationError(
                "reconciliation domains are invalid"
            )
        if len(set(normalized_domains)) != len(normalized_domains):
            raise DataRightsExternalEffectReconciliationError(
                "reconciliation domains must be unique"
            )
        if int(max_attempts) < 1:
            raise DataRightsExternalEffectReconciliationError(
                "max_attempts must be positive"
            )
        self._store = store
        self._domains = normalized_domains
        self._max_attempts = int(max_attempts)

    def reconcile(
        self,
        *,
        request_id: str,
        access_revocation_status: str,
        adapter: DataRightsExternalEffectAdapter,
        now: Optional[Any] = None,
    ) -> Dict[str, Any]:
        request, owner_subject_hash = self._request(request_id)
        if str(access_revocation_status or "").strip() != "revoked":
            return {
                "schemaVersion": DATA_RIGHTS_EXTERNAL_EFFECT_RECONCILER_SCHEMA_VERSION,
                "requestId": str(request.get("id") or request_id),
                "status": "blockedAccessNotRevoked",
                "accessState": "notConfirmed",
                "domains": [],
            }

        observed_at = _timestamp(now)
        results = []
        for domain in self._domains:
            results.append(
                self._reconcile_domain(
                    request_id=str(request.get("id") or request_id),
                    owner_subject_hash=owner_subject_hash,
                    domain=domain,
                    adapter=adapter,
                    observed_at=observed_at,
                )
            )
        return {
            "schemaVersion": DATA_RIGHTS_EXTERNAL_EFFECT_RECONCILER_SCHEMA_VERSION,
            "requestId": str(request.get("id") or request_id),
            "status": _aggregate_status(results),
            "accessState": "revoked",
            "requiresManualReview": any(
                bool(item.get("requiresManualReview")) for item in results
            ),
            "domains": results,
        }

    def record_manual_resolution(
        self,
        *,
        request_id: str,
        domain: str,
        state: str,
        provider_receipt_present: bool,
        reason_code: str,
        evidence_hash: Optional[str],
        observed_at: Any,
    ) -> Dict[str, Any]:
        request, owner_subject_hash = self._request(request_id)
        normalized_domain = str(domain or "").strip()
        if normalized_domain not in self._domains:
            raise DataRightsExternalEffectReconciliationError("manual domain is invalid")
        observation = DataRightsExternalEffectAdapterObservation(
            state=state,
            provider_receipt_present=provider_receipt_present,
            reason_code=reason_code,
            evidence_hash=evidence_hash,
        )
        if observation.state not in {"completed", "failed", "unsupported"}:
            raise DataRightsExternalEffectReconciliationError(
                "manual resolution must be terminal"
            )
        return self._record(
            request_id=str(request.get("id") or request_id),
            owner_subject_hash=owner_subject_hash,
            domain=normalized_domain,
            observation=observation,
            observed_at=_timestamp(observed_at),
            requires_manual_review=observation.state == "failed",
        )

    def _reconcile_domain(
        self,
        *,
        request_id: str,
        owner_subject_hash: str,
        domain: str,
        adapter: DataRightsExternalEffectAdapter,
        observed_at: str,
    ) -> Dict[str, Any]:
        effect_identity_hash = _effect_identity_hash(request_id, domain)
        history = self._effect_history(request_id, effect_identity_hash)
        latest = history[-1] if history else None
        if latest is not None and _observation_state(latest) == "completed":
            return _result(
                domain=domain,
                state="completed",
                outcome="alreadyCompleted",
                reason_code=_observation_reason(latest) or "externalEffectProviderCompleted",
                attempt=_retry_attempt_count(history),
            )
        if latest is not None and _MANUAL_REVIEW_REASON in _observation_reason_codes(latest):
            return _result(
                domain=domain,
                state="failed",
                outcome="manualReviewPending",
                reason_code=_MANUAL_REVIEW_REASON,
                attempt=_retry_attempt_count(history),
                requires_manual_review=True,
            )

        # The account-delete boundary records unsupported/pending facts before
        # any Provider query occurs. Those facts must not consume the adapter
        # retry budget.
        attempt = _retry_attempt_count(history) + 1
        try:
            observation = adapter.observe(
                domain=domain,
                effect_identity_hash=effect_identity_hash,
                attempt=attempt,
            )
            if not isinstance(observation, DataRightsExternalEffectAdapterObservation):
                raise DataRightsExternalEffectReconciliationError(
                    "adapter returned an invalid observation"
                )
        except TimeoutError:
            observation = _system_failure_observation(
                request_id=request_id,
                domain=domain,
                attempt=attempt,
                state="unknown",
                reason_code="externalEffectProviderTimeout",
            )
        except Exception:
            observation = _system_failure_observation(
                request_id=request_id,
                domain=domain,
                attempt=attempt,
                state="failed",
                reason_code="externalEffectProviderUnavailable",
            )

        recorded = self._record(
            request_id=request_id,
            owner_subject_hash=owner_subject_hash,
            domain=domain,
            observation=observation,
            observed_at=observed_at,
            attempt=attempt,
        )
        if observation.state in {"failed", "unknown"} and attempt >= self._max_attempts:
            dead_letter = DataRightsExternalEffectAdapterObservation(
                state="failed",
                provider_receipt_present=False,
                reason_code=_MANUAL_REVIEW_REASON,
                evidence_hash=_system_evidence_hash(
                    request_id,
                    domain,
                    attempt,
                    _MANUAL_REVIEW_REASON,
                ),
            )
            return self._record(
                request_id=request_id,
                owner_subject_hash=owner_subject_hash,
                domain=domain,
                observation=dead_letter,
                observed_at=_timestamp_after(observed_at),
                attempt=attempt,
                requires_manual_review=True,
            )
        return recorded

    def _record(
        self,
        *,
        request_id: str,
        owner_subject_hash: str,
        domain: str,
        observation: DataRightsExternalEffectAdapterObservation,
        observed_at: str,
        attempt: Optional[int] = None,
        requires_manual_review: bool = False,
    ) -> Dict[str, Any]:
        receipt = DataRightsExternalEffectReceipt(
            request_id=request_id,
            owner_subject_hash=owner_subject_hash,
            domain=domain,
            effect_identity_hash=_effect_identity_hash(request_id, domain),
            state=observation.state,
            provider_receipt_present=observation.provider_receipt_present,
            reason_code=observation.reason_code,
            observed_at=observed_at,
            evidence_hash=observation.evidence_hash,
            retention_until=observation.retention_until,
        )
        persisted = self._store.record_rights_external_effect_receipt(receipt)
        history = self._effect_history(request_id, receipt.effect_identity_hash)
        return _result(
            domain=domain,
            state=observation.state,
            outcome=str(persisted.get("outcome") or "observed"),
            reason_code=observation.reason_code,
            attempt=(
                int(attempt)
                if attempt is not None
                else _retry_attempt_count(history)
            ),
            requires_manual_review=requires_manual_review,
        )

    def _request(self, request_id: str) -> tuple[Mapping[str, Any], str]:
        normalized_request_id = str(request_id or "").strip()
        summary = self._store.summarize_rights_request(normalized_request_id)
        request = summary.get("request") if isinstance(summary, Mapping) else None
        if not isinstance(request, Mapping):
            raise DataRightsExternalEffectReconciliationError(
                "data-rights request does not exist"
            )
        owner_subject_hash = str(request.get("subjectHash") or "").strip()
        if not _HASH_PATTERN.fullmatch(owner_subject_hash):
            raise DataRightsExternalEffectReconciliationError(
                "data-rights request owner binding is invalid"
            )
        return request, owner_subject_hash

    def _effect_history(
        self,
        request_id: str,
        effect_identity_hash: str,
    ) -> list[Mapping[str, Any]]:
        observations = self._store.list_rights_external_effect_receipts(request_id)
        matching = [
            item
            for item in observations
            if isinstance(item, DataRightsExternalEffectObservation)
            and item.effect_identity_hash == effect_identity_hash
        ]
        return sorted(matching, key=lambda item: item.observed_at)


def _result(
    *,
    domain: str,
    state: str,
    outcome: str,
    reason_code: str,
    attempt: int,
    requires_manual_review: bool = False,
) -> Dict[str, Any]:
    return {
        "domain": domain,
        "state": state,
        "outcome": outcome,
        "reasonCode": reason_code,
        "attempt": max(0, int(attempt)),
        "requiresManualReview": requires_manual_review,
    }


def _aggregate_status(results: Sequence[Mapping[str, Any]]) -> str:
    if any(bool(item.get("requiresManualReview")) for item in results):
        return "manualReviewRequired"
    states = {str(item.get("state") or "unknown") for item in results}
    if states == {"completed"}:
        return "completed"
    if "failed" in states or "unknown" in states:
        return "attentionRequired"
    if "pending" in states:
        return "pending"
    if states == {"unsupported"}:
        return "unsupported"
    return "partial"


def _effect_identity_hash(request_id: str, domain: str) -> str:
    return sha256(f"{request_id}:accountDelete:{domain}".encode("utf-8")).hexdigest()


def _system_failure_observation(
    *,
    request_id: str,
    domain: str,
    attempt: int,
    state: str,
    reason_code: str,
) -> DataRightsExternalEffectAdapterObservation:
    return DataRightsExternalEffectAdapterObservation(
        state=state,
        provider_receipt_present=False,
        reason_code=reason_code,
        evidence_hash=_system_evidence_hash(request_id, domain, attempt, reason_code),
    )


def _system_evidence_hash(
    request_id: str,
    domain: str,
    attempt: int,
    reason_code: str,
) -> str:
    return sha256(
        f"{request_id}:{domain}:{attempt}:{reason_code}".encode("utf-8")
    ).hexdigest()


def _observation_state(observation: Mapping[str, Any]) -> str:
    return str(observation.get("state") or "unknown")


def _observation_reason_codes(observation: Mapping[str, Any]) -> list[str]:
    values = observation.get("reasonCodes")
    return [str(value) for value in values] if isinstance(values, list) else []


def _observation_reason(observation: Mapping[str, Any]) -> str:
    values = _observation_reason_codes(observation)
    return values[-1] if values else ""


def _retry_attempt_count(history: Sequence[Mapping[str, Any]]) -> int:
    return sum(
        1
        for observation in history
        if _observation_reason(observation) not in _BOUNDARY_REASON_CODES
        and _MANUAL_REVIEW_REASON not in _observation_reason_codes(observation)
    )


def _timestamp_after(value: Any) -> str:
    parsed = datetime.fromisoformat(_timestamp(value))
    return (parsed + timedelta(microseconds=1)).isoformat()


def _timestamp(value: Optional[Any]) -> str:
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise DataRightsExternalEffectReconciliationError(
                "observed_at must be ISO-8601"
            ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DataRightsExternalEffectReconciliationError(
            "observed_at must include timezone"
        )
    return parsed.astimezone(timezone.utc).isoformat()


__all__ = [
    "DATA_RIGHTS_EXTERNAL_EFFECT_RECONCILER_SCHEMA_VERSION",
    "DATA_RIGHTS_EXTERNAL_EFFECT_RECONCILIATION_DOMAINS",
    "DataRightsExternalEffectAdapter",
    "DataRightsExternalEffectAdapterObservation",
    "DataRightsExternalEffectReconciliationError",
    "DataRightsExternalEffectReconciler",
]
