import hashlib
from typing import Any, Dict

from app.core.config import Settings


class TokenService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def realtime_config(self, user_id: str) -> Dict[str, Any]:
        if self.settings.volcengine_app_id and self.settings.volcengine_app_key and self.settings.volcengine_app_token:
            token_ref = hashlib.sha256(self.settings.volcengine_app_token.encode("utf-8")).hexdigest()[:16]
            return {
                "authMode": "legacy",
                "address": self.settings.volcengine_realtime_address,
                "uri": self.settings.volcengine_realtime_uri,
                "resourceID": self.settings.volcengine_realtime_resource_id,
                "headers": {
                    "X-Api-App-ID": self.settings.volcengine_app_id,
                    "X-Api-App-Key": self.settings.volcengine_app_key,
                    "X-Api-Access-Key-Ref": token_ref,
                    "X-User-ID": user_id,
                },
                "tokenRef": token_ref,
            }

        if self.settings.volcengine_api_key:
            key_ref = hashlib.sha256(self.settings.volcengine_api_key.encode("utf-8")).hexdigest()[:16]
            return {
                "authMode": "api_key",
                "address": self.settings.volcengine_realtime_address,
                "uri": self.settings.volcengine_realtime_uri,
                "resourceID": self.settings.volcengine_realtime_resource_id,
                "headers": {
                    "X-Api-Key-Ref": key_ref,
                    "X-Api-Resource-Id": self.settings.volcengine_realtime_resource_id,
                    "X-User-ID": user_id,
                },
                "tokenRef": key_ref,
            }

        raise ValueError("VolcEngine realtime credentials are not configured")
