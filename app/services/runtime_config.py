from typing import Any, Dict

from app.core.config import Settings
from app.services.deepseek import ArchiveImageAnalysisProviderFactory
from app.services.digital_human_access import DigitalHumanAccessPolicy
from app.services.route_ownership import RouteOwnershipRegistry
from app.services.release_policy import ReleasePolicyService
from app.services.tokens import TokenService
from app.services.tts import VoiceCloneTTSProviderFactory
from app.services.voice_clone import VoiceCloneProviderFactory, configured_voice_clone_speaker_ids


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
        release_policy = ReleasePolicyService()
        return {
            "environment": self.settings.environment,
            "baseURL": self.settings.public_base_url,
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
