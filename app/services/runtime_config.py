from typing import Any, Dict

from app.core.config import Settings


class RuntimeConfigService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def public_config(self) -> Dict[str, Any]:
        return {
            "environment": self.settings.environment,
            "baseURL": self.settings.public_base_url,
            "capabilities": {
                "deepseekProxy": bool(self.settings.deepseek_api_key),
                "ttsProxy": bool(self.settings.volcengine_api_key and self.settings.volcengine_voice_type),
                "realtimeToken": bool(
                    (self.settings.volcengine_app_id and self.settings.volcengine_app_key and self.settings.volcengine_app_token)
                    or self.settings.volcengine_api_key
                ),
                "amapDistrictProxy": bool(self.settings.amap_web_service_key),
                "kbSync": True,
                "familyCircle": True,
            },
            "voice": {
                "voiceType": self.settings.volcengine_voice_type,
                "realtimeResourceID": self.settings.volcengine_realtime_resource_id,
            },
            "privacy": {
                "localOnly": "never_upload",
                "generationAllowed": "ai_and_backend_allowed",
                "familyCircle": "authorized_family_sync",
            },
        }
