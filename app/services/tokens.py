from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from app.core.config import Settings


class TokenService:
    fixed_realtime_dialog_app_key = "PlgvMymc7f3tQnJ6"
    realtime_config_ttl_seconds = 3600

    def __init__(self, settings: Settings):
        self.settings = settings

    def realtime_config(self, user_id: str) -> Dict[str, Any]:
        contract = self._runtime_contract()
        if self.settings.volcengine_app_id and self.settings.volcengine_app_token:
            return contract | {
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
            return contract | {
                "authMode": "api_key",
                "address": self.settings.volcengine_realtime_address,
                "uri": self.settings.volcengine_realtime_uri,
                "resourceID": self.settings.volcengine_realtime_resource_id,
                "apiKey": self.settings.volcengine_api_key,
                "uid": user_id,
            }

        raise ValueError("VolcEngine realtime credentials are not configured")

    def _runtime_contract(self) -> Dict[str, Any]:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=self.realtime_config_ttl_seconds)
        return {
            "expiresInSeconds": self.realtime_config_ttl_seconds,
            "expiresAt": expires_at.isoformat().replace("+00:00", "Z"),
            "fallback": {
                "enabled": True,
                "mode": "localBuildSettings",
            },
        }
