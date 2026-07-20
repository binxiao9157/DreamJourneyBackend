"""G0-only tests for the disabled TimeLetter typed-effect admission shadow."""

from __future__ import annotations

from hashlib import sha256
import json
import unittest
from unittest.mock import patch

from app.services.time_letter_delivery_effects import (
    TIME_LETTER_DELIVERY_SCHEMA_VERSION,
    TimeLetterDeliveryAdmissionShadowDisposition,
    build_time_letter_delivery_admission_shadow,
)


def _v4_time_letter(**overrides: object) -> dict[str, object]:
    item: dict[str, object] = {
        "id": "letter-shadow-001",
        "kind": "timeLetter",
        "ownerSubjectId": "owner-shadow-001",
        "vaultId": "vault-shadow-001",
        "authorityEpoch": 3,
        "sealedVersion": 2,
        "sealedPayloadHash": sha256(b"sealed-time-letter-shadow-v2").hexdigest(),
        "deliveryProtocolVersion": TIME_LETTER_DELIVERY_SCHEMA_VERSION,
        "deliveryState": "sealed",
        "deliveryStatus": "scheduled",
        "openAt": "2026-07-20T09:00:00Z",
        "recipients": [
            {
                "id": "family-shadow-001",
                "name": "SENSITIVE_RECIPIENT_NAME",
                "subjectId": "member-shadow-001",
                "type": "family",
            },
            {
                "id": "family-shadow-002",
                "name": "SENSITIVE_SECOND_RECIPIENT_NAME",
                "subjectId": "member-shadow-002",
                "type": "family",
            },
        ],
        "title": "SENSITIVE_TIME_LETTER_TITLE",
        "note": "SENSITIVE_TIME_LETTER_BODY",
    }
    item.update(overrides)
    return item


class TimeLetterTypedEffectAdmissionShadowTests(unittest.TestCase):
    def test_default_disabled_returns_before_malformed_payload_is_parsed(self) -> None:
        with patch(
            "app.services.time_letter_delivery_effects.build_time_letter_delivery_plan"
        ) as build_plan:
            result = build_time_letter_delivery_admission_shadow(
                {"title": "SENSITIVE_MALFORMED_LEGACY_PAYLOAD"},
                now_iso="not-an-iso-timestamp",
            )

        build_plan.assert_not_called()
        self.assertFalse(result.enabled)
        self.assertEqual(
            result.disposition,
            TimeLetterDeliveryAdmissionShadowDisposition.SHADOW_DISABLED,
        )
        self.assertFalse(result.would_admit)
        self.assertEqual(result.intent_stable_key_hashes, ())

    def test_enabled_shadow_ignores_legacy_envelope_without_parsing_it(self) -> None:
        legacy = _v4_time_letter(deliveryProtocolVersion=None, sealedVersion=None)
        with patch(
            "app.services.time_letter_delivery_effects.build_time_letter_delivery_plan"
        ) as build_plan:
            result = build_time_letter_delivery_admission_shadow(
                legacy,
                now_iso="not-an-iso-timestamp",
                enabled=True,
            )

        build_plan.assert_not_called()
        self.assertEqual(
            result.disposition,
            TimeLetterDeliveryAdmissionShadowDisposition.LEGACY_ENVELOPE_IGNORED,
        )
        self.assertFalse(result.would_admit)
        self.assertIsNone(result.plan_summary)

    def test_due_v4_envelope_only_reports_deterministic_value_free_would_admit_data(self) -> None:
        result = build_time_letter_delivery_admission_shadow(
            _v4_time_letter(),
            now_iso="2026-07-20T09:00:01Z",
            enabled=True,
        )
        renamed = build_time_letter_delivery_admission_shadow(
            _v4_time_letter(
                title="SENSITIVE_RENAMED_TITLE",
                note="SENSITIVE_RENAMED_BODY",
                recipients=[
                    {
                        "id": "family-shadow-001",
                        "name": "SENSITIVE_RENAMED_RECIPIENT",
                        "subjectId": "member-shadow-001",
                        "type": "family",
                    },
                    {
                        "id": "family-shadow-002",
                        "name": "SENSITIVE_RENAMED_SECOND_RECIPIENT",
                        "subjectId": "member-shadow-002",
                        "type": "family",
                    },
                ],
            ),
            now_iso="2026-07-20T09:00:01Z",
            enabled=True,
        )

        self.assertEqual(
            result.disposition,
            TimeLetterDeliveryAdmissionShadowDisposition.WOULD_ADMIT,
        )
        self.assertTrue(result.would_admit)
        self.assertEqual(len(result.intent_stable_key_hashes), 3)
        self.assertEqual(result.intent_stable_key_hashes, renamed.intent_stable_key_hashes)
        self.assertTrue(all(len(value) == 64 for value in result.intent_stable_key_hashes))

        summary = result.value_free_summary()
        serialized = json.dumps(summary, ensure_ascii=True, sort_keys=True)
        self.assertTrue(summary["shadowOnly"])
        self.assertTrue(summary["legacyDirectDispatchUnchanged"])
        self.assertFalse(summary["effectAdmissionPerformed"])
        self.assertEqual(summary["intentCount"], 3)
        self.assertNotIn("SENSITIVE_TIME_LETTER_TITLE", serialized)
        self.assertNotIn("SENSITIVE_TIME_LETTER_BODY", serialized)
        self.assertNotIn("SENSITIVE_RECIPIENT_NAME", serialized)
        self.assertNotIn("owner-shadow-001", serialized)
        self.assertNotIn("vault-shadow-001", serialized)

    def test_future_v4_envelope_is_not_due_and_emits_no_intent_hash(self) -> None:
        result = build_time_letter_delivery_admission_shadow(
            _v4_time_letter(openAt="2026-07-20T09:00:02Z"),
            now_iso="2026-07-20T09:00:01Z",
            enabled=True,
        )

        self.assertEqual(
            result.disposition,
            TimeLetterDeliveryAdmissionShadowDisposition.NOT_DUE,
        )
        self.assertFalse(result.would_admit)
        self.assertEqual(result.intent_stable_key_hashes, ())
        self.assertEqual(result.value_free_summary()["intentCount"], 0)

    def test_malformed_v4_envelope_fails_closed_without_leaking_contract_error(self) -> None:
        result = build_time_letter_delivery_admission_shadow(
            _v4_time_letter(sealedVersion=None),
            now_iso="2026-07-20T09:00:01Z",
            enabled=True,
        )

        self.assertEqual(
            result.disposition,
            TimeLetterDeliveryAdmissionShadowDisposition.INVALID_V4_ENVELOPE,
        )
        self.assertFalse(result.would_admit)
        self.assertEqual(result.reason_code, "invalidV4Envelope")
        self.assertIsNone(result.plan_summary)


if __name__ == "__main__":
    unittest.main()
