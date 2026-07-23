"""G0 tests for the default-deny Provider callback reconciliation shadow."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import unittest

from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectTarget
from app.async_effects.provider_effects import (
    ProviderEffectIntent,
    ProviderEffectReceipt,
    ProviderEffectState,
)
from app.async_effects.provider_effect_callback_shadow import (
    ProviderEffectCallbackAdmissionShadow,
    ProviderEffectCallbackCandidate,
    ProviderEffectCallbackShadowDisposition,
    ProviderEffectCallbackShadowError,
)


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _intent() -> ProviderEffectIntent:
    return ProviderEffectIntent(
        effect_intent=AsyncEffectIntent(
            operation_type="provider.callback.shadow.fixture",
            target=AsyncEffectTarget(
                owner_subject_id="owner-callback",
                vault_id="vault-callback",
                resource_type="voiceProfile",
                resource_id="voice-profile-callback",
                resource_version=1,
                purpose="voiceClone",
                authority_epoch=3,
            ),
            payload_hash=_digest("callback-shadow-parent-payload"),
        ),
        provider="volcengineVoiceClone",
        capability="voiceCloneTraining",
        request_hash=_digest("callback-shadow-request"),
    )


def _unknown_receipt(**changes: object) -> ProviderEffectReceipt:
    values: dict[str, object] = {
        "intent": _intent(),
        "state": ProviderEffectState.UNKNOWN,
        "reason_code": "providerTimeout",
        "attempt": 2,
        "observation_origin": "timeoutObservation",
    }
    values.update(changes)
    return ProviderEffectReceipt(**values)  # type: ignore[arg-type]


def _candidate(**changes: object) -> ProviderEffectCallbackCandidate:
    intent = _intent()
    values: dict[str, object] = {
        "provider": intent.provider,
        "provider_effect_key": intent.provider_effect_key,
        "request_hash": intent.request_hash,
        "callback_event_hash": _digest("provider-callback-event"),
        "provider_receipt_hash": _digest("provider-callback-receipt"),
        "reported_state": ProviderEffectState.COMPLETED,
        "contract_version": intent.contract_version,
    }
    values.update(changes)
    return ProviderEffectCallbackCandidate(**values)  # type: ignore[arg-type]


class ProviderEffectCallbackShadowTests(unittest.TestCase):
    def test_disabled_path_does_not_observe_callback_identity(self) -> None:
        observer = ProviderEffectCallbackAdmissionShadow()
        disabled = observer.observe(prior_unknown=object(), candidate=object())
        self.assertEqual(disabled.disposition, ProviderEffectCallbackShadowDisposition.SHADOW_DISABLED)

        first = observer.observe(
            prior_unknown=_unknown_receipt(),
            candidate=_candidate(),
            enabled=True,
        )
        self.assertNotIn("callbackReplayObservedInMemory", first.reason_codes)

    def test_matching_unknown_effect_remains_blocked_and_value_free(self) -> None:
        prior = _unknown_receipt()
        candidate = _candidate()
        result = ProviderEffectCallbackAdmissionShadow().observe(
            prior_unknown=prior,
            candidate=candidate,
            enabled=True,
        )

        self.assertEqual(result.disposition, ProviderEffectCallbackShadowDisposition.BLOCKED)
        self.assertIn("g0NoCallbackRoute", result.reason_codes)
        self.assertIn("g2DurableUnknownEffectLookupRequired", result.reason_codes)
        self.assertIn("g3ProviderCallbackVerificationRequired", result.reason_codes)
        summary = result.value_free_summary()
        self.assertFalse(summary["callbackAccepted"])
        self.assertFalse(summary["providerEffectReconciled"])
        self.assertFalse(summary["providerEffectPerformed"])
        self.assertFalse(summary["replayProtectionPersistent"])
        self.assertFalse(summary["releaseVisible"])
        for private_value in (
            prior.intent.request_hash,
            candidate.callback_event_hash,
            candidate.provider_receipt_hash,
        ):
            self.assertNotIn(private_value, repr(summary))

    def test_cross_provider_effect_request_and_contract_mismatches_never_admit(self) -> None:
        prior = _unknown_receipt()
        cases = {
            "provider": (
                _candidate(provider="otherProvider"),
                "callbackProviderMismatch",
            ),
            "effect": (
                _candidate(provider_effect_key=_digest("other-provider-effect")),
                "callbackEffectKeyMismatch",
            ),
            "request": (
                _candidate(request_hash=_digest("other-request")),
                "callbackRequestHashMismatch",
            ),
            "contract": (
                _candidate(contract_version="provider-effect-v2"),
                "callbackContractVersionMismatch",
            ),
        }
        for name, (candidate, expected_reason) in cases.items():
            with self.subTest(name=name):
                result = ProviderEffectCallbackAdmissionShadow().observe(
                    prior_unknown=prior,
                    candidate=candidate,
                    enabled=True,
                )
                self.assertEqual(result.disposition, ProviderEffectCallbackShadowDisposition.BLOCKED)
                self.assertIn(expected_reason, result.reason_codes)
                self.assertFalse(result.value_free_summary()["providerEffectReconciled"])

    def test_replay_and_rebound_callback_event_are_observed_but_never_consumed(self) -> None:
        observer = ProviderEffectCallbackAdmissionShadow()
        prior = _unknown_receipt()
        first_candidate = _candidate()
        first = observer.observe(prior_unknown=prior, candidate=first_candidate, enabled=True)
        replay = observer.observe(prior_unknown=prior, candidate=first_candidate, enabled=True)
        rebound = observer.observe(
            prior_unknown=prior,
            candidate=_candidate(request_hash=_digest("rebound-request")),
            enabled=True,
        )

        self.assertNotIn("callbackReplayObservedInMemory", first.reason_codes)
        self.assertIn("callbackReplayObservedInMemory", replay.reason_codes)
        self.assertIn("callbackReplayObservedInMemory", rebound.reason_codes)
        self.assertIn("callbackEventBindingConflict", rebound.reason_codes)
        self.assertFalse(replay.value_free_summary()["replayProtectionPersistent"])

    def test_non_unknown_prior_receipt_is_blocked_without_reconciliation(self) -> None:
        result = ProviderEffectCallbackAdmissionShadow().observe(
            prior_unknown=_unknown_receipt(
                state=ProviderEffectState.ACCEPTED,
                reason_code="providerAccepted",
                observation_origin="providerSubmission",
            ),
            candidate=_candidate(),
            enabled=True,
        )
        self.assertEqual(result.disposition, ProviderEffectCallbackShadowDisposition.BLOCKED)
        self.assertIn("priorUnknownReceiptRequired", result.reason_codes)
        self.assertFalse(result.value_free_summary()["providerEffectReconciled"])

    def test_candidate_rejects_nonterminal_state_and_raw_values(self) -> None:
        with self.assertRaises(ProviderEffectCallbackShadowError):
            _candidate(reported_state=ProviderEffectState.UNKNOWN)
        with self.assertRaises(ProviderEffectCallbackShadowError):
            _candidate(provider="provider with spaces")
        with self.assertRaises(ProviderEffectCallbackShadowError):
            _candidate(callback_event_hash="provider callback body")

    def test_invalid_context_is_value_free_and_default_denied(self) -> None:
        result = ProviderEffectCallbackAdmissionShadow().observe(
            prior_unknown=_unknown_receipt(),
            candidate=object(),
            enabled=True,
        )
        self.assertEqual(result.disposition, ProviderEffectCallbackShadowDisposition.INVALID_CONTEXT)
        self.assertFalse(result.value_free_summary()["callbackAccepted"])

    def test_module_does_not_import_callback_network_signature_or_persistence_clients(self) -> None:
        source = (
            Path(__file__).parents[1] / "app/async_effects/provider_effect_callback_shadow.py"
        ).read_text(encoding="utf-8")
        for forbidden in (
            "FastAPI",
            "requests",
            "httpx",
            "urllib.request",
            "hmac",
            "psycopg",
            "sqlite3",
            "ProviderEffectReconciliation",
        ):
            self.assertNotIn(forbidden, source)

    def test_deployed_smoke_is_container_only_and_side_effect_free(self) -> None:
        source = (
            Path(__file__).parents[1]
            / "scripts/backend-provider-effect-callback-shadow-deployed-smoke.py"
        ).read_text(encoding="utf-8")
        self.assertIn("must run inside the deployed API container", source)
        self.assertIn("ProviderEffectCallbackAdmissionShadow", source)
        self.assertIn("sys.path.insert(0, str(ROOT_DIR))", source)
        for forbidden in (
            "FastAPI",
            "requests",
            "httpx",
            "urllib.request",
            "hmac",
            "psycopg",
            "sqlite3",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
