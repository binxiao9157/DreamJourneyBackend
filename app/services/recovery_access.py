from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class RecoveryAccessDecision:
    allowed: bool
    code: str
    reason: str


class RecoveryAccessPolicy:
    """Global, value-free traffic fence used during database recovery."""

    SCHEMA_VERSION = 1
    CONTRACT_VERSION = 1
    _VALID_MODES = frozenset({"normal", "readOnly", "signedOut", "maintenance"})
    _ALWAYS_AVAILABLE_PATHS = frozenset({"/health", "/live", "/ready", "/config/runtime"})
    _READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

    def __init__(self, *, mode: str, authority_epoch: str) -> None:
        normalized_mode = str(mode or "").strip()
        normalized_epoch = str(authority_epoch or "").strip()
        if normalized_mode not in self._VALID_MODES or not normalized_epoch:
            self.mode = "maintenance"
            self.authority_epoch = "invalid"
            self.configuration_valid = False
        else:
            self.mode = normalized_mode
            self.authority_epoch = normalized_epoch
            self.configuration_valid = True

    def evaluate(self, *, method: str, path: str) -> RecoveryAccessDecision:
        normalized_method = str(method or "").strip().upper()
        normalized_path = str(path or "").strip()
        if normalized_path in self._ALWAYS_AVAILABLE_PATHS:
            return RecoveryAccessDecision(True, "recoveryInfrastructureAllowed", "infrastructurePath")
        if self.mode == "normal":
            return RecoveryAccessDecision(True, "recoveryNormal", "normalOperation")
        if self.mode == "readOnly" and normalized_method in self._READ_METHODS:
            return RecoveryAccessDecision(True, "recoveryReadAllowed", "readOnlyOperation")
        if self.mode == "readOnly":
            return RecoveryAccessDecision(False, "recoveryWriteBlocked", "readOnlyRecoveryFence")
        return RecoveryAccessDecision(False, "recoveryMaintenance", f"{self.mode}RecoveryFence")

    def public_descriptor(self) -> Dict[str, Any]:
        writes_allowed = self.mode == "normal" and self.configuration_valid
        session_policy = {
            "normal": "preserve",
            "readOnly": "preserveReadOnly",
            "signedOut": "clear",
            "maintenance": "suspend",
        }[self.mode]
        return {
            "schemaVersion": self.SCHEMA_VERSION,
            "mode": self.mode,
            "authorityEpoch": self.authority_epoch,
            "writesAllowed": writes_allowed,
            "authenticatedSessionPolicy": session_policy,
            "cacheWritePolicy": "enabled" if writes_allowed else "disabled",
            "scope": "globalRecoveryFence",
            "configurationValid": self.configuration_valid,
            "contractOnly": True,
            "contractVersion": self.CONTRACT_VERSION,
        }
