from typing import Any, Dict

from app.core.config import Settings
from app.services.deepseek import ArchiveImageAnalysisProviderFactory
from app.services.digital_human_access import DigitalHumanAccessPolicy
from app.services.route_ownership import RouteOwnershipRegistry
from app.services.release_policy import ReleasePolicyService, parse_release_policy_feature_set
from app.services.recovery_access import RecoveryAccessPolicy
from app.services.safety_policy import SafetyPolicy
from app.services.tokens import TokenService
from app.services.tts import VoiceCloneTTSProviderFactory
from app.services.voice_clone import VoiceCloneProviderFactory, configured_voice_clone_speaker_ids
from app.services.runtime_capabilities import (
    RuntimeCapabilityComposer,
    RuntimeCapabilityInput,
)


class RuntimeConfigService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def public_config(self) -> Dict[str, Any]:
        archive_image_analysis = ArchiveImageAnalysisProviderFactory(self.settings).make()
        voice_clone_provider = VoiceCloneProviderFactory(self.settings).make()
        voice_clone_tts_provider = VoiceCloneTTSProviderFactory(self.settings).make()
        voice_clone_speaker_ids = self._voice_clone_speaker_ids()
        digital_human_asset_mode = self._digital_human_asset_mode()
        digital_human_access = DigitalHumanAccessPolicy().blocked_mobile_contract()
        route_ownership_audit = RouteOwnershipRegistry().audit_summary()
        realtime_voice = TokenService(self.settings).realtime_config(user_id="runtime-capability")
        release_policy = ReleasePolicyService(
            policy_revision=self.settings.release_policy_revision,
            min_client_build=self.settings.release_policy_min_client_build,
            ttl_seconds=self.settings.release_policy_ttl_seconds,
            emergency_revision=self.settings.release_policy_emergency_revision,
            emergency_disabled_features=parse_release_policy_feature_set(
                self.settings.release_policy_emergency_disabled_features
            ),
            enforced_features=parse_release_policy_feature_set(
                self.settings.release_policy_enforced_features
            ),
            shadow_mode=self.settings.release_policy_command_mode != "enforce",
        )
        recovery_access = RecoveryAccessPolicy(
            mode=self.settings.recovery_access_mode,
            authority_epoch=self.settings.authority_epoch,
        )
        safety_policy = SafetyPolicy().evaluate("")
        capability_snapshots = self._capability_snapshots(
            archive_image_analysis=archive_image_analysis,
            voice_clone_provider=voice_clone_provider,
            voice_clone_tts_provider=voice_clone_tts_provider,
            digital_human_access=digital_human_access,
            release_policy=release_policy,
        )
        return {
            "environment": self.settings.environment,
            "baseURL": self.settings.public_base_url,
            "capabilitySnapshotSchemaVersion": RuntimeCapabilityComposer.SCHEMA_VERSION,
            "capabilitySnapshots": capability_snapshots,
            "capabilities": {
                "deepseekProxy": bool(self.settings.deepseek_api_key),
                "archiveImageAnalysis": archive_image_analysis.enabled,
                "ttsProxy": bool(self.settings.volcengine_api_key and self.settings.volcengine_voice_type),
                "realtimeToken": False,
                "amapDistrictProxy": bool(self.settings.amap_web_service_key),
                "kbSync": True,
                "familyCircle": True,
                "archiveMediaUploadIntent": True,
                "voiceClone": voice_clone_provider.is_configured,
                "digitalHumanSession": False,
                "digitalHumanSessionLease": False,
                "authSession": True,
                "releasePolicy": True,
            },
            "auth": {
                "mode": "opaqueAccessRefresh",
                "loginEndpoint": "/auth/login",
                "refreshEndpoint": "/auth/refresh",
                "logoutEndpoint": "/auth/logout",
                "tokenType": "Bearer",
                "accessTTLSeconds": max(60, self.settings.auth_access_ttl_seconds),
                "refreshTTLSeconds": max(
                    max(60, self.settings.auth_access_ttl_seconds) + 60,
                    self.settings.auth_refresh_ttl_seconds,
                ),
                "refreshRotation": True,
                "ownershipMode": (
                    self.settings.auth_ownership_mode
                    if self.settings.auth_ownership_mode in {"shadow", "enforce"}
                    else "shadow"
                ),
                "crossAccountPolicy": {
                    "mode": (
                        self.settings.auth_ownership_mode
                        if self.settings.auth_ownership_mode in {"shadow", "enforce"}
                        else "shadow"
                    ),
                    "coveredPolicies": [
                        "careSnapshotRead",
                        "careSnapshotWrite",
                        "timeLetterDetail",
                        "familyInvitationAccept",
                        "familyMemberAccept",
                        "systemOnly",
                    ],
                    "diagnosticHeaders": [
                        "X-DreamJourney-Authorization-Policy",
                        "X-DreamJourney-Authorization-Decision",
                        "X-DreamJourney-Authorization-Reason",
                    ],
                    "productionEnforceReady": False,
                    "principalBoundRouteEnforcement": True,
                    "routeOwnershipAudit": {
                        "routeCount": route_ownership_audit["routeCount"],
                        "categoryCounts": route_ownership_audit["categoryCounts"],
                        "unclassifiedCount": route_ownership_audit["unclassifiedCount"],
                    },
                    "enforceBlockers": [
                        "smsIdentityProof",
                        "deployedShadowEvidence",
                    ],
                    "contractVersion": 1,
                },
                "legacyBackendTokenCompatible": False,
                "contractVersion": 2,
            },
            "releasePolicy": release_policy.public_descriptor(),
            "recovery": recovery_access.public_descriptor(),
            "safety": {
                "policyVersion": SafetyPolicy.POLICY_VERSION,
                "aiDisclosure": safety_policy.disclosure.model_dump(mode="json"),
                "neutralSafetyMode": "textOnly",
                "personaOnCrisis": "deny",
                "delayedReplyOnCrisis": "deny",
                "providerEffectsOnCrisis": "deny",
                "contractVersion": 1,
            },
            "archive": {
                "uploadIntentEndpoint": "/archive/media/upload-intent",
                "storageProvider": "mockObjectStorage",
                "providerDisplayName": "Mock Object Storage",
                "providerMode": "mock",
                "requiresClientUpload": False,
                "uploadURLScheme": "mock",
                "realProviderReady": False,
                "providerSwitchContractVersion": 1,
                "clientUploadAction": "metadataOnly",
                "supportedMediaKinds": ["audio", "video"],
                "audioFileSizeLimitMB": 50,
                "videoFileSizeLimitMB": 200,
                "uploadIntentTTLSeconds": 900,
            },
            "archiveImageAnalysis": archive_image_analysis.public_capability(),
            "voice": {
                **realtime_voice,
                "voiceType": self.settings.volcengine_voice_type,
                "realtimeResourceID": self.settings.volcengine_realtime_resource_id,
                "runtimeConfigEndpoint": "/voice/realtime-token",
            },
            "voiceClone": {
                "enabled": voice_clone_provider.is_configured,
                "provider": voice_clone_provider.provider_mode,
                "realProviderReady": voice_clone_provider.is_configured,
                "trainEndpoint": "/voice/profiles",
                "queryEndpoint": "/voice/profiles/{user_id}/{voice_profile_id}/refresh",
                "synthesisEndpoint": "/voice/synthesis",
                "synthesisProviderReady": voice_clone_tts_provider.is_configured,
                "requiresAuthorization": True,
                "qualityAcceptanceRequired": True,
                "defaultReleaseVisible": False,
                "speakerIdMode": self.settings.volcengine_voice_clone_speaker_id_mode,
                "consoleSpeakerIdConfigured": bool(self.settings.volcengine_voice_clone_speaker_id),
                "speakerIdPoolConfigured": bool(voice_clone_speaker_ids),
                "speakerIdPoolCount": len(voice_clone_speaker_ids),
                "speakerSlotAllocationMode": "exclusivePersistentSlot",
                "speakerSlotReusePolicy": "retireOnDelete",
                "logicalProfileIdSeparated": True,
                "modelType": self.settings.volcengine_voice_clone_model_type,
                "ttsResourceId": self.settings.volcengine_voice_clone_tts_resource_id,
                "voiceClone2TrialReady": (
                    voice_clone_provider.is_configured
                    and self.settings.volcengine_voice_clone_model_type == 5
                    and bool(voice_clone_speaker_ids)
                    and bool(self.settings.volcengine_voice_clone_tts_resource_id)
                ),
                "fallbackMode": "hiddenContract" if not voice_clone_provider.is_configured else "providerV3",
                "lipSyncTimeline": {
                    "field": "visemeTimeline",
                    "source": "providerOptional",
                    "supported": False,
                    "fallbackMode": "avAudioPlayerMetering",
                    "contractVersion": 1,
                },
                "tencentAudioDrive": {
                    "supported": voice_clone_tts_provider.is_configured,
                    "synthesisEndpoint": "/voice/synthesis",
                    "requestOutputMode": "tencentAudioDrive",
                    "providerRequestFormat": "wav",
                    "audioFormat": "pcm16kMono",
                    "sampleRate": 16000,
                    "bitsPerSample": 16,
                    "channelCount": 1,
                    "fallbackMode": "providerTextDrive",
                    "contractVersion": 1,
                },
                "contractVersion": 2,
            },
            "digitalHuman": {
                **digital_human_access,
                "enabled": False,
                "providerMode": "blocked",
                "realProviderReady": False,
                "sdkProvider": "tencent-cloud-digital-human",
                "sdkAuthMode": "staticProjectCredentialUnsupportedOnMobile",
                "sdkAdapterLinked": False,
                "sdkReadinessMessage": "Tencent mobile SDK only exposes project-level static credentials; digital human rendering is blocked.",
                "sessionEndpoint": "/digital-human/sessions",
                "driveModes": ["streamText", "sendAudio"],
                "fallbackMode": "text",
                "assetMode": digital_human_asset_mode,
                "defaultReleaseVisible": False,
                "releaseVisible": False,
                "requiresBackendIssuedCredential": True,
                "credentialBroker": {
                    "required": True,
                    "status": digital_human_access["brokerStatus"],
                    "requiredProperties": ["scope", "ttl", "audience", "revocation"],
                    "verifiedProperties": [],
                    "missingProperties": ["scope", "ttl", "audience", "revocation"],
                },
                "sessionLease": {
                    "enabled": False,
                    "heartbeatEndpointTemplate": "/digital-human/sessions/{sessionId}/heartbeat",
                    "releaseEndpointTemplate": "/digital-human/sessions/{sessionId}/release",
                    "ttlSeconds": max(60, self.settings.tencent_digital_human_session_ttl_seconds),
                    "heartbeatIntervalSeconds": max(
                        10,
                        min(
                            self.settings.tencent_digital_human_heartbeat_interval_seconds,
                            max(60, self.settings.tencent_digital_human_session_ttl_seconds) // 2,
                        ),
                    ),
                    "maxConcurrentSessions": max(
                        1,
                        self.settings.tencent_digital_human_max_concurrent_sessions,
                    ),
                    "conflictStatusCode": 409,
                    "contractVersion": 1,
                },
            },
            "privacy": {
                "localOnly": "never_upload",
                "generationAllowed": "ai_and_backend_allowed",
                "familyCircle": "authorized_family_sync",
            },
        }

    def _voice_clone_speaker_ids(self) -> list[str]:
        return configured_voice_clone_speaker_ids(self.settings)

    def _digital_human_asset_mode(self) -> str:
        if self.settings.tencent_digital_human_asset_virtualman_key:
            return "asset"
        if self.settings.tencent_digital_human_virtualman_project_id:
            return "project"
        return "missing"

    def _capability_snapshots(
        self,
        *,
        archive_image_analysis: Any,
        voice_clone_provider: Any,
        voice_clone_tts_provider: Any,
        digital_human_access: Dict[str, Any],
        release_policy: ReleasePolicyService,
    ) -> Dict[str, Dict[str, Any]]:
        composer = RuntimeCapabilityComposer()
        release_decisions = {
            feature: release_policy.build_snapshot(
                audience="owner",
                cohort="closedPilotAdultSelf",
                client_build=release_policy.min_client_build,
                requested_feature=feature,
            ).features[0]
            for feature in (
                "archiveLocalAnalysis",
                "archiveAudioUpload",
                "archiveVideoUpload",
                "timeLetters",
                "familyManagement",
                "familySpace",
                "voiceCloneShell",
                "digitalHumanLivePanel",
            )
        }

        image_enabled = archive_image_analysis.enabled
        image_provider_ready = image_enabled and archive_image_analysis.supports_vision
        voice_enabled = voice_clone_provider.is_configured
        voice_provider_ready = voice_enabled and voice_clone_tts_provider.is_configured
        digital_human_enabled = bool(digital_human_access.get("enabled", False))
        digital_human_provider_ready = bool(digital_human_access.get("providerReady", False))

        inputs = (
            RuntimeCapabilityInput(
                capability="archiveImageAnalysis",
                implemented=True,
                enabled=image_enabled,
                provider_ready=image_provider_ready,
                release_visible=release_decisions["archiveLocalAnalysis"].releaseVisible,
                external_verified=False,
                provider=archive_image_analysis.provider_id,
                fallback_mode=archive_image_analysis.fallback_mode,
                reason=(
                    "providerVisionUnsupported"
                    if image_enabled and not archive_image_analysis.supports_vision
                    else "runtimeDisabled"
                    if not image_enabled
                    else "externalEvidenceMissing"
                ),
            ),
            RuntimeCapabilityInput(
                capability="archiveAudioUpload",
                implemented=True,
                enabled=True,
                provider_ready=False,
                release_visible=release_decisions["archiveAudioUpload"].releaseVisible,
                external_verified=False,
                provider="mockObjectStorage",
                fallback_mode="metadataOnly",
                reason="mockProviderOnly",
            ),
            RuntimeCapabilityInput(
                capability="archiveVideoUpload",
                implemented=True,
                enabled=True,
                provider_ready=False,
                release_visible=release_decisions["archiveVideoUpload"].releaseVisible,
                external_verified=False,
                provider="mockObjectStorage",
                fallback_mode="metadataOnly",
                reason="mockProviderOnly",
            ),
            RuntimeCapabilityInput(
                capability="timeLetters",
                implemented=True,
                enabled=True,
                provider_ready=True,
                release_visible=release_decisions["timeLetters"].releaseVisible,
                external_verified=False,
                provider="internalScheduler",
                fallback_mode="localDraftOnly",
                reason="externalEvidenceMissing",
            ),
            RuntimeCapabilityInput(
                capability="familyManagement",
                implemented=True,
                enabled=True,
                provider_ready=True,
                release_visible=release_decisions["familyManagement"].releaseVisible,
                external_verified=False,
                provider="internalFamilyService",
                fallback_mode="hiddenContract",
                reason="externalEvidenceMissing",
            ),
            RuntimeCapabilityInput(
                capability="familySpace",
                implemented=True,
                enabled=True,
                provider_ready=True,
                release_visible=release_decisions["familySpace"].releaseVisible,
                external_verified=False,
                provider="internalPersonaService",
                fallback_mode="ownerOnly",
                reason="externalEvidenceMissing",
            ),
            RuntimeCapabilityInput(
                capability="voiceCloneShell",
                implemented=True,
                enabled=voice_enabled,
                provider_ready=voice_provider_ready,
                release_visible=release_decisions["voiceCloneShell"].releaseVisible,
                external_verified=False,
                provider=voice_clone_provider.provider_mode,
                fallback_mode=("providerProxy" if voice_provider_ready else "hiddenContract"),
                reason=(
                    "runtimeDisabled"
                    if not voice_enabled
                    else "synthesisProviderUnavailable"
                    if not voice_provider_ready
                    else "externalEvidenceMissing"
                ),
            ),
            RuntimeCapabilityInput(
                capability="digitalHumanLivePanel",
                implemented=True,
                enabled=digital_human_enabled,
                provider_ready=digital_human_provider_ready,
                release_visible=release_decisions["digitalHumanLivePanel"].releaseVisible,
                external_verified=False,
                provider=str(digital_human_access.get("provider") or "tencent"),
                fallback_mode=str(digital_human_access.get("fallbackMode") or "text"),
                reason=str(
                    (digital_human_access.get("decisionReceipt") or {}).get("reasonCode")
                    or "runtimeDisabled"
                ),
            ),
        )
        return {
            item.capability: item.model_dump(mode="json")
            for item in (composer.compose(value) for value in inputs)
        }
