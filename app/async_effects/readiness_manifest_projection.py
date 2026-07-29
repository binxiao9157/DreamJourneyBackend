"""Persist value-free async-effect readiness observations as evidence manifests.

This adapter intentionally reuses the shared append-only evidence manifest
sink. It does not start a worker, claim or replay a job, or call a Provider.
Only aggregate readiness metadata and hashes are persisted.
"""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from typing import Any, Optional

from app.async_effects.readiness_evidence import (
    ASYNC_EFFECT_READINESS_EVIDENCE_SCHEMA_VERSION,
    AsyncEffectReadinessManifestPlan,
    AsyncEffectWorkerReadinessEvidence,
    build_async_effect_readiness_manifest_plan,
)
from app.observability.evidence_manifest import EvidenceManifestService


ASYNC_EFFECT_READINESS_MANIFEST_PROJECTION_SCHEMA_VERSION = (
    "async-effect-readiness-manifest-projection-v1"
)
_MANIFEST_TYPE = "asyncEffectReadiness"
_COMMAND_ID = "persistAsyncEffectReadinessManifest"
_ISSUER = "asyncEffectReadinessAdapter"


class AsyncEffectReadinessManifestProjectionError(ValueError):
    """An invalid readiness observation cannot become durable evidence."""


def _sample_set_hash(manifest_id: str) -> str:
    return sha256(
        f"async-effect-readiness-manifest-v1|{manifest_id}".encode("utf-8")
    ).hexdigest()


def persist_async_effect_readiness_manifest(
    evidence: AsyncEffectWorkerReadinessEvidence,
    *,
    manifest_service: EvidenceManifestService,
    source_commit: str,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Append one value-free manifest for a readiness observation.

    The underlying manifest event ID is deterministic for the same effective
    observation, so repeating the call delegates to the existing append-only
    sink's deduplication. A later expired observation receives a separate
    ``notRun`` manifest and cannot overwrite earlier evidence.
    """

    if not isinstance(evidence, AsyncEffectWorkerReadinessEvidence):
        raise AsyncEffectReadinessManifestProjectionError(
            "asyncEffectReadinessEvidenceRequired"
        )
    if not isinstance(manifest_service, EvidenceManifestService):
        raise AsyncEffectReadinessManifestProjectionError(
            "asyncEffectReadinessManifestServiceRequired"
        )

    plan: AsyncEffectReadinessManifestPlan = (
        build_async_effect_readiness_manifest_plan(evidence, now=now)
    )
    receipt = manifest_service.issue(
        manifest_type=_MANIFEST_TYPE,
        source_commit=source_commit,
        command_id=_COMMAND_ID,
        sample_count=1,
        sample_set_hash=_sample_set_hash(plan.manifest_id),
        exclusion_codes=(
            "businessPayload",
            "ownerIdentity",
            "providerCredential",
            "providerPayload",
        ),
        source_schema_versions=(
            ASYNC_EFFECT_READINESS_EVIDENCE_SCHEMA_VERSION,
            ASYNC_EFFECT_READINESS_MANIFEST_PROJECTION_SCHEMA_VERSION,
        ),
        artifact_hashes=(plan.artifact_hash,),
        window_started_at=evidence.observed_at,
        window_ended_at=evidence.observed_at,
        issued_at=evidence.observed_at,
        expires_at=evidence.expires_at,
        issuer=_ISSUER,
        manifest_status=plan.status.value,
    )
    return {
        "schemaVersion": ASYNC_EFFECT_READINESS_MANIFEST_PROJECTION_SCHEMA_VERSION,
        "manifestPlan": plan.value_free_summary(),
        "evidenceManifest": receipt,
    }


__all__ = [
    "ASYNC_EFFECT_READINESS_MANIFEST_PROJECTION_SCHEMA_VERSION",
    "AsyncEffectReadinessManifestProjectionError",
    "persist_async_effect_readiness_manifest",
]
