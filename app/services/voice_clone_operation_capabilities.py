"""Provider-neutral operation matrix for the private VoiceProfile lifecycle.

The matrix describes whether a client command may be attempted and whether an
upstream Provider can prove its part of completion.  Those are intentionally
separate: pause and delete must remain available to revoke local use even when
the Provider is down or has no reviewed deletion API.
"""

from __future__ import annotations

from typing import Any, Mapping


VOICE_CLONE_OPERATION_CAPABILITY_CONTRACT_VERSION = 1
_PROVIDER_CAPABILITIES = {"ready", "unavailable", "notRequired", "unsupported"}


def _provider_mode(provider: Any, fallback: str) -> str:
    candidate = str(getattr(provider, "provider_mode", "") or "").strip()
    return candidate or fallback


def _provider_ready(provider: Any) -> bool:
    return getattr(provider, "is_configured", False) is True


def _operation(
    *,
    available: bool,
    execution_owner: str,
    provider_capability: str,
    provider_completion_available: bool,
    completion_mode: str,
    reason_code: str,
    endpoint_template: str,
    required_profile_states: tuple[str, ...],
) -> dict[str, object]:
    if provider_capability not in _PROVIDER_CAPABILITIES:
        raise ValueError("voice clone provider capability is invalid")
    if not endpoint_template.startswith("/voice/"):
        raise ValueError("voice clone operation endpoint must be an app route")
    return {
        "available": bool(available),
        "executionOwner": execution_owner,
        "providerCapability": provider_capability,
        "providerCompletionAvailable": bool(provider_completion_available),
        "completionMode": completion_mode,
        "reasonCode": reason_code,
        "endpointTemplate": endpoint_template,
        "requiredProfileStates": list(required_profile_states),
    }


def build_voice_clone_operation_capability_matrix(
    *,
    training_provider: Any,
    synthesis_provider: Any,
    training_admission_enabled: bool,
    training_admission_reason: str,
    deletion_worker_enabled: bool,
) -> dict[str, object]:
    """Build a value-free matrix for all public VoiceProfile commands."""

    training_ready = _provider_ready(training_provider)
    synthesis_ready = _provider_ready(synthesis_provider)
    admission_ready = bool(training_admission_enabled)
    normalized_admission_reason = str(training_admission_reason or "").strip()
    train_available = training_ready and admission_ready

    raw_deletion_capability = str(
        getattr(training_provider, "profile_deletion_capability", "unsupported")
        or "unsupported"
    ).strip()
    if raw_deletion_capability not in {"ready", "unsupported", "unavailable"}:
        raw_deletion_capability = "unsupported"
    deletion_provider_ready = (
        training_ready
        and raw_deletion_capability == "ready"
        and bool(deletion_worker_enabled)
    )
    if raw_deletion_capability == "unsupported":
        deletion_reason = "providerVoiceDeletionUnsupported"
    elif not training_ready or raw_deletion_capability == "unavailable":
        deletion_reason = "voiceCloneProviderUnavailable"
    elif not deletion_worker_enabled:
        deletion_reason = "providerDeletionWorkerDisabled"
    else:
        deletion_reason = "ready"

    operations: dict[str, Mapping[str, object]] = {
        "train": _operation(
            available=train_available,
            execution_owner="provider",
            provider_capability="ready" if training_ready else "unavailable",
            provider_completion_available=train_available,
            completion_mode="providerAcceptedThenPolled",
            reason_code=(
                "ready"
                if train_available
                else (
                    normalized_admission_reason
                    if training_ready and normalized_admission_reason
                    else "voiceCloneProviderUnavailable"
                )
            ),
            endpoint_template="/voice/profiles",
            required_profile_states=("draft", "failed"),
        ),
        "query": _operation(
            available=training_ready,
            execution_owner="provider",
            provider_capability="ready" if training_ready else "unavailable",
            provider_completion_available=training_ready,
            completion_mode="providerObservation",
            reason_code="ready" if training_ready else "voiceCloneProviderUnavailable",
            endpoint_template="/voice/profiles/{user_id}/{voice_profile_id}/refresh",
            required_profile_states=("training", "previewReady", "failed"),
        ),
        "preview": _operation(
            available=synthesis_ready,
            execution_owner="provider",
            provider_capability="ready" if synthesis_ready else "unavailable",
            provider_completion_available=synthesis_ready,
            completion_mode="providerSynchronous",
            reason_code="ready" if synthesis_ready else "voiceSynthesisProviderUnavailable",
            endpoint_template="/voice/synthesis",
            required_profile_states=("previewReady", "accepted"),
        ),
        "accept": _operation(
            available=True,
            execution_owner="serverAuthority",
            provider_capability="notRequired",
            provider_completion_available=True,
            completion_mode="serverReceipt",
            reason_code="ready",
            endpoint_template="/voice/profiles/{user_id}/{voice_profile_id}/quality-acceptance",
            required_profile_states=("previewReady",),
        ),
        "synthesize": _operation(
            available=synthesis_ready,
            execution_owner="provider",
            provider_capability="ready" if synthesis_ready else "unavailable",
            provider_completion_available=synthesis_ready,
            completion_mode="providerSynchronous",
            reason_code="ready" if synthesis_ready else "voiceSynthesisProviderUnavailable",
            endpoint_template="/voice/synthesis",
            required_profile_states=("accepted",),
        ),
        "pause": _operation(
            available=True,
            execution_owner="serverAuthority",
            provider_capability="notRequired",
            provider_completion_available=True,
            completion_mode="serverReceipt",
            reason_code="ready",
            endpoint_template="/voice/profiles/{user_id}/{voice_profile_id}/disable",
            required_profile_states=("previewReady", "accepted", "failed"),
        ),
        "delete": _operation(
            available=True,
            execution_owner="serverThenProvider",
            provider_capability=raw_deletion_capability,
            provider_completion_available=deletion_provider_ready,
            completion_mode="revocationFirstAsyncReceipt",
            reason_code=deletion_reason,
            endpoint_template="/voice/profiles/{user_id}/{voice_profile_id}",
            required_profile_states=(
                "draft",
                "uploadPending",
                "training",
                "previewReady",
                "accepted",
                "paused",
                "failed",
                "deleting",
            ),
        ),
    }
    return {
        "schemaVersion": VOICE_CLONE_OPERATION_CAPABILITY_CONTRACT_VERSION,
        "trainingProvider": _provider_mode(training_provider, "unavailable"),
        "synthesisProvider": _provider_mode(synthesis_provider, "unavailable"),
        "operations": operations,
    }


__all__ = [
    "VOICE_CLONE_OPERATION_CAPABILITY_CONTRACT_VERSION",
    "build_voice_clone_operation_capability_matrix",
]
