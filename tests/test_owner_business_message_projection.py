from __future__ import annotations

from hashlib import sha256
import unittest

from app.async_effects.business_message_projection_request_repository import (
    InMemoryBusinessMessageProjectionRequestRepository,
)
from app.async_effects.consumer_repository import (
    AsyncEffectSyntheticConsumerCommand,
    InMemoryAsyncEffectConsumerRepository,
)
from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectTarget
from app.async_effects.legacy_identity_inbox_bridge import (
    InMemoryLegacyInboxAccountResolver,
    LegacyAliasClaimState,
    LegacyInboxAccountBinding,
)
from app.async_effects.message_notification_effects import InAppMessageKind
from app.async_effects.owner_business_message_projection import (
    OwnerBusinessMessageProjectionError,
    enqueue_owner_business_message,
)
from app.async_effects.repository import InMemoryEffectKernelRepository


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class _Store:
    def __init__(self, *, resolved_vault_id: str = "vault-message-owner") -> None:
        self.kernel = InMemoryEffectKernelRepository()
        self.inputs = InMemoryBusinessMessageProjectionRequestRepository()
        self.resolver = InMemoryLegacyInboxAccountResolver(
            [
                LegacyInboxAccountBinding(
                    legacy_user_id="legacy-message-owner",
                    legacy_alias_hash=_digest("legacy-message-owner"),
                    subject_id="owner-message-owner",
                    vault_id=resolved_vault_id,
                    claim_state=LegacyAliasClaimState.VERIFIED,
                    identity_proof_subject_id="owner-message-owner",
                    subject_state="active",
                    vault_owner_subject_id="owner-message-owner",
                    vault_state="active",
                    account_access_state="active",
                    account_deletion_state="active",
                    account_auth_epoch=4,
                    bridge_row_version=1,
                )
            ]
        )

    def effect_kernel_repository(self):
        return self.kernel

    def async_effect_business_message_projection_request_repository(self):
        return self.inputs

    def async_effect_legacy_inbox_account_resolver(self):
        return self.resolver


def _completed_source():
    intent = AsyncEffectIntent(
        operation_type="asyncEffect.synthetic.ownerTruth.source.created",
        target=AsyncEffectTarget(
            owner_subject_id="owner-message-owner",
            vault_id="vault-message-owner",
            resource_type="source",
            resource_id="source-message-owner",
            resource_version=1,
            purpose="candidateExtraction",
            authority_epoch=4,
        ),
        payload_hash=_digest("owner-message-source"),
    )
    completion = InMemoryAsyncEffectConsumerRepository().consume(
        AsyncEffectSyntheticConsumerCommand(
            intent=intent,
            consumer_name="ownerTruth.source.extraction",
            business_target_key=intent.business_target_key,
            outcome="completed",
            reason_code="candidateExtractionProposalsPersisted",
            result_ref_hash=_digest("owner-message-result"),
        )
    )
    return intent, completion


class OwnerBusinessMessageProjectionTests(unittest.TestCase):
    def test_explicitly_disabled_test_store_does_not_enqueue(self) -> None:
        store = _Store()
        store.business_message_projection_enabled = False
        intent, completion = _completed_source()

        self.assertIsNone(
            enqueue_owner_business_message(
                store,
                intent=intent,
                completion=completion,
                kind=InAppMessageKind.CANDIDATE_READY,
            )
        )
        self.assertEqual(store.kernel.record_count(), 0)
        self.assertEqual(store.inputs.request_count(), 0)

    def test_accepts_candidate_message_with_verified_owner_inbox_and_deduplicates(self) -> None:
        store = _Store()
        intent, completion = _completed_source()

        first = enqueue_owner_business_message(
            store,
            intent=intent,
            completion=completion,
            kind=InAppMessageKind.CANDIDATE_READY,
        )
        replay = enqueue_owner_business_message(
            store,
            intent=intent,
            completion=completion,
            kind=InAppMessageKind.CANDIDATE_READY,
        )

        self.assertEqual(first.outcome, "accepted")
        self.assertEqual(first.input_outcome, "recorded")
        self.assertEqual(replay.outcome, "deduplicated")
        self.assertEqual(replay.input_outcome, "deduplicated")
        self.assertEqual(store.kernel.record_count(), 1)
        self.assertEqual(store.inputs.request_count(), 1)

    def test_rejects_inbox_binding_for_another_vault(self) -> None:
        store = _Store(resolved_vault_id="vault-other-owner")
        intent, completion = _completed_source()

        with self.assertRaises(OwnerBusinessMessageProjectionError):
            enqueue_owner_business_message(
                store,
                intent=intent,
                completion=completion,
                kind=InAppMessageKind.CANDIDATE_READY,
            )

    def test_unsupported_test_double_does_not_claim_projection_success(self) -> None:
        intent, completion = _completed_source()

        self.assertIsNone(
            enqueue_owner_business_message(
                object(),
                intent=intent,
                completion=completion,
                kind=InAppMessageKind.CANDIDATE_READY,
            )
        )


if __name__ == "__main__":
    unittest.main()
