import unittest
from hashlib import sha256

from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectTarget
from app.async_effects.provider_effects import (
    PROVIDER_EFFECT_CATALOG,
    ProviderEffectCatalogEntry,
    ProviderEffectConflict,
    ProviderEffectContractError,
    ProviderEffectIntent,
    ProviderEffectQueryOutcome,
    ProviderEffectReconciliation,
    ProviderEffectReceipt,
    ProviderEffectState,
    assert_same_provider_request,
    provider_effect_catalog_summary,
)


def _effect_intent() -> AsyncEffectIntent:
    return AsyncEffectIntent(
        operation_type="provider.effect.test",
        target=AsyncEffectTarget(
            owner_subject_id="owner-001",
            vault_id="vault-001",
            resource_type="voiceProfile",
            resource_id="voice-profile-001",
            resource_version=3,
            purpose="voiceClone",
            authority_epoch=2,
        ),
        payload_hash=sha256(b"canonical-provider-input").hexdigest(),
    )


class ProviderEffectIntentTests(unittest.TestCase):
    def test_stable_request_identity_is_deterministic_and_hash_bound(self):
        first = ProviderEffectIntent(
            effect_intent=_effect_intent(),
            provider="volcengineVoiceClone",
            capability="voiceCloneTraining",
            request_hash=sha256(b"request-a").hexdigest(),
        )
        replay = ProviderEffectIntent(
            effect_intent=_effect_intent(),
            provider="volcengineVoiceClone",
            capability="voiceCloneTraining",
            request_hash=sha256(b"request-a").hexdigest(),
        )
        changed = ProviderEffectIntent(
            effect_intent=_effect_intent(),
            provider="volcengineVoiceClone",
            capability="voiceCloneTraining",
            request_hash=sha256(b"request-b").hexdigest(),
        )

        self.assertEqual(first.provider_effect_key, replay.provider_effect_key)
        self.assertEqual(first.provider_request_id, replay.provider_request_id)
        self.assertNotEqual(first.provider_request_id, changed.provider_request_id)
        self.assertNotEqual(first.immutable_fingerprint, changed.immutable_fingerprint)

        assert_same_provider_request(first, replay)
        with self.assertRaises(ProviderEffectConflict):
            assert_same_provider_request(first, changed)

    def test_value_free_summary_never_contains_provider_body_fields(self):
        intent = ProviderEffectIntent(
            effect_intent=_effect_intent(),
            provider="deepseek",
            capability="kbExtract",
            request_hash=sha256(b"private prompt is hashed before this boundary").hexdigest(),
        )

        summary = intent.value_free_summary()

        self.assertEqual(set(summary), {
            "capability",
            "contractVersion",
            "operationId",
            "operationStableKey",
            "provider",
            "providerEffectKey",
            "providerRequestIdHash",
            "requestHash",
            "schemaVersion",
        })
        self.assertNotIn("private prompt", str(summary))
        self.assertNotIn("providerRequestId", summary)

    def test_rejects_invalid_request_hash_and_machine_identifiers(self):
        with self.assertRaises(ProviderEffectContractError):
            ProviderEffectIntent(
                effect_intent=_effect_intent(),
                provider="provider with spaces",
                capability="kbExtract",
                request_hash="not-a-hash",
            )


class ProviderEffectReconciliationTests(unittest.TestCase):
    def _unknown_receipt(self) -> ProviderEffectReceipt:
        intent = ProviderEffectIntent(
            effect_intent=_effect_intent(),
            provider="volcengineVoiceClone",
            capability="voiceCloneTraining",
            request_hash=sha256(b"training-request").hexdigest(),
        )
        return ProviderEffectReceipt(
            intent=intent,
            state=ProviderEffectState.UNKNOWN,
            reason_code="providerTimeout",
            attempt=2,
        )

    def test_query_completion_resolves_unknown_without_reissuing(self):
        reconciliation = ProviderEffectReconciliation(
            prior_unknown=self._unknown_receipt(),
            outcome=ProviderEffectQueryOutcome.COMPLETED,
            query_receipt_hash=sha256(b"provider-query-completed").hexdigest(),
        )

        terminal = reconciliation.terminal_receipt()

        self.assertEqual(reconciliation.result_state, ProviderEffectState.COMPLETED)
        self.assertEqual(terminal.state, ProviderEffectState.COMPLETED)
        self.assertEqual(terminal.reason_code, "providerQueryCompleted")
        self.assertFalse(reconciliation.reissue_allowed)
        self.assertFalse(reconciliation.requires_manual_review)

    def test_unsupported_or_still_unknown_query_requires_manual_review_without_resend(self):
        for outcome in (
            ProviderEffectQueryOutcome.STILL_UNKNOWN,
            ProviderEffectQueryOutcome.UNSUPPORTED,
        ):
            with self.subTest(outcome=outcome):
                reconciliation = ProviderEffectReconciliation(
                    prior_unknown=self._unknown_receipt(),
                    outcome=outcome,
                    query_receipt_hash=sha256(outcome.value.encode("utf-8")).hexdigest(),
                )
                self.assertEqual(reconciliation.result_state, ProviderEffectState.UNKNOWN)
                self.assertTrue(reconciliation.requires_manual_review)
                self.assertFalse(reconciliation.reissue_allowed)

    def test_reconciliation_rejects_non_unknown_prior_state(self):
        unknown = self._unknown_receipt()
        completed = ProviderEffectReceipt(
            intent=unknown.intent,
            state=ProviderEffectState.COMPLETED,
            reason_code="providerCompleted",
        )

        with self.assertRaises(ProviderEffectContractError):
            ProviderEffectReconciliation(
                prior_unknown=completed,
                outcome=ProviderEffectQueryOutcome.COMPLETED,
                query_receipt_hash=sha256(b"query").hexdigest(),
            )


class ProviderEffectCatalogTests(unittest.TestCase):
    def test_catalog_is_sorted_complete_and_default_off_for_future_effects(self):
        summary = provider_effect_catalog_summary()

        self.assertEqual(summary["entryCount"], 10)
        self.assertFalse(summary["providerCallsEnabledByCatalog"])
        self.assertEqual(
            [entry.key for entry in PROVIDER_EFFECT_CATALOG],
            sorted(entry.key for entry in PROVIDER_EFFECT_CATALOG),
        )
        catalog = {entry.key: entry for entry in PROVIDER_EFFECT_CATALOG}
        self.assertEqual(catalog["amap.districtLookup"].migration_disposition, "keepReadOnlySync")
        self.assertEqual(catalog["deepseek.archiveImageAnalysis"].current_execution, "providerUnsupported")
        self.assertEqual(catalog["tencent.digitalHumanSession"].current_execution, "credentialBrokerBlocked")
        self.assertEqual(catalog["apns.delivery"].current_execution, "notImplemented")
        self.assertTrue(catalog["volcengineVoiceClone.training"].requires_stable_provider_effect)

    def test_catalog_rejects_unsorted_or_duplicate_entries(self):
        entry = ProviderEffectCatalogEntry(
            key="fixture.provider",
            provider="fixture",
            capability="fixtureCapability",
            source_paths=("app/fixture.py:provider",),
            current_execution="mockOnly",
            request_id_strategy="none",
            query_reconcile_support="notApplicable",
            migration_disposition="stableRequestBeforeEnable",
            default_exposure="defaultOff",
        )

        with self.assertRaises(ProviderEffectContractError):
            provider_effect_catalog_summary((entry, entry))


if __name__ == "__main__":
    unittest.main()
