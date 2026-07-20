"""G0 contracts for future immutable Self Persona version/receipt records."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from unittest.mock import patch
import unittest

from app.services.owner_truth_persona_authority_command_shadow import (
    OwnerTruthPersonaAuthorityCommandContext,
    OwnerTruthPersonaAuthorityCommandOrigin,
)
from app.services.owner_truth_persona_authority_receipt_shadow import (
    OwnerTruthPersonaAuthorityReceiptDisposition,
    plan_self_persona_authority_receipt,
)


_PERSONA_ID = "6d582ac7-f5c4-4e6f-9c8a-4890799f1c65"


def _context(
    *,
    current_persona_version: int = 2,
    authority_epoch: int = 7,
) -> OwnerTruthPersonaAuthorityCommandContext:
    return OwnerTruthPersonaAuthorityCommandContext(
        vault_id="vault-persona-receipt-a",
        owner_subject_id="owner-persona-receipt-a",
        actor_subject_id="owner-persona-receipt-a",
        current_persona_version=current_persona_version,
        authority_epoch=authority_epoch,
    )


def _command(*, expected_version: int = 2) -> dict[str, object]:
    return {
        "commandId": "persona-receipt-command-a",
        "expectedVersion": expected_version,
        "personaId": _PERSONA_ID,
        "profile": {
            "birthDate": "1950-01-01",
            "displayName": "不应出现在 Persona receipt 摘要中的称呼",
            "gender": "女",
        },
    }


class OwnerTruthPersonaAuthorityReceiptShadowTests(unittest.TestCase):
    def _plan(self, payload: object, *, context: object | None = None):
        return plan_self_persona_authority_receipt(
            payload,
            context=_context() if context is None else context,
            enabled=True,
        )

    def test_disabled_path_returns_without_calling_command_preflight(self) -> None:
        with patch(
            "app.services.owner_truth_persona_authority_receipt_shadow."
            "preflight_self_persona_authority_command"
        ) as preflight:
            plan = plan_self_persona_authority_receipt(
                {"unexpected": object()},
                context=object(),
            )

        preflight.assert_not_called()
        self.assertEqual(
            plan.disposition,
            OwnerTruthPersonaAuthorityReceiptDisposition.SHADOW_DISABLED,
        )
        self.assertIsNone(plan.persona_version)
        self.assertIsNone(plan.decision_receipt)
        self.assertFalse(plan.records_written)

    def test_accepted_command_builds_immutable_future_records_only(self) -> None:
        plan = self._plan(_command())

        self.assertEqual(
            plan.disposition,
            OwnerTruthPersonaAuthorityReceiptDisposition.PLANNED_FOR_FUTURE_PERSISTENCE,
        )
        self.assertIsNotNone(plan.persona_version)
        self.assertIsNotNone(plan.decision_receipt)
        assert plan.persona_version is not None
        assert plan.decision_receipt is not None
        self.assertEqual(plan.persona_version.version_number, 3)
        self.assertEqual(plan.persona_version.expected_prior_version, 2)
        self.assertEqual(plan.persona_version.authority_epoch, 7)
        self.assertEqual(plan.decision_receipt.decision, "confirm")
        self.assertTrue(plan.decision_receipt.is_terminal)
        self.assertEqual(plan.decision_receipt.after_version, 3)
        self.assertEqual(plan.decision_receipt.authority_epoch, 7)
        self.assertEqual(plan.persona_version.decision_receipt_id, plan.decision_receipt.receipt_id)
        self.assertFalse(plan.records_written)
        self.assertFalse(plan.persona_created)
        self.assertFalse(plan.persona_version_written)
        self.assertFalse(plan.decision_receipt_written)

        summary = plan.value_free_summary()
        self.assertTrue(summary["shadowOnly"])
        self.assertTrue(summary["futurePersistenceRequired"])
        self.assertNotIn("不应出现在 Persona receipt 摘要中的称呼", repr(summary))
        self.assertNotIn("1950-01-01", repr(summary))
        self.assertNotIn("owner-persona-receipt-a", repr(summary))
        self.assertNotIn("vault-persona-receipt-a", repr(summary))
        self.assertNotIn(_PERSONA_ID, repr(summary))

    def test_same_command_context_is_deterministic_and_record_is_frozen(self) -> None:
        first = self._plan(_command())
        second = self._plan(_command())
        assert first.persona_version is not None
        assert first.decision_receipt is not None
        assert second.persona_version is not None
        assert second.decision_receipt is not None

        self.assertEqual(first.persona_version.version_id, second.persona_version.version_id)
        self.assertEqual(first.decision_receipt.receipt_id, second.decision_receipt.receipt_id)
        with self.assertRaises(FrozenInstanceError):
            first.persona_version.version_number = 999  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            first.decision_receipt.after_version = 999  # type: ignore[misc]

    def test_stale_or_invalid_command_cannot_plan_a_receipt(self) -> None:
        stale = self._plan(_command(expected_version=1))
        invalid_payload = _command()
        invalid_profile = dict(invalid_payload["profile"])
        invalid_profile["voiceProfileId"] = "runtime-profile"
        invalid_payload["profile"] = invalid_profile
        invalid = self._plan(invalid_payload)

        self.assertEqual(
            stale.disposition,
            OwnerTruthPersonaAuthorityReceiptDisposition.NOT_ADMITTED,
        )
        self.assertEqual(
            invalid.disposition,
            OwnerTruthPersonaAuthorityReceiptDisposition.NOT_ADMITTED,
        )
        self.assertIsNone(stale.persona_version)
        self.assertIsNone(stale.decision_receipt)
        self.assertIsNone(invalid.persona_version)
        self.assertIsNone(invalid.decision_receipt)
        self.assertFalse(stale.records_written)
        self.assertFalse(invalid.records_written)

    def test_non_owner_origin_cannot_plan_a_receipt(self) -> None:
        plan = self._plan(
            _command(),
            context=OwnerTruthPersonaAuthorityCommandContext(
                vault_id="vault-persona-receipt-a",
                owner_subject_id="owner-persona-receipt-a",
                actor_subject_id="owner-persona-receipt-a",
                current_persona_version=2,
                command_origin=OwnerTruthPersonaAuthorityCommandOrigin.PROVIDER,
            ),
        )

        self.assertEqual(
            plan.disposition,
            OwnerTruthPersonaAuthorityReceiptDisposition.NOT_ADMITTED,
        )
        self.assertIsNone(plan.persona_version)
        self.assertIsNone(plan.decision_receipt)
        self.assertFalse(plan.records_written)


if __name__ == "__main__":
    unittest.main()
