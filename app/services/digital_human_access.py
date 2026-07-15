from typing import Any, Dict


class DigitalHumanAccessPolicy:
    """Server-authoritative decision for the Tencent mobile access path."""

    CONTRACT_VERSION = 4
    _SCOPED_SESSION_REQUIREMENTS = ("scope", "ttl", "audience", "revocation")

    def blocked_mobile_contract(self) -> Dict[str, Any]:
        required_properties = list(self._SCOPED_SESSION_REQUIREMENTS)
        return {
            "status": "blocked",
            "capability": "digitalHuman",
            "provider": "tencent",
            "providerReady": False,
            "credentialMode": "blockedStaticCredential",
            "accessPath": "textFallback",
            "mobileDirectAllowed": False,
            "brokerStatus": "providerContractNotVerified",
            "releaseVisible": False,
            "retryable": False,
            "decisionReceipt": {
                "decision": "keepDirectMobileClosed",
                "reasonCode": "scopedSessionCredentialContractNotVerified",
                "requiredProperties": required_properties,
                "verifiedProperties": [],
                "missingProperties": required_properties,
                "evidenceVersion": "tencent-digital-human-ios-sdk-110200-and-apaas-90943@2026-07-15",
            },
            "fallbackMode": "text",
            "contractVersion": self.CONTRACT_VERSION,
        }
