import unittest
from hashlib import sha256

from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectTarget
from app.async_effects.provider_effect_repository import (
    InMemoryProviderEffectRepository,
)
from app.async_effects.provider_effects import (
    ProviderEffectConflict,
    ProviderEffectContractError,
    ProviderEffectIntent,
    ProviderEffectQueryOutcome,
    ProviderEffectReconciliation,
    ProviderEffectReceipt,
    ProviderEffectState,
)


def _effect_intent() -> AsyncEffectIntent:
    return AsyncEffectIntent(
        operation_type="provider.effect.reconcile.fixture",
        target=AsyncEffectTarget(
            owner_subject_id="owner-provider-001",
            vault_id="vault-provider-001",
            resource_type="voiceProfile",
            resource_id="voice-profile-provider-001",
            resource_version=2,
            purpose="voiceClone",
            authority_epoch=4,
        ),
        payload_hash=sha256(b"provider-effect-parent-payload").hexdigest(),
    )


def _intent(request_material: bytes = b"canonical-provider-request") -> ProviderEffectIntent:
    return ProviderEffectIntent(
        effect_intent=_effect_intent(),
        provider="volcengineVoiceClone",
        capability="voiceCloneTraining",
        request_hash=sha256(request_material).hexdigest(),
    )


def _receipt(
    intent: ProviderEffectIntent,
    state: ProviderEffectState,
    *,
    reason_code: str,
    observation_origin: str,
    attempt: int = 1,
) -> ProviderEffectReceipt:
    return ProviderEffectReceipt(
        intent=intent,
        state=state,
        reason_code=reason_code,
        observation_origin=observation_origin,
        attempt=attempt,
    )


