from typing import Any, Dict

from app.core.config import Settings
from app.services.deepseek import ArchiveImageAnalysisProviderFactory
from app.services.voice_clone import VoiceCloneProviderFactory


class RuntimeConfigService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def public_config(self) -> Dict[str, Any]:
        archive_image_analysis = ArchiveImageAnalysisProviderFactory(self.settings).make()
        voice_clone_provider = VoiceCloneProviderFactory(self.settings).make()
        return {
            "environment": self.settings.environment,
            "baseURL": self.settings.public_base_url,
            "capabilities": {
                "deepseekProxy": bool(self.settings.deepseek_api_key),
                "archiveImageAnalysis": archive_image_analysis.enabled,
                "ttsProxy": bool(self.settings.volcengine_api_key and self.settings.volcengine_voice_type),
                "realtimeToken": bool(
                    (self.settings.volcengine_app_id and self.settings.volcengine_app_token)
                    or self.settings.volcengine_api_key
                ),
                "amapDistrictProxy": bool(self.settings.amap_web_service_key),
                "kbSync": True,
                "familyCircle": True,
                "archiveMediaUploadIntent": True,
                "voiceClone": voice_clone_provider.is_configured,
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
                "voiceType": self.settings.volcengine_voice_type,
                "realtimeResourceID": self.settings.volcengine_realtime_resource_id,
                "runtimeConfigEndpoint": "/voice/realtime-token",
                "fallback": {
                    "enabled": True,
                    "mode": "localBuildSettings",
                },
            },
            "voiceClone": {
                "enabled": voice_clone_provider.is_configured,
                "provider": voice_clone_provider.provider_mode,
                "realProviderReady": voice_clone_provider.is_configured,
                "trainEndpoint": "/voice/profiles",
                "queryEndpoint": "/voice/profiles/{user_id}/{voice_profile_id}/refresh",
                "synthesisEndpoint": "/voice/synthesis",
                "synthesisProviderReady": voice_clone_provider.is_configured,
                "requiresAuthorization": True,
                "qualityAcceptanceRequired": True,
                "defaultReleaseVisible": False,
                "fallbackMode": "hiddenContract" if not voice_clone_provider.is_configured else "providerV3",
                "contractVersion": 1,
            },
            "privacy": {
                "localOnly": "never_upload",
                "generationAllowed": "ai_and_backend_allowed",
                "familyCircle": "authorized_family_sync",
            },
        }
