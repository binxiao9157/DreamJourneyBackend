"""Private SourceObject deletion acceptance and value-free provider evidence.

The API accepts a deletion by revoking application access first.  This module
then records a deterministic async-effect request and a provider-effect
acceptance receipt.  It deliberately does not delete bytes synchronously: the
P0-S2 worker will own physical object-store completion, retry and reconciliation.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Mapping, Optional

from app.async_effects.consumer_repository import AsyncEffectConsumerCompletionCommand
from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectTarget, EffectReceiptSummary
from app.async_effects.lease_repository import AsyncEffectLeaseError
from app.async_effects.provider_effect_repository import ProviderEffectPersistenceSummary
from app.async_effects.provider_effects import (
    ProviderEffectIntent,
    ProviderEffectReceipt,
    ProviderEffectState,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_media_processing import (
    OwnerTruthMediaProcessingError,
    build_media_source_object_processing_effect_intent_for_generation,
)
from app.services.owner_truth_media_source_object import MediaSourceObjectDeletionResult


OWNER_TRUTH_MEDIA_DELETION_SCHEMA_VERSION = "owner-truth-media-deletion-v1"
OWNER_TRUTH_MEDIA_DELETION_OPERATION_TYPE = "ownerTruth.mediaSourceObject.delete"
OWNER_TRUTH_MEDIA_DELETION_EVENT_TYPE = "ownerTruth.mediaSourceObject.deletionRequested"
OWNER_TRUTH_MEDIA_DELETION_JOB_TYPE = "ownerTruth.mediaSourceObject.delete"
OWNER_TRUTH_MEDIA_DELETION_MAX_ATTEMPTS = 3
OWNER_TRUTH_MEDIA_DELETION_CONSUMER = "ownerTruth.mediaSourceObject.deletion"


class OwnerTruthMediaDeletionError(RuntimeError):
    """The private media-deletion effect could not be safely accepted."""


@dataclass(frozen=True)
class MediaDeletionEnqueueResult:
    source_object: Mapping[str, Any]
    deletion_effect: Optional[EffectReceiptSummary]
    provider_effect: Optional[ProviderEffectPersistenceSummary]
    processing_cancelled: bool


@dataclass(frozen=True)
class OwnerTruthMediaDeletionConsumerCommand(AsyncEffectConsumerCompletionCommand):
    """Value-free terminal evidence for one revocation-first deletion effect."""

    deletion_state: str

    def __post_init__(self) -> None:
        super().__post_init__()
        target = self.intent.target
        if (
            self.intent.operation_type != OWNER_TRUTH_MEDIA_DELETION_OPERATION_TYPE
            or self.intent.event_type != OWNER_TRUTH_MEDIA_DELETION_EVENT_TYPE
            or self.intent.job_type != OWNER_TRUTH_MEDIA_DELETION_JOB_TYPE
            or target.resource_type != "mediaSourceObject"
            or target.purpose != "privateMediaDeletion"
        ):
            raise OwnerTruthMediaDeletionError("media deletion consumer requires its typed effect")
        if self.consumer_name != OWNER_TRUTH_MEDIA_DELETION_CONSUMER:
            raise OwnerTruthMediaDeletionError("media deletion consumer name is invalid")
        if self.business_target_key != self.intent.business_target_key:
            raise OwnerTruthMediaDeletionError("media deletion consumer target is invalid")
        normalized_state = str(self.deletion_state or "").strip()
        if normalized_state not in {"completed", "partial", "unsupported"}:
            raise OwnerTruthMediaDeletionError("media deletion state is invalid")
        if normalized_state == "completed" and self.outcome != "completed":
            raise OwnerTruthMediaDeletionError("completed media deletion must complete its consumer")
        if normalized_state != "completed" and self.outcome != "failed":
            raise OwnerTruthMediaDeletionError("incomplete media deletion must fail its consumer")
        object.__setattr__(self, "deletion_state", normalized_state)


def _canonical_hash(value: Mapping[str, object]) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def build_media_source_object_deletion_effect_intent(
    *,
    source_object: Mapping[str, Any],
) -> AsyncEffectIntent:
    """Build one stable deletion request without copying an object-store key."""

    if str(source_object.get("accessState") or "") != "accessRevoked":
        raise OwnerTruthMediaDeletionError("media deletion requires revoked access")
    if str(source_object.get("deletionStatus") or "") != "pending":
        raise OwnerTruthMediaDeletionError("media deletion is not pending")
    source_object_id = str(source_object.get("sourceObjectId") or "").strip()
    vault_id = str(source_object.get("vaultId") or "").strip()
    owner_subject_id = str(source_object.get("ownerSubjectId") or "").strip()
    authority_epoch = source_object.get("authorityEpoch")
    deletion_generation = source_object.get("deletionGeneration")
    storage_version = source_object.get("storageVersion")
    if not source_object_id or not vault_id or not owner_subject_id:
        raise OwnerTruthMediaDeletionError("media deletion target is incomplete")
    if type(authority_epoch) is not int or authority_epoch < 0:
        raise OwnerTruthMediaDeletionError("media deletion authority epoch is invalid")
    if type(deletion_generation) is not int or deletion_generation < 1:
        raise OwnerTruthMediaDeletionError("media deletion generation is invalid")
    if type(storage_version) is not int or storage_version < 1:
        raise OwnerTruthMediaDeletionError("media deletion storage version is invalid")
    return AsyncEffectIntent(
        operation_type=OWNER_TRUTH_MEDIA_DELETION_OPERATION_TYPE,
        target=AsyncEffectTarget(
            owner_subject_id=owner_subject_id,
            vault_id=vault_id,
            resource_type="mediaSourceObject",
            resource_id=source_object_id,
            resource_version=deletion_generation,
            purpose="privateMediaDeletion",
            authority_epoch=authority_epoch,
        ),
        payload_hash=_canonical_hash(
            {
                "contentSha256": str(source_object.get("contentSha256") or ""),
                "deletionGeneration": deletion_generation,
                "mediaKind": str(source_object.get("mediaKind") or ""),
                "schemaVersion": OWNER_TRUTH_MEDIA_DELETION_SCHEMA_VERSION,
                "sourceObjectId": source_object_id,
                "storageVersion": storage_version,
            }
        ),
        event_type=OWNER_TRUTH_MEDIA_DELETION_EVENT_TYPE,
        job_type=OWNER_TRUTH_MEDIA_DELETION_JOB_TYPE,
        max_attempts=OWNER_TRUTH_MEDIA_DELETION_MAX_ATTEMPTS,
    )


class OwnerTruthMediaDeletionCoordinator:
    """Accepts only revocation-first deletion work in an active request UoW."""

    def __init__(self, store: Any) -> None:
        self._store = store

    def enqueue_accepted_deletion(
        self,
        *,
        context: OwnerTruthCommandContext,
        result: MediaSourceObjectDeletionResult,
    ) -> MediaDeletionEnqueueResult:
        source_object = result.source_object
        if str(source_object.get("vaultId") or "") != context.vault_id:
            raise OwnerTruthMediaDeletionError("media deletion vault does not match command context")
        if str(source_object.get("ownerSubjectId") or "") != context.owner_subject_id:
            raise OwnerTruthMediaDeletionError("media deletion owner does not match command context")

        processing_cancelled = self._cancel_prior_processing_if_present(
            source_object=source_object,
            processing_generation=result.cancelled_processing_generation,
        )
        if not result.deletion_effect_required:
            return MediaDeletionEnqueueResult(
                source_object=source_object,
                deletion_effect=None,
                provider_effect=None,
                processing_cancelled=processing_cancelled,
            )

        deletion_intent = build_media_source_object_deletion_effect_intent(
            source_object=source_object
        )
        deletion_effect = self._store.effect_kernel_repository().accept(deletion_intent)
        provider_intent = ProviderEffectIntent(
            effect_intent=deletion_intent,
            provider="objectStorage",
            capability="privateMediaDeletion",
            request_hash=_canonical_hash(
                {
                    "deletionGeneration": source_object.get("deletionGeneration"),
                    "operationStableKey": deletion_intent.stable_key,
                    "schemaVersion": OWNER_TRUTH_MEDIA_DELETION_SCHEMA_VERSION,
                    "storageVersion": source_object.get("storageVersion"),
                }
            ),
        )
        provider_effect = self._store.provider_effect_repository().record(
            ProviderEffectReceipt(
                intent=provider_intent,
                state=ProviderEffectState.ACCEPTED,
                reason_code="privateMediaDeletionQueued",
                observation_origin="localAcceptance",
            )
        )
        return MediaDeletionEnqueueResult(
            source_object=source_object,
            deletion_effect=deletion_effect,
            provider_effect=provider_effect,
            processing_cancelled=processing_cancelled,
        )

    def _cancel_prior_processing_if_present(
        self,
        *,
        source_object: Mapping[str, Any],
        processing_generation: Optional[int],
    ) -> bool:
        if processing_generation is None or processing_generation < 1:
            return False
        lease_getter = getattr(self._store, "async_effect_lease_repository", None)
        if not callable(lease_getter):
            return False
        try:
            intent = build_media_source_object_processing_effect_intent_for_generation(
                source_object=source_object,
                processing_generation=processing_generation,
            )
            lease_getter().request_cancel(intent.job_id)
            return True
        except (AsyncEffectLeaseError, OwnerTruthMediaProcessingError):
            # The old job may not yet have been dispatched to the lease table.
            # The source-object commit fence remains authoritative in that case.
            return False


__all__ = [
    "MediaDeletionEnqueueResult",
    "OWNER_TRUTH_MEDIA_DELETION_CONSUMER",
    "OWNER_TRUTH_MEDIA_DELETION_EVENT_TYPE",
    "OWNER_TRUTH_MEDIA_DELETION_JOB_TYPE",
    "OWNER_TRUTH_MEDIA_DELETION_MAX_ATTEMPTS",
    "OWNER_TRUTH_MEDIA_DELETION_OPERATION_TYPE",
    "OWNER_TRUTH_MEDIA_DELETION_SCHEMA_VERSION",
    "OwnerTruthMediaDeletionCoordinator",
    "OwnerTruthMediaDeletionConsumerCommand",
    "OwnerTruthMediaDeletionError",
    "build_media_source_object_deletion_effect_intent",
]
