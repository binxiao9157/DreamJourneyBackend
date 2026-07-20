"""G0 tests for legacy profile to Persona boundary classification."""

from __future__ import annotations

from unittest.mock import patch
import unittest

from app.main import _sanitize_profile_payload
from app.services.owner_truth_persona_profile_legacy_boundary_shadow import (
    OwnerTruthPersonaProfileLegacyBoundaryContext,
    OwnerTruthPersonaProfileLegacyBoundaryDisposition,
    inventory_legacy_profile_persona_boundary,
)


def _context() -> OwnerTruthPersonaProfileLegacyBoundaryContext:
    return OwnerTruthPersonaProfileLegacyBoundaryContext(
        vault_id="vault-persona-a",
        owner_subject_id="owner-persona-a",
    )


def _profile() -> dict[str, object]:
    return {
        "userId": "owner-persona-a",
        "nickname": "不应出现在摘要中的显示名",
        "gender": "女",
        "birthday": "1950-01-01",
        "region": "不在 Persona V1 allowlist 的地区",
        "avatarName": "asset-private-portrait",
        "voiceProfileId": "voice-private-profile",
        "providerSpeakerId": "provider-private-speaker",
        "digitalHumanId": "digital-human-private-id",
        "personaScope": "family",
        "relationshipStatus": "accepted",
        "secretLegalName": "不应出现在字段摘要中的内容",
    }


class OwnerTruthPersonaProfileLegacyBoundaryShadowTests(unittest.TestCase):
    def _inventory(self, profile: object):
        return inventory_legacy_profile_persona_boundary(
            profile,
            context=_context(),
            enabled=True,
        )

    def test_disabled_path_returns_before_profile_is_inspected(self) -> None:
        with patch(
            "app.services.owner_truth_persona_profile_legacy_boundary_shadow._profile_fingerprint"
        ) as fingerprint:
            result = inventory_legacy_profile_persona_boundary(
                {"unexpected": object()},
                context=object(),
            )

        fingerprint.assert_not_called()
        self.assertEqual(
            result.disposition,
            OwnerTruthPersonaProfileLegacyBoundaryDisposition.SHADOW_DISABLED,
        )
        self.assertFalse(result.persona_created)
        self.assertFalse(result.legacy_profile_migrated)

    def test_profile_fields_are_allowlisted_or_quarantined_without_creating_persona_authority(self) -> None:
        result = self._inventory(_profile())

        self.assertEqual(
            result.disposition,
            OwnerTruthPersonaProfileLegacyBoundaryDisposition.CLASSIFIED,
        )
        self.assertEqual(
            result.canonical_candidate_fields,
            ("birthDate", "displayName", "gender"),
        )
        self.assertEqual(
            result.field_class_counts["allowlisted_persona_candidate"],
            3,
        )
        self.assertEqual(result.field_class_counts["identity_binding_only"], 1)
        self.assertEqual(result.field_class_counts["embodiment_or_provider_excluded"], 4)
        self.assertEqual(result.field_class_counts["family_or_relationship_excluded"], 2)
        self.assertEqual(result.field_class_counts["unknown_quarantine"], 2)
        self.assertFalse(result.persona_created)
        self.assertFalse(result.legacy_profile_migrated)

        summary = result.value_free_summary()
        self.assertEqual(summary["candidatePersonaFields"], ["birthDate", "displayName", "gender"])
        for private_value in (
            "不应出现在摘要中的显示名",
            "asset-private-portrait",
            "voice-private-profile",
            "provider-private-speaker",
            "digital-human-private-id",
            "不应出现在字段摘要中的内容",
        ):
            self.assertNotIn(private_value, repr(summary))
        self.assertNotIn("secretLegalName", repr(summary))
        self.assertNotIn("owner-persona-a", repr(summary))
        self.assertNotIn("vault-persona-a", repr(summary))

    def test_foreign_owner_is_rejected_before_field_classification(self) -> None:
        profile = _profile()
        profile["userId"] = "owner-persona-b"
        with patch(
            "app.services.owner_truth_persona_profile_legacy_boundary_shadow._classify_field"
        ) as classify_field:
            result = self._inventory(profile)

        classify_field.assert_not_called()
        self.assertEqual(
            result.disposition,
            OwnerTruthPersonaProfileLegacyBoundaryDisposition.OWNER_MISMATCH,
        )
        self.assertFalse(result.persona_created)
        self.assertFalse(result.legacy_profile_migrated)

    def test_current_profile_sanitizer_fields_do_not_silently_become_a_persona(self) -> None:
        current_profile = _sanitize_profile_payload(
            {
                "userId": "owner-persona-a",
                "nickname": "当前资料昵称",
                "gender": "女",
                "region": "当前资料地区",
                "avatarName": "当前头像资产名",
            }
        )

        result = self._inventory(current_profile)

        self.assertEqual(result.canonical_candidate_fields, ("displayName", "gender"))
        self.assertEqual(result.field_class_counts["allowlisted_persona_candidate"], 2)
        self.assertEqual(result.field_class_counts["identity_binding_only"], 1)
        self.assertEqual(result.field_class_counts["embodiment_or_provider_excluded"], 1)
        self.assertEqual(result.field_class_counts["unknown_quarantine"], 1)
        self.assertFalse(result.persona_created)
        self.assertFalse(result.legacy_profile_migrated)

    def test_invalid_envelope_stays_quarantined(self) -> None:
        result = self._inventory(object())

        self.assertEqual(
            result.disposition,
            OwnerTruthPersonaProfileLegacyBoundaryDisposition.INVALID_ENVELOPE,
        )
        self.assertFalse(result.persona_created)
        self.assertFalse(result.legacy_profile_migrated)


if __name__ == "__main__":
    unittest.main()
