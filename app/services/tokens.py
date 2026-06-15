from typing import Any, Dict

from app.core.config import Settings


class TokenService:
    fixed_realtime_dialog_app_key = "PlgvMymc7f3tQnJ6"

    def __init__(self, settings: Settings):
        self.settings = settings

    def realtime_config(self, user_id: str) -> Dict[str, Any]:
        if self.settings.volcengine_app_id and self.settings.volcengine_app_token:
            return {
                "authMode": "legacy",
                "address": self.settings.volcengine_realtime_address,
                "uri": self.settings.volcengine_realtime_uri,
                "resourceID": self.settings.volcengine_realtime_resource_id,
                "appID": self.settings.volcengine_app_id,
                "appKey": self.settings.volcengine_app_key or self.fixed_realtime_dialog_app_key,
                "appToken": self.settings.volcengine_app_token,
                "uid": user_id,
            }

        if self.settings.volcengine_api_key:
            return {
                "authMode": "api_key",
                "address": self.settings.volcengine_realtime_address,
                "uri": self.settings.volcengine_realtime_uri,
                "resourceID": self.settings.volcengine_realtime_resource_id,
                "apiKey": self.settings.volcengine_api_key,
                "uid": user_id,
            }

        raise ValueError("VolcEngine realtime credentials are not configured")
