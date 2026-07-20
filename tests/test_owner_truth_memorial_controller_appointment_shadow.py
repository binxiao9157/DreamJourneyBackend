"""G0 contracts for future Memorial primary-controller appointment commands."""

from __future__ import annotations

from unittest.mock import patch
import unittest

from app.services.owner_truth_memorial_authority_admission_shadow import (
    MemorialPersonaAuthorityCommandOrigin,
)
from app.services.owner_truth_memorial_controller_appointment_shadow import (
    MemorialControllerAppointmentClaims,
    MemorialControllerAppointmentCommandContext,
    MemorialControllerAppointmentCommandDisposition,
    plan_memorial_controller_appointment,
)


_PERSONA_ID = "39cb8c7d-31c0-4314-b813-2c01b5e2a7fb"


def _context(
    *,
    actor_subject_id: str = "controller-current-a",
    current_primary_controller_subject_id: str | None = None,
    resolved_next_controller_subject_id: str = "controller-next-a",
    current_appointment_version: int = 0,
    represented_login_subject_id: str | None = None,
    command_origin: MemorialPersonaAuthorityCommandOrigin = (
        MemorialPersonaAuthorityCommandOrigin.MEMORIAL_CONTROLLER_INTERACTIVE
    ),
) -> MemorialControllerAppointmentCommandContext:
    return MemorialControllerAppointmentCommandContext(
        vault_id="vault-memorial-controller-a",
        represented_persona_id=_PERSONA_ID,
        actor_subject_id=actor_subject_id,
        current_primary_controller_subject_id=current_primary_controller_subject_id,
        resolved_next_controller_subject_id=resolved_next_controller_subject_id,
        current_appointment_version=current_appointment_version,
        authority_epoch=7,
        represented_login_subject_id=represented_login_subject_id,
        command_origin=command_origin,
    )


def _claims() -> MemorialControllerAppointmentClaims:
    return MemorialControllerAppointmentClaims(
        actor_identity_verified=True,
        next_controller_identity_verified=True,
        death_and_kinship_verified=True,
        legal_policy_ready=True,
    )


def _command(*, operation: str, expected_version: int) -> dict[str, object]:
    return {
        "commandId": f"memorial-controller-{operation}-a",
        "expectedVersion": expected_version,
        "operation": operation,
    }


