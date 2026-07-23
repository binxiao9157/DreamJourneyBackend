"""Default-deny G0 observer for future Provider callback reconciliation.

This module deliberately models only value-free callback binding vocabulary.
It is not a webhook handler, signature verifier, Provider client, repository,
or reconciliation executor. Every observation remains blocked until a later
durable effect lookup, replay ledger, and Provider callback contract exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from threading import RLock

from app.async_effects.provider_effects import ProviderEffectReceipt, ProviderEffectState


PROVIDER_EFFECT_CALLBACK_SHADOW_SCHEMA_VERSION = "provider-effect-callback-shadow-v1"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_TERMINAL_CALLBACK_STATES = {
    ProviderEffectState.COMPLETED,
    ProviderEffectState.FAILED,
}


class ProviderEffectCallbackShadowError(ValueError):
    """Raised when the value-minimized callback shadow contract is malformed."""


class ProviderEffectCallbackShadowDisposition(str, Enum):
    SHADOW_DISABLED = "shadow_disabled"
    INVALID_CONTEXT = "invalid_context"
    BLOCKED = "blocked"


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise ProviderEffectCallbackShadowError(f"{field} must be an opaque identifier")
    return normalized


def _hash(value: object, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _HASH_PATTERN.fullmatch(normalized):
        raise ProviderEffectCallbackShadowError(f"{field} must be a lowercase SHA-256 digest")
    return normalized


@dataclass(frozen=True)
class ProviderEffectCallbackCandidate:
    """Hash-only callback claim; raw body, headers and signature are excluded."""

    provider: str
    provider_effect_key: str
    request_hash: str
    callback_event_hash: str
    provider_receipt_hash: str
    reported_state: ProviderEffectState
    contract_version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _identifier(self.provider, field="provider"))
        object.__setattr__(
            self,
            "contract_version",
            _identifier(self.contract_version, field="contract_version"),
        )
        for field in (
            "provider_effect_key",
            "request_hash",
            "callback_event_hash",
            "provider_receipt_hash",
        ):
            object.__setattr__(self, field, _hash(getattr(self, field), field=field))
        if self.reported_state not in _TERMINAL_CALLBACK_STATES:
            raise ProviderEffectCallbackShadowError(
                "reported_state must be a terminal completed or failed state"
            )


@dataclass(frozen=True)
class ProviderEffectCallbackAdmissionResult:
    disposition: ProviderEffectCallbackShadowDisposition
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.disposition, ProviderEffectCallbackShadowDisposition):
            raise TypeError("callback shadow disposition is required")
        normalized = tuple(sorted({_identifier(code, field="reason_code") for code in self.reason_codes}))
        if not normalized:
            raise ProviderEffectCallbackShadowError("at least one reason code is required")
        object.__setattr__(self, "reason_codes", normalized)

    def value_free_summary(self) -> dict[str, object]:
        return {
            "callbackAccepted": False,
            "providerEffectPerformed": False,
            "providerEffectReconciled": False,
            "reasonCodes": list(self.reason_codes),
            "replayProtectionPersistent": False,
            "releaseVisible": False,
            "schemaVersion": PROVIDER_EFFECT_CALLBACK_SHADOW_SCHEMA_VERSION,
            "status": self.disposition.value,
        }


class ProviderEffectCallbackAdmissionShadow:
    """In-memory G0 callback observer; never a callback processor or reconciler."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._observed_callback_bindings: dict[str, tuple[str, str, str, str, str]] = {}

    def observe(
        self,
        *,
        prior_unknown: ProviderEffectReceipt | object,
        candidate: ProviderEffectCallbackCandidate | object,
        enabled: object = False,
    ) -> ProviderEffectCallbackAdmissionResult:
        if enabled is not True:
            return ProviderEffectCallbackAdmissionResult(
                disposition=ProviderEffectCallbackShadowDisposition.SHADOW_DISABLED,
                reason_codes=("providerCallbackShadowDisabled",),
            )
        if not isinstance(prior_unknown, ProviderEffectReceipt) or not isinstance(
            candidate,
            ProviderEffectCallbackCandidate,
        ):
            return ProviderEffectCallbackAdmissionResult(
                disposition=ProviderEffectCallbackShadowDisposition.INVALID_CONTEXT,
                reason_codes=("invalidProviderCallbackContext",),
            )

        reasons: set[str] = {
            "g0NoCallbackRoute",
            "g2DurableEffectBindingRequired",
            "g2DurableUnknownEffectLookupRequired",
            "g3ProviderCallbackVerificationRequired",
            "replayProtectionNotPersistent",
            "releasePolicyDefaultOff",
        }
        intent = prior_unknown.intent
        if prior_unknown.state is not ProviderEffectState.UNKNOWN:
            reasons.add("priorUnknownReceiptRequired")
        if candidate.provider != intent.provider:
            reasons.add("callbackProviderMismatch")
        if candidate.provider_effect_key != intent.provider_effect_key:
            reasons.add("callbackEffectKeyMismatch")
        if candidate.request_hash != intent.request_hash:
            reasons.add("callbackRequestHashMismatch")
        if candidate.contract_version != intent.contract_version:
            reasons.add("callbackContractVersionMismatch")

        binding = (
            candidate.provider,
            candidate.provider_effect_key,
            candidate.request_hash,
            candidate.provider_receipt_hash,
            candidate.reported_state.value,
        )
        with self._lock:
            previous_binding = self._observed_callback_bindings.get(candidate.callback_event_hash)
            if previous_binding is None:
                self._observed_callback_bindings[candidate.callback_event_hash] = binding
            else:
                reasons.add("callbackReplayObservedInMemory")
                if previous_binding != binding:
                    reasons.add("callbackEventBindingConflict")

        return ProviderEffectCallbackAdmissionResult(
            disposition=ProviderEffectCallbackShadowDisposition.BLOCKED,
            reason_codes=tuple(reasons),
        )


__all__ = [
    "PROVIDER_EFFECT_CALLBACK_SHADOW_SCHEMA_VERSION",
    "ProviderEffectCallbackAdmissionResult",
    "ProviderEffectCallbackAdmissionShadow",
    "ProviderEffectCallbackCandidate",
    "ProviderEffectCallbackShadowDisposition",
    "ProviderEffectCallbackShadowError",
]
