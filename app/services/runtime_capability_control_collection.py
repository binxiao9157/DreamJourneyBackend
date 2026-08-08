"""Collect bounded operational evidence for controlled runtime capabilities."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import socket
from typing import Any, Iterable, Optional, Tuple

from app.async_effects.contracts import (
    is_async_effect_store_ready,
    resolve_async_effect_runtime_status,
)
from app.async_effects.readiness_evidence import (
    AsyncEffectWorkerReadinessEvidence,
    build_async_effect_worker_readiness_evidence,
)
from app.core.config import Settings
from app.services.provider_runtime import ProviderRuntimeInventory, ProviderRuntimeStatus
from app.services.owner_truth_media_processing import (
    OWNER_TRUTH_MEDIA_PROCESSING_JOB_TYPE,
)
from app.services.release_policy import parse_release_policy_feature_set
from app.services.runtime_capability_control import (
    RuntimeCapabilityBudgetState,
    RuntimeCapabilityControlObservation,
)


class RuntimeCapabilityControlCollector:
    """Read current scanner, worker and reconciliation health without payloads."""

    def __init__(self, *, settings: Settings, store: Any) -> None:
        self._settings = settings
        self._store = store

    def collect(
        self,
        *,
        now: Optional[datetime] = None,
        provider_inventory: Optional[ProviderRuntimeInventory] = None,
    ) -> Tuple[ProviderRuntimeInventory, tuple[RuntimeCapabilityControlObservation, ...]]:
        instant = self._utc(now or datetime.now(timezone.utc))
        expires_at = instant + timedelta(
            seconds=max(30, self._settings.runtime_capability_readiness_ttl_seconds)
        )
        inventory = provider_inventory or ProviderRuntimeInventory(
            self._settings,
            validated_at_startup=True,
        )
        storage = inventory.status_for("ownerTruthMediaStorage")
        processing = inventory.status_for("ownerTruthMediaProcessing")
        emergency_features = parse_release_policy_feature_set(
            self._settings.release_policy_emergency_disabled_features
        )
        reconciliation_healthy = self._deletion_reconciliation_healthy(
            required=storage.provider_ready or processing.provider_ready,
        )
        worker_evidence, open_dead_letter_count = self._worker_evidence(
            now=instant,
            expires_at=expires_at,
            required=processing.provider_ready,
        )

        observations = (
            self._observation(
                status=storage,
                observed_at=instant,
                expires_at=expires_at,
                scanner_ready=self._scanner_ready(storage),
                worker_evidence=None,
                open_dead_letter_count=None,
                deletion_reconciliation_healthy=reconciliation_healthy,
                kill_switch_active="ownerMediaCaptureV1" in emergency_features,
            ),
            self._observation(
                status=processing,
                observed_at=instant,
                expires_at=expires_at,
                scanner_ready=self._scanner_ready(storage),
                worker_evidence=worker_evidence,
                open_dead_letter_count=open_dead_letter_count,
                deletion_reconciliation_healthy=reconciliation_healthy,
                kill_switch_active="ownerMediaProcessingV1" in emergency_features,
            ),
        )
        return inventory, observations

    def _worker_evidence(
        self,
        *,
        now: datetime,
        expires_at: datetime,
        required: bool,
    ) -> tuple[Optional[AsyncEffectWorkerReadinessEvidence], Optional[int]]:
        if not required:
            return None, None
        runtime_status = resolve_async_effect_runtime_status(
            async_effect_v1_enabled=self._settings.async_effect_v1_enabled,
            worker_enabled=self._settings.async_effect_worker_enabled,
            schema_ready=self._store_schema_ready(),
        )
        repository_factory = getattr(self._store, "async_effect_lease_repository", None)
        dead_letter_factory = getattr(
            self._store,
            "async_effect_dead_letter_repository",
            None,
        )
        request_uow = getattr(self._store, "request_unit_of_work", None)
        if not all(
            callable(value)
            for value in (repository_factory, dead_letter_factory, request_uow)
        ):
            return (
                build_async_effect_worker_readiness_evidence(
                    runtime_status=runtime_status,
                    worker_id=self._worker_id(),
                    previews=(),
                    runnable_handler_count=1,
                    observed_at=now,
                    expires_at=expires_at,
                    store_supported=False,
                ),
                None,
            )

        limit = min(100, self._settings.runtime_capability_backlog_limit + 1)
        try:
            with request_uow(
                correlation_id="runtime-capability-readiness",
                command_id="runtimeCapabilityReadiness",
            ):
                previews = repository_factory().preview_eligible(
                    limit=max(1, limit),
                    job_types=[OWNER_TRUTH_MEDIA_PROCESSING_JOB_TYPE],
                )
                dead_letter_repository = dead_letter_factory()
                count_open = getattr(dead_letter_repository, "count_open", None)
                if not callable(count_open):
                    raise RuntimeError("dead-letter aggregate is unavailable")
                open_dead_letter_count = int(
                    count_open(job_type=OWNER_TRUTH_MEDIA_PROCESSING_JOB_TYPE)
                )
        except Exception:
            return (
                build_async_effect_worker_readiness_evidence(
                    runtime_status=runtime_status,
                    worker_id=self._worker_id(),
                    previews=(),
                    runnable_handler_count=1,
                    observed_at=now,
                    expires_at=expires_at,
                    collection_error_code="runtimeCapabilityWorkerObservationFailed",
                ),
                None,
            )
        return (
            build_async_effect_worker_readiness_evidence(
                runtime_status=runtime_status,
                worker_id=self._worker_id(),
                previews=previews,
                runnable_handler_count=1,
                observed_at=now,
                expires_at=expires_at,
            ),
            open_dead_letter_count,
        )

    def _observation(
        self,
        *,
        status: ProviderRuntimeStatus,
        observed_at: datetime,
        expires_at: datetime,
        scanner_ready: Optional[bool],
        worker_evidence: Optional[AsyncEffectWorkerReadinessEvidence],
        open_dead_letter_count: Optional[int],
        deletion_reconciliation_healthy: Optional[bool],
        kill_switch_active: bool,
    ) -> RuntimeCapabilityControlObservation:
        worker_ready = (
            None
            if worker_evidence is None
            else worker_evidence.is_ready(now=observed_at)
        )
        backlog_count = (
            None
            if worker_evidence is None
            else worker_evidence.backlog_eligible_count
        )
        identity = {
            "backlogCount": backlog_count,
            "budgetState": RuntimeCapabilityBudgetState.NOT_APPLICABLE.value,
            "capability": status.capability,
            "deletionReconciliationHealthy": deletion_reconciliation_healthy,
            "expiresAt": expires_at.isoformat(),
            "killSwitchActive": kill_switch_active,
            "observedAt": observed_at.isoformat(),
            "openDeadLetterCount": open_dead_letter_count,
            "providerReady": status.provider_ready,
            "providerReason": status.reason,
            "scannerReady": scanner_ready,
            "workerEvidenceId": (
                None if worker_evidence is None else worker_evidence.observation_id
            ),
            "workerReady": worker_ready,
        }
        digest = sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return RuntimeCapabilityControlObservation(
            capability=status.capability,
            observation_id=f"rco-{digest[:32]}",
            observed_at=observed_at,
            expires_at=expires_at,
            provider_ready=status.provider_ready,
            provider_reason=status.reason,
            scanner_ready=scanner_ready,
            worker_ready=worker_ready,
            worker_evidence_id=(
                None if worker_evidence is None else worker_evidence.observation_id
            ),
            backlog_count=backlog_count,
            backlog_limit=(
                None
                if backlog_count is None
                else self._settings.runtime_capability_backlog_limit
            ),
            open_dead_letter_count=open_dead_letter_count,
            dead_letter_limit=(
                None
                if open_dead_letter_count is None
                else self._settings.runtime_capability_dead_letter_limit
            ),
            deletion_reconciliation_healthy=deletion_reconciliation_healthy,
            budget_state=RuntimeCapabilityBudgetState.NOT_APPLICABLE,
            budget_required=False,
            kill_switch_active=kill_switch_active,
        )

    def _deletion_reconciliation_healthy(self, *, required: bool) -> Optional[bool]:
        if not required:
            return None
        summary_source = getattr(
            self._store,
            "summarize_rights_external_effect_reconciliation",
            None,
        )
        if not callable(summary_source):
            return False
        try:
            summary = summary_source(domains=("objectStorage",))
        except Exception:
            return False
        return bool(summary.get("healthy", False))

    def _store_schema_ready(self) -> bool:
        probe = getattr(self._store, "readiness_probe", None)
        if not callable(probe):
            return False
        try:
            return is_async_effect_store_ready(probe())
        except Exception:
            return False

    def _scanner_ready(self, status: ProviderRuntimeStatus) -> Optional[bool]:
        if not self._settings.owner_truth_media_capture_enabled:
            return None
        return status.reason not in {
            "contentSafetyScannerUnavailable",
            "contentSafetyProviderUnavailable",
        }

    @staticmethod
    def _worker_id() -> str:
        hostname = "".join(
            character
            for character in socket.gethostname()
            if character.isalnum() or character in "_.:-"
        )
        return f"runtime-capability-{hostname or 'worker'}"

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("runtime capability collection time must include timezone")
        return value.astimezone(timezone.utc)


__all__ = ["RuntimeCapabilityControlCollector"]
