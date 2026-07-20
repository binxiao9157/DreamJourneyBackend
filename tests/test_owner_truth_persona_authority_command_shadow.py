"""G0 contracts for the future Self Persona Authority command boundary."""

from __future__ import annotations

from unittest.mock import patch
import unittest

from app.services.owner_truth_persona_authority_command_shadow import (
    OwnerTruthPersonaAuthorityCommandContext,
    OwnerTruthPersonaAuthorityCommandDisposition,
    OwnerTruthPersonaAuthorityCommandOrigin,
    OwnerTruthPersonaAuthoritySubjectKind,
    preflight_self_persona_authority_command,
)


_PERSONA_ID = "6d582ac7-f5c4-4e6f-9c8a-4890799f1c65"


def _context(
    *,
    actor_subject_id: str = "owner-persona-command-a",
    current_persona_version: int = 2,
    subject_kind: OwnerTruthPersonaAuthoritySubjectKind = (
        OwnerTruthPersonaAuthoritySubjectKind.SELF_OWNER
    ),
) -> OwnerTruthPersonaAuthorityCommandContext:
    return OwnerTruthPersonaAuthorityCommandContext(
        vault_id="vault-persona-command-a",
        owner_subject_id="owner-persona-command-a",
        actor_subject_id=actor_subject_id,
        resolved_persona_id=_PERSONA_ID,
        current_persona_version=current_persona_version,
        subject_kind=subject_kind,
    )


def _command(*, expected_version: int = 2) -> dict[str, object]:
    return {
        "commandId": "persona-command-a",
        "expectedVersion": expected_version,
        "profile": {
            "birthDate": "1950-01-01",
            "displayName": "不应出现在公开摘要中的本人称呼",
            "gender": "女",
        },
    }


