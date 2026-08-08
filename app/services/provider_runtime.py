"""Fail-closed, value-free Provider capability inventory.

This module validates provider configuration shape and first-party runtime
dependencies that can be checked without a provider call. It is built once
during API startup and is consumed by ``/config/runtime``. No credential,
bucket name, endpoint, object key, or user data is included in the public
descriptor.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional

from app.core.config import Settings
from app.services.identity_bindings import identity_challenge_runtime_descriptor
from app.services.owner_truth_media_source_object import (
    clamav_daemon_runtime_ready,
    clamav_scanner_runtime_ready,
    cos_endpoint_matches_region,
)


@dataclass(frozen=True)
class ProviderRuntimeStatus:
    """One value-free capability decision produced from startup configuration."""

    capability: str
    enabled: bool
    provider_ready: bool
    provider: str
    provider_kind: str
    operation: str
    data_class: str
    region: str
    retention_policy_version: str
    fallback_mode: str
    reason: str
    configuration_status: str
    evidence_status: str

    def public_descriptor(self) -> Dict[str, object]:
        return {
            "capability": self.capability,
            "enabled": self.enabled,
            "providerReady": self.provider_ready,
            "provider": self.provider,
            "providerKind": self.provider_kind,
            "operation": self.operation,
            "dataClass": self.data_class,
            "region": self.region,
            "retentionPolicyVersion": self.retention_policy_version,
            "fallbackMode": self.fallback_mode,
            "reason": self.reason,
            "configurationStatus": self.configuration_status,
            "evidenceStatus": self.evidence_status,
        }


class ProviderRuntimeInventory:
    """Single startup-time authority for public provider capability metadata.

    A capability may be implemented while disabled.  A partially configured
    provider is never reported as enabled or ready; callers can safely use the
    result as a fail-closed admission gate without reading individual env vars.
    """

    CONTRACT_VERSION = 1
    _MEDIA_STORAGE_CAPABILITY = "ownerTruthMediaStorage"
    _MEDIA_PROCESSING_CAPABILITY = "ownerTruthMediaProcessing"
    _IDENTITY_CAPABILITY = "identityChallenge"
    _VOICE_CAPABILITY = "voiceCloneShell"
    _DIGITAL_HUMAN_CAPABILITY = "digitalHumanLivePanel"

    def __init__(
        self,
        settings: Settings,
        *,
        validated_at_startup: bool = False,
        clamav_scanner_ready: Optional[Callable[[], bool]] = None,
    ) -> None:
        self._settings = settings
        self._validated_at_startup = validated_at_startup
        self._clamav_scanner_ready = clamav_scanner_ready or self._default_clamav_scanner_ready
        storage = self._media_storage_status()
        self._statuses = {
            storage.capability: storage,
            self._MEDIA_PROCESSING_CAPABILITY: self._media_processing_status(storage),
            self._IDENTITY_CAPABILITY: self._identity_challenge_status(),
            self._VOICE_CAPABILITY: self._voice_clone_status(),
            self._DIGITAL_HUMAN_CAPABILITY: self._digital_human_status(),
        }

    def status_for(self, capability: str) -> ProviderRuntimeStatus:
        try:
            return self._statuses[capability]
        except KeyError as error:
            raise KeyError(f"unknown provider runtime capability: {capability}") from error

    def public_descriptor(self) -> Dict[str, object]:
        return {
            "contractVersion": self.CONTRACT_VERSION,
            "validatedAtStartup": self._validated_at_startup,
            "capabilities": {
                capability: status.public_descriptor()
                for capability, status in sorted(self._statuses.items())
            },
        }

    def _media_storage_status(self) -> ProviderRuntimeStatus:
        settings = self._settings
        requested = bool(settings.owner_truth_media_capture_enabled)
        provider = self._normalized(settings.owner_truth_media_storage_provider)
        region = self._media_region(provider)
        base = {
            "capability": self._MEDIA_STORAGE_CAPABILITY,
            "provider_kind": "privateObjectStorage",
            "operation": "writeReadDeleteWithSafetyScan",
            "data_class": "ownerPrivateMedia",
            "region": region,
            "retention_policy_version": "ownerTruthMediaRetention-v1",
            "fallback_mode": "captureDisabled",
        }

        if not requested:
            return ProviderRuntimeStatus(
                enabled=False,
                provider_ready=False,
                provider="disabled",
                reason="runtimeDisabled",
                configuration_status="disabled",
                evidence_status="notRequested",
                **base,
            )

        safety_reason = self._media_safety_reason()
        if safety_reason is not None:
            return ProviderRuntimeStatus(
                enabled=False,
                provider_ready=False,
                provider=provider or "disabled",
                reason=safety_reason,
                configuration_status="incomplete",
                evidence_status="notVerified",
                **base,
            )

        if provider == "filesystem":
            root = str(settings.owner_truth_media_storage_root or "").strip()
            if root and Path(root).is_absolute():
                return ProviderRuntimeStatus(
                    enabled=True,
                    provider_ready=True,
                    provider="filesystem",
                    reason="externalEvidenceMissing",
                    configuration_status="valid",
                    evidence_status="notVerified",
                    **base,
                )
            return ProviderRuntimeStatus(
                enabled=False,
                provider_ready=False,
                provider="filesystem",
                reason="providerConfigurationIncomplete",
                configuration_status="incomplete",
                evidence_status="notVerified",
                **base,
            )

        if provider in {"s3", "cos"}:
            if self._media_object_storage_configuration_is_complete(provider):
                return ProviderRuntimeStatus(
                    enabled=True,
                    provider_ready=True,
                    provider=provider,
                    reason="externalEvidenceMissing",
                    configuration_status="valid",
                    evidence_status="notVerified",
                    **base,
                )
            return ProviderRuntimeStatus(
                enabled=False,
                provider_ready=False,
                provider=provider,
                reason="providerConfigurationIncomplete",
                configuration_status="incomplete",
                evidence_status="notVerified",
                **base,
            )

        return ProviderRuntimeStatus(
            enabled=False,
            provider_ready=False,
            provider=provider or "disabled",
            reason="storageProviderUnsupported",
            configuration_status="unsupported",
            evidence_status="notVerified",
            **base,
        )

    def _media_processing_status(
        self,
        storage: ProviderRuntimeStatus,
    ) -> ProviderRuntimeStatus:
        settings = self._settings
        base = {
            "capability": self._MEDIA_PROCESSING_CAPABILITY,
            "provider": "builtInDocumentProcessor",
            "provider_kind": "privateMediaProcessor",
            "operation": "extractDocumentText",
            "data_class": "ownerPrivateDocument",
            "region": storage.region,
            "retention_policy_version": "ownerTruthMediaProcessing-v1",
            "fallback_mode": "processingPending",
        }
        if not settings.owner_truth_media_processing_worker_enabled:
            return ProviderRuntimeStatus(
                enabled=False,
                provider_ready=False,
                reason="workerDisabled",
                configuration_status="disabled",
                evidence_status="notRequested",
                **base,
            )
        if not storage.provider_ready:
            return ProviderRuntimeStatus(
                enabled=False,
                provider_ready=False,
                reason="storageProviderUnavailable",
                configuration_status="blockedByDependency",
                evidence_status="notVerified",
                **base,
            )
        if not settings.async_effect_v1_enabled or not settings.async_effect_worker_enabled:
            return ProviderRuntimeStatus(
                enabled=False,
                provider_ready=False,
                reason="asyncEffectWorkerUnavailable",
                configuration_status="blockedByDependency",
                evidence_status="notVerified",
                **base,
            )
        return ProviderRuntimeStatus(
            enabled=True,
            provider_ready=True,
            reason="externalEvidenceMissing",
            configuration_status="valid",
            evidence_status="notVerified",
            **base,
        )

    def _identity_challenge_status(self) -> ProviderRuntimeStatus:
        descriptor = identity_challenge_runtime_descriptor(self._settings)
        provider = self._normalized(descriptor.get("providerMode")) or "disabled"
        base = {
            "capability": self._IDENTITY_CAPABILITY,
            "provider": provider,
            "provider_kind": "otpDelivery",
            "operation": "issueAndVerifyChallenge",
            "data_class": "phoneIdentityBinding",
            "region": "providerManaged",
            "retention_policy_version": "identityChallengeRetention-v1",
            "fallback_mode": "loginUnavailable",
        }
        if not descriptor.get("enabled", False):
            requested = self._normalized(self._settings.identity_challenge_adapter)
            return ProviderRuntimeStatus(
                enabled=False,
                provider_ready=False,
                reason=(
                    "runtimeDisabled"
                    if requested in {"", "disabled"}
                    else "providerConfigurationIncomplete"
                ),
                configuration_status=(
                    "disabled" if requested in {"", "disabled"} else "incomplete"
                ),
                evidence_status="notVerified",
                **base,
            )
        if provider == "synthetic":
            return ProviderRuntimeStatus(
                enabled=True,
                provider_ready=True,
                reason="syntheticProviderOnly",
                configuration_status="valid",
                evidence_status="syntheticOnly",
                **base,
            )
        return ProviderRuntimeStatus(
            enabled=True,
            provider_ready=bool(descriptor.get("productionReady", False)),
            reason="externalEvidenceMissing",
            configuration_status="valid",
            evidence_status="notVerified",
            **base,
        )

    def _voice_clone_status(self) -> ProviderRuntimeStatus:
        training_ready = self._present(self._settings.volcengine_voice_clone_api_key)
        synthesis_ready = self._present(self._settings.volcengine_voice_clone_tts_api_key)
        base = {
            "capability": self._VOICE_CAPABILITY,
            "provider": "volcengineVoiceClone",
            "provider_kind": "voiceCloneAndSynthesis",
            "operation": "trainQuerySynthesizeDelete",
            "data_class": "authorizedAdultVoiceSample",
            "region": "providerManaged",
            "retention_policy_version": "voiceProfileRetention-v1",
            "fallback_mode": "voiceCloneDisabled",
        }
        if not training_ready:
            return ProviderRuntimeStatus(
                enabled=False,
                provider_ready=False,
                reason="runtimeDisabled",
                configuration_status="disabled",
                evidence_status="notRequested",
                **base,
            )
        if not synthesis_ready:
            return ProviderRuntimeStatus(
                enabled=True,
                provider_ready=False,
                reason="synthesisProviderUnavailable",
                configuration_status="incomplete",
                evidence_status="notVerified",
                **base,
            )
        return ProviderRuntimeStatus(
            enabled=True,
            provider_ready=True,
            reason="externalEvidenceMissing",
            configuration_status="valid",
            evidence_status="notVerified",
            **base,
        )

    def _digital_human_status(self) -> ProviderRuntimeStatus:
        provider_configured = all(
            self._present(value)
            for value in (
                self._settings.tencent_digital_human_app_key,
                self._settings.tencent_digital_human_access_token,
            )
        ) and any(
            self._present(value)
            for value in (
                self._settings.tencent_digital_human_asset_virtualman_key,
                self._settings.tencent_digital_human_virtualman_project_id,
            )
        )
        return ProviderRuntimeStatus(
            capability=self._DIGITAL_HUMAN_CAPABILITY,
            enabled=False,
            provider_ready=False,
            provider="tencent",
            provider_kind="digitalHumanSession",
            operation="scopedSessionRenderAndAudioDrive",
            data_class="ephemeralConversationAudio",
            region="providerManaged",
            retention_policy_version="digitalHumanSessionRetention-v1",
            fallback_mode="text",
            reason="scopedSessionCredentialContractNotVerified",
            configuration_status=(
                "configuredButBrokerBlocked" if provider_configured else "incomplete"
            ),
            evidence_status="notVerified",
        )

    def _media_safety_reason(self) -> Optional[str]:
        provider = self._normalized(self._settings.owner_truth_media_content_safety_provider)
        environment = self._normalized(self._settings.environment)
        if provider == "clamav":
            try:
                if self._clamav_scanner_ready():
                    return None
            except Exception:
                pass
            return "contentSafetyScannerUnavailable"
        if provider == "testclean" and environment not in {"production", "prod"}:
            return None
        return "contentSafetyProviderUnavailable"

    def _default_clamav_scanner_ready(self) -> bool:
        host = str(self._settings.owner_truth_media_clamav_host or "").strip()
        if host:
            return clamav_daemon_runtime_ready(
                host=host,
                port=self._settings.owner_truth_media_clamav_port,
                timeout_seconds=self._settings.owner_truth_media_clamav_timeout_seconds,
            )
        return clamav_scanner_runtime_ready(
            timeout_seconds=self._settings.owner_truth_media_clamav_timeout_seconds,
        )

    def _media_region(self, provider: str) -> str:
        if provider == "filesystem":
            return "serverLocal"
        if provider in {"s3", "cos"}:
            return self._normalized(self._settings.owner_truth_media_s3_region) or "unknown"
        return "unknown"

    def _media_object_storage_configuration_is_complete(self, provider: str) -> bool:
        settings = self._settings
        required = (
            settings.owner_truth_media_s3_bucket,
            settings.owner_truth_media_s3_region,
            settings.owner_truth_media_s3_access_key_id,
            settings.owner_truth_media_s3_secret_access_key,
            settings.owner_truth_media_s3_server_side_encryption,
        )
        if not all(self._present(value) for value in required):
            return False

        encryption = str(settings.owner_truth_media_s3_server_side_encryption or "").strip()
        kms_key_id = str(settings.owner_truth_media_s3_kms_key_id or "").strip()
        if provider == "cos":
            endpoint = str(settings.owner_truth_media_s3_endpoint_url or "").strip()
            if not cos_endpoint_matches_region(
                endpoint_url=endpoint,
                region=settings.owner_truth_media_s3_region,
            ):
                return False
            if encryption not in {"AES256", "cos/kms"}:
                return False
            return not kms_key_id or encryption == "cos/kms"

        if encryption not in {"AES256", "aws:kms"}:
            return False
        return not kms_key_id or encryption == "aws:kms"

    @staticmethod
    def _normalized(value: object) -> str:
        return str(value or "").strip().lower()

    @staticmethod
    def _present(value: object) -> bool:
        return bool(str(value or "").strip())
