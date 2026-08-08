import hashlib
import json
import unittest
from typing import Optional

from app.services.data_rights_contract import DataRightsRequestAuthority
from app.services.data_rights_external_effect_receipts import (
    DataRightsExternalEffectReceipt,
)
from app.services.in_memory_store import InMemoryStore


class DataRightsExternalEffectReceiptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryStore()
        request = DataRightsRequestAuthority().create_request(
            command_id="external-effect-receipt-command",
            subject_id="external-effect-owner",
            identity_proof={"kind": "reauthenticated"},
            payload={"action": "account.delete", "scope": ["voice"]},
            now="2026-08-05T08:00:00+00:00",
        ).request
        self.store.create_rights_request(request)
        self.request_id = request.request_id
        self.owner_hash = request.subject_hash

    def _receipt(
        self,
        *,
        state: str = "accepted",
        owner_hash: Optional[str] = None,
    ) -> DataRightsExternalEffectReceipt:
        return DataRightsExternalEffectReceipt(
            request_id=self.request_id,
            owner_subject_hash=owner_hash or self.owner_hash,
            domain="providerVoice",
            effect_identity_hash=hashlib.sha256(b"voice-effect").hexdigest(),
            state=state,
            provider_receipt_present=state == "completed",
            reason_code="providerObservation",
            observed_at="2026-08-05T08:01:00+00:00",
            evidence_hash=(
                hashlib.sha256(b"provider-receipt").hexdigest()
                if state == "completed"
                else None
            ),
        )

    def test_replay_is_idempotent_and_state_transition_is_append_only(self) -> None:
        accepted = self._receipt()
        first = self.store.record_rights_external_effect_receipt(accepted)
        replay = self.store.record_rights_external_effect_receipt(accepted)
        completed = self.store.record_rights_external_effect_receipt(
            self._receipt(state="completed")
        )
        observations = self.store.list_rights_external_effect_receipts(self.request_id)

        self.assertEqual(first["outcome"], "appended")
        self.assertEqual(replay["outcome"], "deduplicated")
        self.assertEqual(completed["outcome"], "appended")
        self.assertEqual([item["state"] for item in observations], ["accepted", "completed"])
        self.assertTrue(observations[-1]["providerReceiptPresent"])
        self.assertNotIn("effectIdentityHash", observations[-1])
        self.assertNotIn(self.owner_hash, json.dumps(observations, sort_keys=True))
        self.assertNotIn(self.owner_hash, json.dumps(first["receipt"], sort_keys=True))

    def test_same_logical_callback_with_later_observation_time_is_deduplicated(self) -> None:
        accepted = self._receipt()
        later = DataRightsExternalEffectReceipt(
            request_id=accepted.request_id,
            owner_subject_hash=accepted.owner_subject_hash,
            domain=accepted.domain,
            effect_identity_hash=accepted.effect_identity_hash,
            state=accepted.state,
            provider_receipt_present=accepted.provider_receipt_present,
            reason_code=accepted.reason_code,
            observed_at="2026-08-05T08:02:00+00:00",
            evidence_hash=accepted.evidence_hash,
        )

        first = self.store.record_rights_external_effect_receipt(accepted)
        replay = self.store.record_rights_external_effect_receipt(later)

        self.assertEqual(first["outcome"], "appended")
        self.assertEqual(replay["outcome"], "deduplicated")
        self.assertEqual(
            len(self.store.list_rights_external_effect_receipts(self.request_id)),
            1,
        )

    def test_cross_account_observation_is_rejected_before_projection(self) -> None:
        with self.assertRaisesRegex(ValueError, "owner does not match"):
            self.store.record_rights_external_effect_receipt(
                self._receipt(owner_hash=hashlib.sha256(b"other-owner").hexdigest())
            )


if __name__ == "__main__":
    unittest.main()
