from typing import Any, Dict

from app.core.config import Settings


class TokenService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def realtime_config(self, user_id: str) -> Dict[str, Any]:
        del user_id
        return {
            "status": "blocked",
            "capability": "realtimeVoice",
            "provider": "volcengine",
            "credentialMode": "blockedStaticCredential",
            "providerReady": False,
            "releaseVisible": False,
            "retryable": False,
            "fallback": {
                "enabled": True,
                "mode": "backendProxyOrText",
            },
            "contractVersion": 2,
        }
