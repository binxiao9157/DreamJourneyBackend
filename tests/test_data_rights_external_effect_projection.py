import json
import unittest

from app.services.data_rights_external_effect_projection import (
    build_data_rights_external_effect_projection,
)


class DataRightsExternalEffectProjectionTests(unittest.TestCase):
    @staticmethod
    def _summary() -> dict:
        return {
            "request": {
                "id": "rights-request-1",
                "subjectHash": "owner-hash-1",
            }
        }

    def test_projects_provider_states_without_promoting_missing_receipts(self) -> None:
        report = build_data_rights_external_effect_projection(
            self._summary(),
            resource_evidence=[
                {
                    "moduleId": "objectStorage",
                    "resourceType": "mediaObjectBytes",
                    "status": "completed",
                    "receiptPresent": True,
                    "ageSeconds": 8,
                    "reasonCodes": [],
                },
                {
                    "moduleId": "providerVoice",
                    "resourceType": "voiceCloneAsset",
                    "status": "pending",
                    "receiptPresent": False,
                    "ageSeconds": 15,
                    "reasonCodes": ["executionPending"],
                },
            ],
            access_revocation={"status": "revoked"},
            linked_effect_observations=[
                {
                    "requestId": "rights-request-1",
                    "ownerSubjectHash": "owner-hash-1",
                    "domain": "providerDigitalHuman",
                    "state": "completed",
                    "providerReceiptPresent": False,
                    "ageSeconds": 4,
                    "reasonCodes": ["providerReportedComplete"],
                },
            ],
        )

        domains = {item["domain"]: item for item in report["domains"]}
        self.assertEqual(report["accessState"], "revoked")
        self.assertEqual(domains["objectStorage"]["status"], "completed")
        self.assertEqual(domains["objectStorage"]["receiptState"], "recorded")
        self.assertEqual(domains["providerVoice"]["status"], "pending")
        self.assertEqual(domains["providerDigitalHuman"]["status"], "pending")
        self.assertIn(
            "providerCompletionMissingUpstreamReceipt",
            domains["providerDigitalHuman"]["reasonCodes"],
        )

    def test_absent_provider_and_cross_account_observation_are_honest(self) -> None:
        report = build_data_rights_external_effect_projection(
            self._summary(),
            resource_evidence=[],
            access_revocation={"status": "unknown"},
            linked_effect_observations=[
                {
                    "requestId": "rights-request-1",
                    "ownerSubjectHash": "other-owner-hash",
                    "domain": "providerVoice",
                    "state": "completed",
                    "providerReceiptPresent": True,
                    "reasonCodes": ["foreignEvidence"],
                }
            ],
        )

        domains = {item["domain"]: item for item in report["domains"]}
        self.assertEqual(report["rejectedEvidenceCount"], 1)
        self.assertEqual(domains["objectStorage"]["status"], "unsupported")
        self.assertEqual(domains["providerVoice"]["status"], "pending")
        self.assertEqual(domains["providerVoice"]["accessState"], "notConfirmed")
        self.assertIn(
            "accessRevocationNotConfirmed",
            domains["providerVoice"]["reasonCodes"],
        )

    def test_projection_is_value_minimized(self) -> None:
        report = build_data_rights_external_effect_projection(
            self._summary(),
            resource_evidence=[],
            access_revocation={"status": "revoked"},
            linked_effect_observations=[
                {
                    "requestId": "rights-request-1",
                    "ownerSubjectHash": "owner-hash-1",
                    "domain": "providerVoice",
                    "state": "completed",
                    "providerReceiptPresent": True,
                    "providerLogId": "provider-log-must-not-leak",
                    "objectKey": "private/object/key",
                    "reasonCodes": ["providerCompleted"],
                }
            ],
        )

        serialized = json.dumps(report, ensure_ascii=False, sort_keys=True)
        self.assertNotIn("provider-log-must-not-leak", serialized)
        self.assertNotIn("private/object/key", serialized)
        self.assertNotIn("owner-hash-1", serialized)
        self.assertEqual(
            next(item for item in report["domains"] if item["domain"] == "providerVoice")["status"],
            "completed",
        )

    def test_legacy_redacted_summary_rejects_linked_receipts_without_failing(self) -> None:
        report = build_data_rights_external_effect_projection(
            {"request": {"id": "legacy-rights-request"}},
            resource_evidence=[],
            access_revocation={"status": "revoked"},
            linked_effect_observations=[
                {
                    "requestId": "legacy-rights-request",
                    "ownerSubjectHash": "owner-hash-that-cannot-be-verified",
                    "domain": "providerVoice",
                    "state": "completed",
                    "providerReceiptPresent": True,
                    "reasonCodes": ["foreignLegacyEvidence"],
                }
            ],
        )

        voice = next(item for item in report["domains"] if item["domain"] == "providerVoice")
        self.assertEqual(report["rejectedEvidenceCount"], 1)
        self.assertEqual(voice["status"], "pending")


if __name__ == "__main__":
    unittest.main()