class OwnerTruthPersonaAuthorityCommandShadowTests(unittest.TestCase):
    def _preflight(
        self,
        payload: object,
        *,
        context: object | None = None,
    ):
        return preflight_self_persona_authority_command(
            payload,
            context=_context() if context is None else context,
            enabled=True,
        )

    def test_disabled_path_does_not_inspect_payload_or_context(self) -> None:
        with patch(
            "app.services.owner_truth_persona_authority_command_shadow."
            "OwnerTruthSelfPersonaAuthorityCommand.from_payload"
        ) as from_payload:
            result = preflight_self_persona_authority_command(
                {"unexpected": object()},
                context=object(),
            )

        from_payload.assert_not_called()
        self.assertEqual(
            result.disposition,
            OwnerTruthPersonaAuthorityCommandDisposition.SHADOW_DISABLED,
        )
        self.assertFalse(result.persona_created)
        self.assertFalse(result.persona_version_written)
        self.assertFalse(result.decision_receipt_written)

    def test_self_owner_allowlist_command_is_preflight_only(self) -> None:
        result = self._preflight(_command())

        self.assertEqual(
            result.disposition,
            OwnerTruthPersonaAuthorityCommandDisposition.ACCEPTED_FOR_FUTURE_PERSISTENCE,
        )
        self.assertEqual(result.candidate_fields, ("birthDate", "displayName", "gender"))
        self.assertTrue(result.command_accepted_for_future_persistence)
        self.assertFalse(result.persona_created)
        self.assertFalse(result.persona_version_written)
        self.assertFalse(result.decision_receipt_written)
        self.assertFalse(result.provider_or_runtime_mutated)

        summary = result.value_free_summary()
        self.assertEqual(summary["candidatePersonaFields"], ["birthDate", "displayName", "gender"])
        self.assertTrue(summary["shadowOnly"])
        self.assertNotIn("不应出现在公开摘要中的本人称呼", repr(summary))
        self.assertNotIn("1950-01-01", repr(summary))
        self.assertNotIn("owner-persona-command-a", repr(summary))
        self.assertNotIn("vault-persona-command-a", repr(summary))
        self.assertNotIn(_PERSONA_ID, repr(summary))

    def test_unknown_provider_runtime_and_family_fields_are_denied(self) -> None:
        for forbidden_field in (
            "voiceProfileId",
            "providerAssetId",
            "digitalHumanId",
            "personaScope",
            "relationshipStatus",
            "ownerSubjectId",
        ):
            with self.subTest(forbidden_field=forbidden_field):
                payload = _command()
                profile = dict(payload["profile"])
                profile[forbidden_field] = "private-untrusted-value"
                payload["profile"] = profile

                result = self._preflight(payload)

                self.assertEqual(
                    result.disposition,
                    OwnerTruthPersonaAuthorityCommandDisposition.INVALID_COMMAND,
                )
                self.assertFalse(result.command_accepted_for_future_persistence)
                self.assertFalse(result.persona_version_written)
                self.assertNotIn("private-untrusted-value", repr(result.value_free_summary()))

    def test_extra_top_level_runtime_or_client_persona_id_is_denied(self) -> None:
        for field, value in (
            ("digitalHumanId", "private-runtime-asset"),
            ("personaId", _PERSONA_ID),
        ):
            with self.subTest(field=field):
                payload = _command()
                payload[field] = value

                result = self._preflight(payload)

                self.assertEqual(
                    result.disposition,
                    OwnerTruthPersonaAuthorityCommandDisposition.INVALID_COMMAND,
                )
                self.assertFalse(result.command_accepted_for_future_persistence)
                self.assertNotIn(value, repr(result.value_free_summary()))

    def test_owner_actor_mismatch_fails_closed_before_payload_is_parsed(self) -> None:
        with patch(
            "app.services.owner_truth_persona_authority_command_shadow."
            "OwnerTruthSelfPersonaAuthorityCommand.from_payload"
        ) as from_payload:
            result = self._preflight(
                _command(),
                context=_context(actor_subject_id="family-member-a"),
            )

        from_payload.assert_not_called()
        self.assertEqual(
            result.disposition,
            OwnerTruthPersonaAuthorityCommandDisposition.ACTOR_NOT_OWNER,
        )
        self.assertIn("selfPersonaRequiresVaultOwner", result.reason_codes)

    def test_family_assistant_provider_runtime_and_unknown_origins_fail_before_payload_is_parsed(self) -> None:
        expected_reason = {
            OwnerTruthPersonaAuthorityCommandOrigin.FAMILY: "familyCannotWritePersonaAuthority",
            OwnerTruthPersonaAuthorityCommandOrigin.ASSISTANT: "assistantCannotWritePersonaAuthority",
            OwnerTruthPersonaAuthorityCommandOrigin.PROVIDER: "providerCannotWritePersonaAuthority",
            OwnerTruthPersonaAuthorityCommandOrigin.RUNTIME: "runtimeCannotWritePersonaAuthority",
            OwnerTruthPersonaAuthorityCommandOrigin.UNKNOWN: "unknownOriginCannotWritePersonaAuthority",
        }
        for origin, reason_code in expected_reason.items():
            with self.subTest(origin=origin.value):
                with patch(
                    "app.services.owner_truth_persona_authority_command_shadow."
                    "OwnerTruthSelfPersonaAuthorityCommand.from_payload"
                ) as from_payload:
                    result = self._preflight(
                        _command(),
                        context=OwnerTruthPersonaAuthorityCommandContext(
                            vault_id="vault-persona-command-a",
                            owner_subject_id="owner-persona-command-a",
                            actor_subject_id="owner-persona-command-a",
                            resolved_persona_id=_PERSONA_ID,
                            current_persona_version=2,
                            command_origin=origin,
                        ),
                    )

                from_payload.assert_not_called()
                self.assertEqual(
                    result.disposition,
                    OwnerTruthPersonaAuthorityCommandDisposition.ORIGIN_NOT_ALLOWED,
                )
                self.assertIn(reason_code, result.reason_codes)
                self.assertFalse(result.command_accepted_for_future_persistence)
                self.assertFalse(result.persona_version_written)

    def test_stale_expected_version_is_rejected_without_a_write(self) -> None:
        result = self._preflight(_command(expected_version=1))

        self.assertEqual(
            result.disposition,
            OwnerTruthPersonaAuthorityCommandDisposition.EXPECTED_VERSION_CONFLICT,
        )
        self.assertFalse(result.command_accepted_for_future_persistence)
        self.assertFalse(result.persona_version_written)
        self.assertIn("expectedPersonaVersionMismatch", result.reason_codes)

    def test_deceased_representation_cannot_be_treated_as_a_login_principal(self) -> None:
        with patch(
            "app.services.owner_truth_persona_authority_command_shadow."
            "OwnerTruthSelfPersonaAuthorityCommand.from_payload"
        ) as from_payload:
            result = self._preflight(
                _command(),
                context=_context(
                    subject_kind=OwnerTruthPersonaAuthoritySubjectKind.MEMORIAL_REPRESENTED
                ),
            )

        from_payload.assert_not_called()
        self.assertEqual(
            result.disposition,
            OwnerTruthPersonaAuthorityCommandDisposition.MEMORIAL_CONTROLLER_REQUIRED,
        )
        self.assertIn("deceasedPersonaRequiresControllerNotLoginPrincipal", result.reason_codes)
        self.assertFalse(result.persona_version_written)

    def test_invalid_envelope_is_quarantined(self) -> None:
        result = self._preflight(object())

        self.assertEqual(
            result.disposition,
            OwnerTruthPersonaAuthorityCommandDisposition.INVALID_COMMAND,
        )
        self.assertFalse(result.command_accepted_for_future_persistence)
        self.assertFalse(result.persona_version_written)


if __name__ == "__main__":
    unittest.main()
