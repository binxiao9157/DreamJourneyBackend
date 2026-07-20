"""G0 contracts for the future Self Persona persistence admission boundary."""

from __future__ import annotations

from unittest.mock import patch
import unittest

from app.services.owner_truth_persona_authority_command_shadow import (
    OwnerTruthPersonaAuthorityCommandContext,
)
from app.services.owner_truth_persona_authority_receipt_shadow import (
    plan_self_persona_authority_receipt,
)
from app.services.owner_truth_persona_persistence_admission_shadow import (
    OwnerTruthPersonaPersistenceAdmissionClaims,
    OwnerTruthPersonaPersistenceAdmissionDisposition,
    observe_self_persona_persistence_admission,
)


_PERSONA_ID = "6d582ac7-f5c4-4e6f-9c8a-4890799f1c65"


def _context() -> OwnerTruthPersonaAuthorityCommandContext:
    return OwnerTruthPersonaAuthorityCommandContext(
        vault_id="vault-persona-persistence-a",
        owner_subject_id="owner-persona-persistence-a",
        actor_subject_id="owner-persona-persistence-a",
        resolved_persona_id=_PERSONA_ID,
        current_persona_version=2,
        authority_epoch=7,
    )


def _command() -> dict[str, object]:
    return {
        "commandId": "persona-persistence-command-a",
        "expectedVersion": 2,
        "profile": {
            "birthDate": "1950-01-01",
            "displayName": "不应出现在 Persona persistence 摘要中的称呼",
            "gender": "女",
        },
    }


def _receipt_plan():
    return plan_self_persona_authority_receipt(
        _command(),
        context=_context(),
        enabled=True,
    )


class OwnerTruthPersonaPersistenceAdmissionShadowTests(unittest.TestCase):
    def test_disabled_path_does_not_inspect_plan_or_claims(self) -> None:
        with patch(
            "app.services.owner_truth_persona_persistence_admission_shadow."
            "OwnerTruthPersonaPersistenceAdmissionClaims"
        ) as claims_type:
            result = observe_self_persona_persistence_admission(
                object(),
                claims=object(),
            )

        claims_type.assert_not_called()
        self.assertEqual(
            result.disposition,
            OwnerTruthPersonaPersistenceAdmissionDisposition.SHADOW_DISABLED,
        )
        self.assertFalse(result.persistence_admitted)
        self.assertFalse(result.repository_written)

    def test_future_receipt_plan_requires_separate_g2_and_g4_evidence(self) -> None:
        result = observe_self_persona_persistence_admission(
            _receipt_plan(),
            enabled=True,
        )

        self.assertEqual(
            result.disposition,
            OwnerTruthPersonaPersistenceAdmissionDisposition.EXTERNAL_G2_G4_REQUIRED,
        )
        self.assertFalse(result.persistence_admitted)
        self.assertFalse(result.schema_changed)
        self.assertFalse(result.repository_written)
        self.assertFalse(result.persona_version_written)
        self.assertFalse(result.decision_receipt_written)
        self.assertIn(
            "shadowPlanCannotSelfAuthorizePersonaPersistence",
            result.reason_codes,
        )
        self.assertIn(
            "additiveSchemaMigrationRequiresSeparateG2Proof",
            result.reason_codes,
        )
        self.assertIn(
            "versionCasRequiresTransactionalRepositoryProof",
            result.reason_codes,
        )
        self.assertIn(
            "decisionReceiptUniquenessRequiresTransactionalRepositoryProof",
            result.reason_codes,
        )

        summary = result.value_free_summary()
        self.assertTrue(summary["shadowOnly"])
        self.assertEqual(summary["requiredExternalGates"], ["G2", "G4"])
        self.assertNotIn("不应出现在 Persona persistence 摘要中的称呼", repr(summary))
        self.assertNotIn("1950-01-01", repr(summary))
        self.assertNotIn("owner-persona-persistence-a", repr(summary))
        self.assertNotIn("vault-persona-persistence-a", repr(summary))
        self.assertNotIn(_PERSONA_ID, repr(summary))

    def test_untrusted_claims_cannot_self_authorize_persistence(self) -> None:
        result = observe_self_persona_persistence_admission(
            _receipt_plan(),
            claims=OwnerTruthPersonaPersistenceAdmissionClaims(
                additive_schema_ready=True,
                decision_receipt_uniqueness_ready=True,
                rights_evidence_ready=True,
                version_cas_ready=True,
            ),
            enabled=True,
        )

        self.assertEqual(
            result.disposition,
            OwnerTruthPersonaPersistenceAdmissionDisposition.EXTERNAL_G2_G4_REQUIRED,
        )
        self.assertFalse(result.persistence_admitted)
        self.assertFalse(result.repository_written)
        self.assertIn("shadowClaimsCannotAuthorizePersonaPersistence", result.reason_codes)

    def test_non_admitted_or_invalid_envelopes_cannot_reach_g2_review(self) -> None:
        not_admitted = plan_self_persona_authority_receipt(
            _command(),
            context=_context(),
        )
        receipt_result = observe_self_persona_persistence_admission(
            not_admitted,
            enabled=True,
        )
        invalid_result = observe_self_persona_persistence_admission(
            object(),
            enabled=True,
        )

        self.assertEqual(
            receipt_result.disposition,
            OwnerTruthPersonaPersistenceAdmissionDisposition.RECEIPT_NOT_ADMITTED,
        )
        self.assertFalse(receipt_result.persistence_admitted)
        self.assertEqual(
            invalid_result.disposition,
            OwnerTruthPersonaPersistenceAdmissionDisposition.INVALID_ENVELOPE,
        )
        self.assertFalse(invalid_result.persistence_admitted)


if __name__ == "__main__":
    unittest.main()