class MemorialControllerAppointmentShadowTests(unittest.TestCase):
    def _plan(self, payload: object, *, context: object | None = None, claims: object | None = None):
        return plan_memorial_controller_appointment(
            payload,
            context=_context() if context is None else context,
            claims=_claims() if claims is None else claims,
            enabled=True,
        )

    def test_disabled_path_does_not_inspect_payload_context_or_claims(self) -> None:
        with patch(
            "app.services.owner_truth_memorial_controller_appointment_shadow."
            "MemorialControllerAppointmentCommand"
        ) as command_type:
            result = plan_memorial_controller_appointment(
                object(),
                context=object(),
                claims=object(),
            )

        command_type.from_payload.assert_not_called()
        self.assertEqual(
            result.disposition,
            MemorialControllerAppointmentCommandDisposition.SHADOW_DISABLED,
        )
        self.assertFalse(result.records_written)

    def test_bootstrap_plans_one_future_primary_controller_without_a_write(self) -> None:
        result = self._plan(
            _command(operation="bootstrap", expected_version=0),
            context=_context(
                actor_subject_id="controller-next-a",
                current_primary_controller_subject_id=None,
                resolved_next_controller_subject_id="controller-next-a",
                current_appointment_version=0,
            ),
        )

        self.assertEqual(
            result.disposition,
            MemorialControllerAppointmentCommandDisposition.PLANNED_FOR_FUTURE_PERSISTENCE,
        )
        self.assertIsNotNone(result.appointment_plan)
        assert result.appointment_plan is not None
        self.assertEqual(result.appointment_plan.operation, "bootstrap")
        self.assertEqual(result.appointment_plan.after_version, 1)
        self.assertEqual(result.appointment_plan.captured_authority_epoch, 7)
        self.assertFalse(result.appointment_plan.authority_epoch_changed)
        self.assertFalse(result.appointment_plan.requires_atomic_prior_revoke)
        self.assertFalse(result.records_written)
        self.assertFalse(result.controller_appointment_written)
        self.assertFalse(result.persona_version_written)

    def test_transfer_requires_current_controller_and_atomic_revoke_plan(self) -> None:
        result = self._plan(
            _command(operation="transfer", expected_version=3),
            context=_context(
                actor_subject_id="controller-current-a",
                current_primary_controller_subject_id="controller-current-a",
                resolved_next_controller_subject_id="controller-next-a",
                current_appointment_version=3,
            ),
        )

        self.assertEqual(
            result.disposition,
            MemorialControllerAppointmentCommandDisposition.PLANNED_FOR_FUTURE_PERSISTENCE,
        )
        assert result.appointment_plan is not None
        self.assertEqual(result.appointment_plan.operation, "transfer")
        self.assertTrue(result.appointment_plan.requires_atomic_prior_revoke)
        self.assertEqual(result.appointment_plan.expected_prior_version, 3)
        self.assertEqual(result.appointment_plan.after_version, 4)
        self.assertEqual(result.appointment_plan.captured_authority_epoch, 7)
        self.assertFalse(result.appointment_plan.authority_epoch_changed)
        self.assertTrue(result.appointment_plan.requires_session_and_grant_revoke)
        self.assertTrue(result.appointment_plan.requires_high_risk_capability_reevaluation)
        summary = result.value_free_summary()
        self.assertTrue(summary["shadowOnly"])
        self.assertNotIn("vault-memorial-controller-a", repr(summary))
        self.assertNotIn("controller-current-a", repr(summary))
        self.assertNotIn("controller-next-a", repr(summary))
        self.assertNotIn(_PERSONA_ID, repr(summary))

    def test_server_resolved_successor_binds_future_plan_and_receipt_ids(self) -> None:
        command = _command(operation="bootstrap", expected_version=0)
        first = self._plan(
            command,
            context=_context(
                actor_subject_id="controller-next-a",
                resolved_next_controller_subject_id="controller-next-a",
            ),
        )
        second = self._plan(
            command,
            context=_context(
                actor_subject_id="controller-next-b",
                resolved_next_controller_subject_id="controller-next-b",
            ),
        )

        assert first.appointment_plan is not None
        assert second.appointment_plan is not None
        self.assertNotEqual(first.appointment_plan.appointment_id, second.appointment_plan.appointment_id)
        self.assertNotEqual(
            first.appointment_plan.decision_receipt_id,
            second.appointment_plan.decision_receipt_id,
        )
        self.assertNotIn("controller-next-b", repr(second.value_free_summary()))

    def test_client_cannot_supply_controller_or_persona_scope_and_stale_or_wrong_actor_fails_closed(self) -> None:
        injected = _command(operation="bootstrap", expected_version=0)
        injected["nextControllerSubjectId"] = "client-selected-controller"
        injected["representedPersonaId"] = _PERSONA_ID
        invalid = self._plan(
            injected,
            context=_context(
                actor_subject_id="controller-next-a",
                resolved_next_controller_subject_id="controller-next-a",
            ),
        )
        stale = self._plan(
            _command(operation="transfer", expected_version=2),
            context=_context(
                current_primary_controller_subject_id="controller-current-a",
                current_appointment_version=3,
            ),
        )
        wrong_actor = self._plan(
            _command(operation="transfer", expected_version=3),
            context=_context(
                actor_subject_id="controller-other-a",
                current_primary_controller_subject_id="controller-current-a",
                current_appointment_version=3,
            ),
        )

        self.assertEqual(
            invalid.disposition,
            MemorialControllerAppointmentCommandDisposition.INVALID_COMMAND,
        )
        self.assertEqual(
            stale.disposition,
            MemorialControllerAppointmentCommandDisposition.EXPECTED_VERSION_CONFLICT,
        )
        self.assertEqual(
            wrong_actor.disposition,
            MemorialControllerAppointmentCommandDisposition.ACTOR_NOT_CURRENT_CONTROLLER,
        )
        self.assertFalse(invalid.records_written)
        self.assertFalse(stale.records_written)
        self.assertFalse(wrong_actor.records_written)

    def test_represented_login_family_missing_verification_claim_and_hold_are_denied(self) -> None:
        represented_login = self._plan(
            _command(operation="bootstrap", expected_version=0),
            context=_context(
                actor_subject_id="controller-next-a",
                resolved_next_controller_subject_id="controller-next-a",
                represented_login_subject_id="invalid-deceased-principal",
            ),
        )
        family = self._plan(
            _command(operation="bootstrap", expected_version=0),
            context=_context(
                actor_subject_id="controller-next-a",
                resolved_next_controller_subject_id="controller-next-a",
                command_origin=MemorialPersonaAuthorityCommandOrigin.FAMILY_CONTRIBUTOR,
            ),
        )
        missing_verification = self._plan(
            _command(operation="bootstrap", expected_version=0),
            context=_context(
                actor_subject_id="controller-next-a",
                resolved_next_controller_subject_id="controller-next-a",
            ),
            claims=MemorialControllerAppointmentClaims(),
        )
        rights_claim = self._plan(
            _command(operation="bootstrap", expected_version=0),
            context=_context(
                actor_subject_id="controller-next-a",
                resolved_next_controller_subject_id="controller-next-a",
            ),
            claims=MemorialControllerAppointmentClaims(
                actor_identity_verified=True,
                next_controller_identity_verified=True,
                death_and_kinship_verified=True,
                legal_policy_ready=True,
                rights_claim_active=True,
            ),
        )

        self.assertEqual(
            represented_login.disposition,
            MemorialControllerAppointmentCommandDisposition.REPRESENTED_LOGIN_PRINCIPAL_FORBIDDEN,
        )
        self.assertEqual(
            family.disposition,
            MemorialControllerAppointmentCommandDisposition.ORIGIN_NOT_ALLOWED,
        )
        self.assertEqual(
            missing_verification.disposition,
            MemorialControllerAppointmentCommandDisposition.VERIFICATION_REQUIRED,
        )
        self.assertEqual(
            rights_claim.disposition,
            MemorialControllerAppointmentCommandDisposition.RIGHTS_OR_CONFLICT_HOLD,
        )


if __name__ == "__main__":
    unittest.main()
