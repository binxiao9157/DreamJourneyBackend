from typing import Any, Dict

from app.core.config import Settings
from app.services.deepseek import ArchiveImageAnalysisProviderFactory


class RuntimeConfigService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def public_config(self) -> Dict[str, Any]:
        archive_image_analysis = ArchiveImageAnalysisProviderFactory(self.settings).make()
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
            },
            "archive": {
                "uploadIntentEndpoint": "/archive/media/upload-intent",
                "storageProvider": "mockObjectStorage",
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
            "privacy": {
                "localOnly": "never_upload",
                "generationAllowed": "ai_and_backend_allowed",
                "familyCircle": "authorized_family_sync",
            },
        }
