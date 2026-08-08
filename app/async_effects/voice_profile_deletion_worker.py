"""Default-off provider cleanup worker for locally revoked VoiceProfiles.

Deleting a VoiceProfile immediately fences all synthesis in the API request.
This worker is deliberately separate: it consumes only the accepted deletion
effect, obtains a value-free provider observation when an adapter supports
one, and never represents a local tombstone as provider-side deletion.

The current VolcEngine clone adapter reports ``unsupported`` because the
configured train/query contract has no reviewed deletion endpoint.  That is a
terminal partial outcome, not a retryable synthetic success.  A future
provider adapter can implement the two narrow methods used here without
changing the mobile contract.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import socket
from typing import Any, Mapping, Optional

from app.async_effects.contracts import AsyncEffectIntent, is_async_effect_store_ready, resolve_async_effect_runtime_status
from app.async_effects.lease_repository import (
    AsyncEffectJobLease,
    AsyncEffectLeaseCancelled,
    AsyncEffectLeaseLost,
)
from app.async_effects.provider_effects import (
    ProviderEffectReconciliation,
    ProviderEffectQueryOutcome,
    ProviderEffectReceipt,
    ProviderEffectState,
)
from app.core.config import Settings
from app.services.store_factory import close_store, make_store, open_store
from app.services.voice_clone import (
    VoiceCloneProfileDeletionDisposition,
    VoiceCloneProfileDeletionObservation,
    VoiceCloneProviderFactory,
)
from app.services.voice_profile_deletion_effects import (
    VOICE_PROFILE_DELETION_EVENT_TYPE,
    VOICE_PROFILE_DELETION_JOB_TYPE,
    VOICE_PROFILE_DELETION_OPERATION_TYPE,
    VOICE_PROFILE_PROVIDER_DELETE_RECEIPT_SCHEMA_VERSION,
    build_voice_profile_deletion_provider_effect_intent,
)
from app.services.voice_profile_lifecycle import (
    VoiceProfileLifecycleState,
    apply_voice_profile_lifecycle,
    canonical_lifecycle_state,
)


_DEFAULT_LEASE_SECONDS = 120
_DISPATCH_STARTED_REASON = "providerVoiceDeletionDispatchStarted"


class VoiceProfileDeletionWorkerError(RuntimeError):
    """The worker cannot safely apply a provider cleanup observation."""


@dataclass(frozen=True)
class _DeletionDispatch:
    lease: AsyncEffectJobLease
    intent: AsyncEffectIntent
    user_id: str
    voice_profile_id: str
    provider_speaker_id: str
    expected_profile_version: int
    prior_unknown: ProviderEffectReceipt
    mode: str


class VoiceProfileDeletionWorkerRuntime:
    """Claim one VoiceProfile deletion and reconcile its provider evidence."""

    def __init__(
        self,
        *,
        settings: Settings,
        store: Any,
        worker_id: Optional[str] = None,
        lease_seconds: int = _DEFAULT_LEASE_SECONDS,
        provider: Any | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._worker_id = str(
            worker_id or f"voice-profile-deletion-worker-{socket.gethostname()}"
        )
        self._lease_seconds = max(1, int(lease_seconds))
        self._provider = provider or VoiceCloneProviderFactory(settings).make()

    def run_once(self) -> dict[str, Any]:
        block_reason = self._runtime_block_reason()
        if block_reason is not None:
            return self._payload(status="blocked", reason=block_reason)
        store_reason = self._worker_store_block_reason()
        if store_reason is not None:
            return self._payload(status="blocked", reason=store_reason)

        lease = self._claim_next()
        if lease is None:
            return self._payload(status="idle", reason="noEligibleVoiceProfileDeletionJob")

        try:
            prepared = self._prepare_dispatch(lease)
            if isinstance(prepared, dict):
                return prepared
            observation = self._provider_observation(prepared)
            return self._finalize_dispatch(prepared, observation)
        except AsyncEffectLeaseCancelled:
            return self._payload(status="cancelled", reason="voiceProfileDeletionCancelled", lease=lease)
        except AsyncEffectLeaseLost:
            return self._payload(status="lost", reason="voiceProfileDeletionLeaseLost", lease=lease)
        except VoiceProfileDeletionWorkerError as exc:
            return self._block_lease(lease, reason=self._safe_reason(exc))
        except Exception:
            # Third-party exceptions can contain raw speaker IDs, request IDs,
            # or response bodies.  Keep only a stable local error code.
            return self._block_lease(lease, reason="voiceProfileDeletionWorkerUnexpectedFailure")

    def _prepare_dispatch(self, lease: AsyncEffectJobLease) -> _DeletionDispatch | dict[str, Any]:
        with self._unit_of_work(
            correlation_id=f"voice-profile-deletion-worker-prepare-{lease.job_id}",
            command_id=f"voiceProfileDeletionWorkerPrepare:{lease.operation_id}",
        ):
            intent = self._store.async_effect_lease_repository().load_intent(lease)
            self._assert_typed_intent(intent)
            profile = self._load_bound_profile(intent)
            user_id = str(profile.get("userId") or "").strip()
            with self._store.auth_user_operation(user_id):
                profile = self._load_bound_profile(intent)
                state = canonical_lifecycle_state(profile)
                receipt = self._receipt(profile)
                if state is VoiceProfileLifecycleState.DELETED and receipt["state"] == "completed":
                    completion = self._store.async_effect_lease_repository().complete(
                        lease,
                        outcome="succeeded",
                    )
                    return self._payload(
                        status="completed",
                        reason="voiceProfileDeletionAlreadyCompleted",
                        lease=lease,
                        completion=completion,
                    )
                if state is not VoiceProfileLifecycleState.DELETING:
                    raise VoiceProfileDeletionWorkerError("voiceProfileDeletionLifecycleMismatch")

                provider_intent = build_voice_profile_deletion_provider_effect_intent(
                    user_id=user_id,
                    profile=profile,
                    authority_epoch=int(intent.target.authority_epoch),
                )
                if provider_intent.effect_intent.immutable_fingerprint() != intent.immutable_fingerprint():
                    raise VoiceProfileDeletionWorkerError("voiceProfileDeletionIntentMismatch")
                if str(receipt.get("providerEffectKey") or "") != provider_intent.provider_effect_key:
                    raise VoiceProfileDeletionWorkerError("voiceProfileDeletionEffectBindingMismatch")

                expected_profile_version = self._profile_version(profile)
                if receipt["state"] == "accepted":
                    # Persist uncertainty before any provider call.  A crash
                    # after dispatch therefore resolves through query/manual
                    # review instead of issuing an unsafe duplicate delete.
                    prior_unknown = ProviderEffectReceipt(
                        intent=provider_intent,
                        state=ProviderEffectState.UNKNOWN,
                        reason_code=_DISPATCH_STARTED_REASON,
                        observation_origin="workerDispatch",
                    )
                    self._store.provider_effect_repository().record(prior_unknown)
                    updated = self._with_receipt(
                        profile,
                        state="unknown",
                        provider_receipt_present=False,
                        reason_code=_DISPATCH_STARTED_REASON,
                        receipt_hash=prior_unknown.storage_receipt_hash,
                    )
                    saved = self._save_if_current(
                        user_id=user_id,
                        profile=updated,
                        expected_profile_version=expected_profile_version,
                    )
                    return self._dispatch_from_profile(
                        lease=lease,
                        intent=intent,
                        profile=saved,
                        prior_unknown=prior_unknown,
                        mode="request",
                    )

                if receipt["state"] == "unknown" and receipt["reasonCode"] == _DISPATCH_STARTED_REASON:
                    prior_unknown = ProviderEffectReceipt(
                        intent=provider_intent,
                        state=ProviderEffectState.UNKNOWN,
                        reason_code=_DISPATCH_STARTED_REASON,
                        observation_origin="workerDispatch",
                    )
                    if str(receipt.get("receiptHash") or "") != prior_unknown.storage_receipt_hash:
                        raise VoiceProfileDeletionWorkerError("voiceProfileDeletionUnknownReceiptMismatch")
                    return self._dispatch_from_profile(
                        lease=lease,
                        intent=intent,
                        profile=profile,
                        prior_unknown=prior_unknown,
                        mode="query",
                    )

                completion = self._store.async_effect_lease_repository().complete(
                    lease,
                    outcome="blocked",
                    error_code="voiceProfileDeletionManualReview",
                )
                return self._payload(
                    status="blocked",
                    reason="voiceProfileDeletionManualReview",
                    lease=lease,
                    completion=completion,
                )

    def _provider_observation(
        self,
        dispatch: _DeletionDispatch,
    ) -> VoiceCloneProfileDeletionObservation:
        method_name = (
            "request_profile_deletion"
            if dispatch.mode == "request"
            else "query_profile_deletion"
        )
        method = getattr(self._provider, method_name, None)
        if not callable(method):
            return VoiceCloneProfileDeletionObservation.unsupported(
                provider_mode=str(getattr(self._provider, "provider_mode", "unknown")),
            )
        observation = method(
            voice_profile_id=dispatch.provider_speaker_id,
            provider_request_id=dispatch.prior_unknown.intent.provider_request_id,
        )
        if not isinstance(observation, VoiceCloneProfileDeletionObservation):
            raise VoiceProfileDeletionWorkerError("voiceProfileDeletionProviderObservationInvalid")
        return observation

    def _finalize_dispatch(
        self,
        dispatch: _DeletionDispatch,
        observation: VoiceCloneProfileDeletionObservation,
    ) -> dict[str, Any]:
        with self._unit_of_work(
            correlation_id=f"voice-profile-deletion-worker-finalize-{dispatch.lease.job_id}",
            command_id=f"voiceProfileDeletionWorkerFinalize:{dispatch.lease.operation_id}",
        ):
            self._store.async_effect_lease_repository().load_intent(dispatch.lease)
            profile = self._load_bound_profile(dispatch.intent)
            with self._store.auth_user_operation(dispatch.user_id):
                profile = self._load_bound_profile(dispatch.intent)
                if self._profile_version(profile) != dispatch.expected_profile_version:
                    raise VoiceProfileDeletionWorkerError("voiceProfileDeletionProfileVersionChanged")
                now = datetime.now(timezone.utc)
                if observation.disposition is VoiceCloneProfileDeletionDisposition.COMPLETED:
                    summary = self._reconcile(
                        dispatch,
                        outcome="completed",
                        provider_receipt_hash=observation.provider_receipt_hash,
                    )
                    updated = apply_voice_profile_lifecycle(
                        profile,
                        state=VoiceProfileLifecycleState.DELETED,
                        now=now,
                    )
                    updated["deletedAt"] = now.isoformat()
                    updated["deletionState"] = "deleted"
                    updated["deletionRetryable"] = False
                    updated = self._with_receipt(
                        updated,
                        state="completed",
                        provider_receipt_present=True,
                        reason_code=observation.reason_code,
                        receipt_hash=summary.receipt_hash,
                    )
                    saved = self._save_if_current(
                        user_id=dispatch.user_id,
                        profile=updated,
                        expected_profile_version=dispatch.expected_profile_version,
                    )
                    completion = self._store.async_effect_lease_repository().complete(
                        dispatch.lease,
                        outcome="succeeded",
                    )
                    return self._payload(
                        status="completed",
                        reason=observation.reason_code,
                        lease=dispatch.lease,
                        completion=completion,
                        profile=saved,
                    )

                if observation.disposition is VoiceCloneProfileDeletionDisposition.FAILED:
                    summary = self._reconcile(
                        dispatch,
                        outcome="failed",
                        provider_receipt_hash=observation.provider_receipt_hash,
                    )
                    state = "failed"
                    retryable = True
                elif observation.disposition is VoiceCloneProfileDeletionDisposition.UNSUPPORTED:
                    summary = None
                    state = "unsupported"
                    retryable = False
                else:
                    summary = None
                    state = "partial"
                    retryable = True

                updated = apply_voice_profile_lifecycle(
                    profile,
                    state=VoiceProfileLifecycleState.DELETING,
                    now=now,
                )
                updated["deletionState"] = state
                updated["deletionRetryable"] = retryable
                updated = self._with_receipt(
                    updated,
                    state="failed" if summary is not None else "unknown",
                    provider_receipt_present=bool(summary is not None and observation.provider_receipt_present),
                    reason_code=observation.reason_code,
                    receipt_hash=(summary.receipt_hash if summary is not None else dispatch.prior_unknown.storage_receipt_hash),
                )
                saved = self._save_if_current(
                    user_id=dispatch.user_id,
                    profile=updated,
                    expected_profile_version=dispatch.expected_profile_version,
                )
                completion = self._store.async_effect_lease_repository().complete(
                    dispatch.lease,
                    outcome="blocked",
                    error_code="voiceProfileDeletionManualReview",
                )
                return self._payload(
                    status="blocked",
                    reason=observation.reason_code,
                    lease=dispatch.lease,
                    completion=completion,
                    profile=saved,
                )

    def _reconcile(
        self,
        dispatch: _DeletionDispatch,
        *,
        outcome: str,
        provider_receipt_hash: Optional[str],
    ) -> Any:
        if not provider_receipt_hash:
            raise VoiceProfileDeletionWorkerError("voiceProfileDeletionProviderReceiptRequired")
        reconciliation = ProviderEffectReconciliation(
            prior_unknown=dispatch.prior_unknown,
            outcome=(
                ProviderEffectQueryOutcome.COMPLETED
                if outcome == "completed"
                else ProviderEffectQueryOutcome.FAILED
            ),
            query_receipt_hash=provider_receipt_hash,
        )
        return self._store.provider_effect_repository().reconcile(reconciliation)

    def _dispatch_from_profile(
        self,
        *,
        lease: AsyncEffectJobLease,
        intent: AsyncEffectIntent,
        profile: Mapping[str, Any],
        prior_unknown: ProviderEffectReceipt,
        mode: str,
    ) -> _DeletionDispatch:
        return _DeletionDispatch(
            lease=lease,
            intent=intent,
            user_id=str(profile.get("userId") or "").strip(),
            voice_profile_id=str(profile.get("voiceProfileId") or "").strip(),
            provider_speaker_id=str(
                profile.get("providerSpeakerId") or profile.get("voiceProfileId") or ""
            ).strip(),
            expected_profile_version=self._profile_version(profile),
            prior_unknown=prior_unknown,
            mode=mode,
        )

    def _load_bound_profile(self, intent: AsyncEffectIntent) -> dict[str, Any]:
        finder = getattr(self._store, "find_voice_profile_by_deletion_operation", None)
        if not callable(finder):
            raise VoiceProfileDeletionWorkerError("voiceProfileDeletionWorkerStoreUnsupported")
        profile = finder(intent.operation_id)
        if not isinstance(profile, Mapping):
            raise VoiceProfileDeletionWorkerError("voiceProfileDeletionProfileMissing")
        owner = str(profile.get("userId") or "").strip()
        if owner != intent.target.owner_subject_id:
            raise VoiceProfileDeletionWorkerError("voiceProfileDeletionOwnerMismatch")
        if not str(profile.get("voiceProfileId") or "").strip():
            raise VoiceProfileDeletionWorkerError("voiceProfileDeletionProfileIdMissing")
        receipt = self._receipt(profile)
        if str(receipt.get("operationId") or "") != intent.operation_id:
            raise VoiceProfileDeletionWorkerError("voiceProfileDeletionOperationBindingMismatch")
        if self._profile_version(profile) != int(intent.target.resource_version):
            raise VoiceProfileDeletionWorkerError("voiceProfileDeletionProfileVersionMismatch")
        return dict(profile)

    @staticmethod
    def _receipt(profile: Mapping[str, Any]) -> dict[str, Any]:
        raw = profile.get("providerEffectReceipt")
        if not isinstance(raw, Mapping):
            raise VoiceProfileDeletionWorkerError("voiceProfileDeletionReceiptMissing")
        receipt = dict(raw)
        state = str(receipt.get("state") or "").strip().lower()
        if state not in {"accepted", "unknown", "completed", "failed"}:
            raise VoiceProfileDeletionWorkerError("voiceProfileDeletionReceiptStateInvalid")
        receipt["state"] = state
        receipt["reasonCode"] = str(receipt.get("reasonCode") or "").strip()
        return receipt

    @staticmethod
    def _profile_version(profile: Mapping[str, Any]) -> int:
        try:
            value = int(profile.get("profileVersion") or 0)
        except (TypeError, ValueError) as exc:
            raise VoiceProfileDeletionWorkerError("voiceProfileDeletionProfileVersionInvalid") from exc
        if value < 1:
            raise VoiceProfileDeletionWorkerError("voiceProfileDeletionProfileVersionInvalid")
        return value

    @staticmethod
    def _with_receipt(
        profile: Mapping[str, Any],
        *,
        state: str,
        provider_receipt_present: bool,
        reason_code: str,
        receipt_hash: str,
    ) -> dict[str, Any]:
        updated = dict(profile)
        existing = updated.get("providerEffectReceipt")
        receipt = dict(existing) if isinstance(existing, Mapping) else {}
        receipt.update(
            {
                "schemaVersion": VOICE_PROFILE_PROVIDER_DELETE_RECEIPT_SCHEMA_VERSION,
                "state": state,
                "providerReceiptPresent": provider_receipt_present,
                "reasonCode": reason_code,
                "recordedAt": datetime.now(timezone.utc).isoformat(),
                "receiptHash": receipt_hash,
            }
        )
        updated["providerEffectReceipt"] = receipt
        return updated

    def _save_if_current(
        self,
        *,
        user_id: str,
        profile: Mapping[str, Any],
        expected_profile_version: int,
    ) -> dict[str, Any]:
        saved = self._store.save_voice_profile_if_version(
            user_id,
            dict(profile),
            expected_profile_version=expected_profile_version,
        )
        if not isinstance(saved, Mapping):
            raise VoiceProfileDeletionWorkerError("voiceProfileDeletionProfileVersionChanged")
        return dict(saved)

    @staticmethod
    def _assert_typed_intent(intent: AsyncEffectIntent) -> None:
        target = intent.target
        if (
            intent.operation_type != VOICE_PROFILE_DELETION_OPERATION_TYPE
            or intent.event_type != VOICE_PROFILE_DELETION_EVENT_TYPE
            or intent.job_type != VOICE_PROFILE_DELETION_JOB_TYPE
            or target.resource_type != "voiceProfile"
            or target.purpose != "privateVoiceDeletion"
        ):
            raise VoiceProfileDeletionWorkerError("voiceProfileDeletionIntentTypeMismatch")

    def _claim_next(self) -> AsyncEffectJobLease | None:
        with self._unit_of_work(
            correlation_id="voice-profile-deletion-worker-claim",
            command_id="voiceProfileDeletionWorkerClaim",
        ):
            return self._store.async_effect_lease_repository().claim_next(
                worker_id=self._worker_id,
                lease_seconds=self._lease_seconds,
                supported_job_types=[VOICE_PROFILE_DELETION_JOB_TYPE],
            )

    def _block_lease(self, lease: AsyncEffectJobLease, *, reason: str) -> dict[str, Any]:
        try:
            with self._unit_of_work(
                correlation_id=f"voice-profile-deletion-worker-block-{lease.job_id}",
                command_id=f"voiceProfileDeletionWorkerBlock:{lease.operation_id}",
            ):
                completion = self._store.async_effect_lease_repository().complete(
                    lease,
                    outcome="blocked",
                    error_code=reason,
                )
                return self._payload(
                    status="blocked",
                    reason=reason,
                    lease=lease,
                    completion=completion,
                )
        except (AsyncEffectLeaseCancelled, AsyncEffectLeaseLost):
            raise
        except Exception:
            return self._payload(
                status="failed",
                reason="voiceProfileDeletionWorkerOutcomePersistenceFailed",
                lease=lease,
            )

    def _runtime_block_reason(self) -> str | None:
        runtime = resolve_async_effect_runtime_status(
            async_effect_v1_enabled=self._settings.async_effect_v1_enabled,
            worker_enabled=self._settings.async_effect_worker_enabled,
            schema_ready=self._readiness(),
        )
        if not runtime.allowed:
            return runtime.reason
        if not self._settings.voice_clone_deletion_worker_enabled:
            return "voiceCloneDeletionWorkerDisabled"
        return None

    def _readiness(self) -> bool:
        probe = getattr(self._store, "readiness_probe", None)
        return callable(probe) and is_async_effect_store_ready(probe())

    def _worker_store_block_reason(self) -> str | None:
        required = [
            "request_unit_of_work",
            "auth_user_operation",
            "async_effect_lease_repository",
            "provider_effect_repository",
            "find_voice_profile_by_deletion_operation",
            "save_voice_profile_if_version",
        ]
        if not all(callable(getattr(self._store, name, None)) for name in required):
            return "voiceProfileDeletionWorkerStoreUnsupported"
        return None

    def _unit_of_work(self, *, correlation_id: str, command_id: str):
        return self._store.request_unit_of_work(
            correlation_id=correlation_id,
            command_id=command_id,
        )

    @staticmethod
    def _safe_reason(error: VoiceProfileDeletionWorkerError) -> str:
        reason = str(error or "").strip()
        if reason.startswith("voiceProfileDeletion") and len(reason) <= 128:
            return reason
        return "voiceProfileDeletionWorkerRejected"

    def _payload(
        self,
        *,
        status: str,
        reason: str,
        lease: AsyncEffectJobLease | None = None,
        completion: Any | None = None,
        profile: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "mode": "run",
            "status": status,
            "reason": reason,
            "workerId": self._worker_id,
        }
        if lease is not None:
            payload.update(
                {
                    "jobId": lease.job_id,
                    "operationId": lease.operation_id,
                    "attempt": lease.attempt,
                }
            )
        if completion is not None:
            payload.update(
                {
                    "jobState": completion.job_state,
                    "operationState": completion.operation_state,
                    "outboxState": completion.outbox_state,
                }
            )
        if profile is not None:
            payload.update(
                {
                    "lifecycleState": profile.get("lifecycleState"),
                    "deletionState": profile.get("deletionState"),
                    "deletionRetryable": bool(profile.get("deletionRetryable", False)),
                }
            )
        return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="DreamJourney default-disabled VoiceProfile deletion worker"
    )
    parser.add_argument("--once", action="store_true", help="claim and consume at most one deletion job")
    parser.add_argument("--worker-id", default=None, help="opaque worker identifier")
    parser.add_argument("--lease-seconds", type=int, default=_DEFAULT_LEASE_SECONDS)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _parser().parse_args(argv)
    settings = Settings.from_env()
    store = make_store(settings)
    open_store(store, wait=True)
    try:
        worker = VoiceProfileDeletionWorkerRuntime(
            settings=settings,
            store=store,
            worker_id=args.worker_id,
            lease_seconds=args.lease_seconds,
        )
        payload = worker.run_once()
        print(json.dumps(payload, sort_keys=True))
        return 0
    finally:
        close_store(store)


if __name__ == "__main__":  # pragma: no cover - exercised through CLI smoke
    raise SystemExit(main())
