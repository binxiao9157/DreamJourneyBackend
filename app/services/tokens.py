from typing import Any, Dict

from app.core.config import Settings


class TokenService:
    _SCOPED_SESSION_REQUIREMENTS = ("scope", "ttl", "audience", "revocation")

    def __init__(self, settings: Settings):
        self.settings = settings

    def realtime_config(self, user_id: str) -> Dict[str, Any]:
        del user_id
        required_properties = list(self._SCOPED_SESSION_REQUIREMENTS)
        return {
            "status": "blocked",
            "capability": "realtimeVoice",
            "provider": "volcengine",
            "credentialMode": "blockedStaticCredential",
            "accessPath": "backendProxyOrText",
            "mobileDirectAllowed": False,
            "brokerStatus": "providerContractNotVerified",
            "providerReady": False,
            "releaseVisible": False,
            "retryable": False,
            "decisionReceipt": {
                "decision": "keepDirectMobileClosed",
                "reasonCode": "scopedSessionCredentialContractNotVerified",
                "requiredProperties": required_properties,
                "verifiedProperties": [],
                "missingProperties": required_properties,
                "evidenceVersion": "volcengine-dialog-ios-sdk-1597646@2026-07-15",
            },
            "fallback": {
                "enabled": True,
                "mode": "backendProxyOrText",
            },
            "contractVersion": 3,
        }
