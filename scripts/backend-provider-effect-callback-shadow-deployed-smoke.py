#!/usr/bin/env python3
"""Exercise the callback shadow inside the deployed API container only.

This smoke must run inside the deployed API container. It performs no HTTP,
database, Provider, credential, media, callback-route or reconciliation work.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectTarget
from app.async_effects.provider_effect_callback_shadow import (
    ProviderEffectCallbackAdmissionShadow,
    ProviderEffectCallbackCandidate,
    ProviderEffectCallbackShadowDisposition,
)
from app.async_effects.provider_effects import (
    ProviderEffectIntent,
    ProviderEffectReceipt,
    ProviderEffectState,
)


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _unknown_receipt() -> ProviderEffectReceipt:
    intent = ProviderEffectIntent(
        effect_intent=AsyncEffectIntent(
            operation_type="provider.callback.shadow.deployed",
            target=AsyncEffectTarget(
                owner_subject_id="owner-callback-smoke",
                vault_id="vault-callback-smoke",
                resource_type="voiceProfile",
                resource_id="voice-profile-callback-smoke",
                resource_version=1,
                purpose="voiceClone",
                authority_epoch=3,
            ),
            payload_hash=_digest("callback-shadow-deployed-parent"),
        ),
        provider="volcengineVoiceClone",
        capability="voiceCloneTraining",
        request_hash=_digest("callback-shadow-deployed-request"),
    )
    return ProviderEffectReceipt(
        intent=intent,
        state=ProviderEffectState.UNKNOWN,
        reason_code="providerTimeout",
        observation_origin="timeoutObservation",
    )


def main() -> None:
    prior = _unknown_receipt()
    candidate = ProviderEffectCallbackCandidate(
        provider=prior.intent.provider,
        provider_effect_key=prior.intent.provider_effect_key,
        request_hash=prior.intent.request_hash,
        callback_event_hash=_digest("callback-shadow-deployed-event"),
        provider_receipt_hash=_digest("callback-shadow-deployed-receipt"),
        reported_state=ProviderEffectState.COMPLETED,
        contract_version=prior.intent.contract_version,
    )
    shadow = ProviderEffectCallbackAdmissionShadow()
    first = shadow.observe(prior_unknown=prior, candidate=candidate, enabled=True)
    replay = shadow.observe(prior_unknown=prior, candidate=candidate, enabled=True)
    conflict = shadow.observe(
        prior_unknown=prior,
        candidate=ProviderEffectCallbackCandidate(
            provider=candidate.provider,
            provider_effect_key=candidate.provider_effect_key,
            request_hash=_digest("callback-shadow-deployed-rebound"),
            callback_event_hash=candidate.callback_event_hash,
            provider_receipt_hash=candidate.provider_receipt_hash,
            reported_state=candidate.reported_state,
            contract_version=candidate.contract_version,
        ),
        enabled=True,
    )
    assert first.disposition is ProviderEffectCallbackShadowDisposition.BLOCKED
    assert replay.disposition is ProviderEffectCallbackShadowDisposition.BLOCKED
    assert "callbackReplayObservedInMemory" in replay.reason_codes
    assert "callbackEventBindingConflict" in conflict.reason_codes
    summary = first.value_free_summary()
    assert summary["callbackAccepted"] is False
    assert summary["providerEffectReconciled"] is False
    assert summary["providerEffectPerformed"] is False
    assert summary["replayProtectionPersistent"] is False
    print(
        "providerEffectCallbackShadowG0=true "
        "status=blocked callbackAccepted=false providerEffectReconciled=false "
        "replayProtectionPersistent=false"
    )


if __name__ == "__main__":
    main()