class ProviderEffectRepositoryTests(unittest.TestCase):
    def test_timeout_then_query_completion_preserves_unknown_fact_and_projects_effective_state(self):
        repository = InMemoryProviderEffectRepository()
        intent = _intent()
        accepted = _receipt(
            intent,
            ProviderEffectState.ACCEPTED,
            reason_code="providerAccepted",
            observation_origin="providerSubmission",
        )
        unknown = _receipt(
            intent,
            ProviderEffectState.UNKNOWN,
            reason_code="providerTimeout",
            observation_origin="timeoutObservation",
            attempt=2,
        )

        self.assertEqual(repository.record(accepted).outcome, "accepted")
        timed_out = repository.record(unknown)
        self.assertEqual(timed_out.effect_state, ProviderEffectState.UNKNOWN)
        self.assertEqual(timed_out.effective_state, ProviderEffectState.UNKNOWN)

        reconciliation = ProviderEffectReconciliation(
            prior_unknown=unknown,
            outcome=ProviderEffectQueryOutcome.COMPLETED,
            query_receipt_hash=sha256(b"query-completed").hexdigest(),
        )
        first = repository.reconcile(reconciliation)
        replay = repository.reconcile(reconciliation)

        self.assertEqual(first.outcome, "reconciled")
        self.assertEqual(replay.outcome, "deduplicated")
        self.assertEqual(first.effect_state, ProviderEffectState.UNKNOWN)
        self.assertEqual(first.effective_state, ProviderEffectState.COMPLETED)
        self.assertFalse(first.reissue_allowed)
        self.assertEqual(repository.effect_state(intent), ProviderEffectState.UNKNOWN)
        self.assertEqual(repository.effective_state(intent), ProviderEffectState.COMPLETED)

    def test_same_effect_key_with_changed_request_hash_is_rejected(self):
        repository = InMemoryProviderEffectRepository()
        first = _intent(b"request-a")
        changed = _intent(b"request-b")

        repository.record(
            _receipt(
                first,
                ProviderEffectState.ACCEPTED,
                reason_code="providerAccepted",
                observation_origin="providerSubmission",
            )
        )

        with self.assertRaises(ProviderEffectConflict):
            repository.record(
                _receipt(
                    changed,
                    ProviderEffectState.ACCEPTED,
                    reason_code="providerAccepted",
                    observation_origin="providerSubmission",
                )
            )

    def test_unknown_query_requires_manual_review_and_conflicting_terminal_evidence_fails_closed(self):
        repository = InMemoryProviderEffectRepository()
        intent = _intent()
        unknown = _receipt(
            intent,
            ProviderEffectState.UNKNOWN,
            reason_code="providerTimeout",
            observation_origin="timeoutObservation",
        )
        repository.record(unknown)

        still_unknown = ProviderEffectReconciliation(
            prior_unknown=unknown,
            outcome=ProviderEffectQueryOutcome.STILL_UNKNOWN,
            query_receipt_hash=sha256(b"query-still-unknown").hexdigest(),
        )
        manual = repository.reconcile(still_unknown)
        self.assertTrue(manual.requires_manual_review)
        self.assertFalse(manual.reissue_allowed)
        self.assertEqual(manual.effective_state, ProviderEffectState.UNKNOWN)

        resolved = ProviderEffectReconciliation(
            prior_unknown=unknown,
            outcome=ProviderEffectQueryOutcome.COMPLETED,
            query_receipt_hash=sha256(b"query-completed").hexdigest(),
        )
        repository.reconcile(resolved)

        conflict = repository.reconcile(
            ProviderEffectReconciliation(
                prior_unknown=unknown,
                outcome=ProviderEffectQueryOutcome.FAILED,
                query_receipt_hash=sha256(b"query-conflicting-failure").hexdigest(),
            )
        )
        self.assertEqual(conflict.effective_state, ProviderEffectState.UNKNOWN)
        self.assertEqual(conflict.reconciliation_status, "reconciliationConflict")
        self.assertTrue(conflict.requires_manual_review)

    def test_reconciliation_requires_a_durably_recorded_unknown_receipt(self):
        repository = InMemoryProviderEffectRepository()
        intent = _intent()
        accepted = _receipt(
            intent,
            ProviderEffectState.ACCEPTED,
            reason_code="providerAccepted",
            observation_origin="providerSubmission",
        )
        repository.record(accepted)

        with self.assertRaises(ProviderEffectContractError):
            repository.reconcile(
                ProviderEffectReconciliation(
                    prior_unknown=_receipt(
                        intent,
                        ProviderEffectState.UNKNOWN,
                        reason_code="providerTimeout",
                        observation_origin="timeoutObservation",
                    ),
                    outcome=ProviderEffectQueryOutcome.COMPLETED,
                    query_receipt_hash=sha256(b"query-completed").hexdigest(),
                )
            )

    def test_persistence_summary_is_value_free(self):
        repository = InMemoryProviderEffectRepository()
        intent = _intent()
        summary = repository.record(
            _receipt(
                intent,
                ProviderEffectState.UNKNOWN,
                reason_code="providerTimeout",
                observation_origin="timeoutObservation",
            )
        ).value_free_summary()

        self.assertEqual(summary["effectState"], "unknown")
        self.assertEqual(summary["effectiveState"], "unknown")
        self.assertNotIn("requestBody", summary)
        self.assertNotIn("providerRequestId", summary)
        self.assertNotIn("canonical-provider-request", str(summary))

    def test_reconciliation_backlog_aggregates_only_effective_unknowns(self):
        repository = InMemoryProviderEffectRepository()
        pending_intent = _intent()
        resolved_intent = ProviderEffectIntent(
            effect_intent=_effect_intent(),
            provider="volcengineVoiceClone",
            capability="voiceCloneSynthesis",
            request_hash=sha256(b"provider-effect-resolved").hexdigest(),
        )
        pending_unknown = _receipt(
            pending_intent,
            ProviderEffectState.UNKNOWN,
            reason_code="providerTimeout",
            observation_origin="timeoutObservation",
        )
        resolved_unknown = _receipt(
            resolved_intent,
            ProviderEffectState.UNKNOWN,
            reason_code="providerTimeout",
            observation_origin="timeoutObservation",
        )
        repository.record(pending_unknown)
        repository.record(resolved_unknown)
        repository.reconcile(
            ProviderEffectReconciliation(
                prior_unknown=resolved_unknown,
                outcome=ProviderEffectQueryOutcome.COMPLETED,
                query_receipt_hash=sha256(b"provider-effect-resolved-query").hexdigest(),
            )
        )

        backlog = repository.reconciliation_backlog()

        self.assertEqual(len(backlog), 1)
        self.assertEqual(backlog[0].provider, "volcengineVoiceClone")
        self.assertEqual(backlog[0].capability, "voiceCloneTraining")
        self.assertEqual(backlog[0].unknown_effect_count, 1)
        self.assertEqual(backlog[0].pending_reconciliation_count, 1)
        self.assertEqual(backlog[0].manual_review_count, 0)
        self.assertEqual(backlog[0].reconciliation_conflict_count, 0)
        self.assertNotIn("owner-provider-001", str(backlog[0].value_free_summary()))


if __name__ == "__main__":
    unittest.main()
