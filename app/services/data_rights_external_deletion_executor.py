"""Provider-neutral execution boundary for account deletion side effects.

Access revocation remains authoritative and must be durable before this layer
is entered. Provider adapters receive only a one-way effect digest. Their raw
identifiers and errors never enter receipts, diagnostics, or user exports.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from hashlib import sha256
from threading import Lock, RLock
from typing import Any, Mapping, Protocol

from app.services.data_rights_external_effect_reconciler import (
    DATA_RIGHTS_EXTERNAL_EFFECT_RECONCILIATION_DOMAINS,
    DataRightsExternalEffectAdapterObservation,
    DataRightsExternalEffectReconciler,
)


DATA_RIGHTS_EXTERNAL_DELETION_EXECUTOR_SCHEMA_VERSION = 1


class ExternalDeletionProvider(Protocol):
    def delete(
        self,
        *,
        effect_identity_hash: str,
        attempt: int,
    ) -> DataRightsExternalEffectAdapterObservation:
        ...


@dataclass(frozen=True)
class UnsupportedExternalDeletionProvider:
    domain: str

    def delete(
        self,
        *,
        effect_identity_hash: str,
        attempt: int,
    ) -> DataRightsExternalEffectAdapterObservation:
        del effect_identity_hash, attempt
        return DataRightsExternalEffectAdapterObservation(
            state="unsupported",
            provider_receipt_present=False,
            reason_code=f"{self.domain}DeleteAdapterNotConfigured",
        )


class DataRightsExternalDeletionProviderRegistry:
    """Strict five-domain registry; missing adapters stay unsupported."""

    def __init__(
        self,
        providers: Mapping[str, ExternalDeletionProvider] | None = None,
        *,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._providers = dict(providers or {})
        invalid = set(self._providers).difference(
            DATA_RIGHTS_EXTERNAL_EFFECT_RECONCILIATION_DOMAINS
        )
        if invalid:
            raise ValueError("external deletion provider domain is invalid")
        self._timeout_seconds = max(0.01, float(timeout_seconds))

    def observe(
        self,
        *,
        domain: str,
        effect_identity_hash: str,
        attempt: int,
    ) -> DataRightsExternalEffectAdapterObservation:
        provider = self._providers.get(domain)
        if provider is None:
            provider = UnsupportedExternalDeletionProvider(domain=domain)
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rights-delete")
        future = pool.submit(
            provider.delete,
            effect_identity_hash=effect_identity_hash,
            attempt=attempt,
        )
        try:
            result = future.result(timeout=self._timeout_seconds)
        except FutureTimeoutError as exc:
            future.cancel()
            raise TimeoutError("external deletion provider timed out") from exc
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        if not isinstance(result, DataRightsExternalEffectAdapterObservation):
            raise TypeError("external deletion provider returned an invalid receipt")
        return result


class DataRightsExternalDeletionExecutor:
    """Serialize one request while delegating durable receipts to the reconciler."""

    def __init__(
        self,
        store: Any,
        *,
        providers: Mapping[str, ExternalDeletionProvider] | None = None,
        timeout_seconds: float = 10.0,
        max_attempts: int = 3,
    ) -> None:
        self._registry = DataRightsExternalDeletionProviderRegistry(
            providers,
            timeout_seconds=timeout_seconds,
        )
        self._reconciler = DataRightsExternalEffectReconciler(
            store,
            max_attempts=max_attempts,
        )
        self._locks: dict[str, RLock] = {}
        self._lock = Lock()

    def execute(
        self,
        *,
        request_id: str,
        access_revocation_status: str,
        now: Any = None,
    ) -> dict[str, Any]:
        normalized_request = str(request_id or "").strip()
        if not normalized_request:
            raise ValueError("request_id is required")
        with self._request_lock(normalized_request):
            result = self._reconciler.reconcile(
                request_id=normalized_request,
                access_revocation_status=access_revocation_status,
                adapter=self._registry,
                now=now,
            )
        public_status = _public_status(result)
        return {
            "schemaVersion": DATA_RIGHTS_EXTERNAL_DELETION_EXECUTOR_SCHEMA_VERSION,
            "requestId": normalized_request,
            "status": public_status,
            "accessState": result.get("accessState"),
            "requiresManualReview": bool(result.get("requiresManualReview")),
            "domains": list(result.get("domains") or []),
            "executionDigest": sha256(
                f"{normalized_request}:{public_status}".encode("utf-8")
            ).hexdigest(),
        }

    def _request_lock(self, request_id: str) -> RLock:
        with self._lock:
            return self._locks.setdefault(request_id, RLock())


def _public_status(result: Mapping[str, Any]) -> str:
    internal = str(result.get("status") or "")
    if internal == "completed":
        return "completed"
    if internal == "unsupported":
        return "unsupported"
    if internal in {"attentionRequired", "manualReviewRequired"}:
        return "unknown"
    return "partial"


__all__ = [
    "DATA_RIGHTS_EXTERNAL_DELETION_EXECUTOR_SCHEMA_VERSION",
    "DataRightsExternalDeletionExecutor",
    "DataRightsExternalDeletionProviderRegistry",
    "ExternalDeletionProvider",
    "UnsupportedExternalDeletionProvider",
]
