import unittest

from app.core.config import Settings
from app.services.clamav_runtime_evidence import (
    collect_clamav_runtime_evidence,
    parse_clamav_version,
)


class ClamAVRuntimeEvidenceTests(unittest.TestCase):
    def test_version_parser_returns_only_engine_and_signature_versions(self):
        result = parse_clamav_version(
            "ClamAV 1.4.3/27654/Sat Aug  9 08:00:00 2026"
        )

        self.assertEqual(result, {
            "engineVersion": "1.4.3",
            "signatureVersion": "27654",
        })

    def test_configured_clamav_probe_builds_value_free_ready_evidence(self):
        settings = Settings(
            environment="production",
            owner_truth_media_content_safety_provider="clamav",
            owner_truth_media_clamav_host="private-clamav-host",
        )

        result = collect_clamav_runtime_evidence(
            settings,
            probe=lambda: "ClamAV 1.4.3/27654/Sat Aug  9 08:00:00 2026",
        )

        self.assertEqual(result["ready"], True)
        self.assertEqual(result["reason"], "clamavRuntimeReady")
        self.assertEqual(result["engineVersion"], "1.4.3")
        self.assertEqual(result["signatureVersion"], "27654")
        self.assertNotIn("private-clamav-host", str(result))

    def test_probe_failure_and_non_clamav_provider_fail_closed(self):
        configured = Settings(
            environment="production",
            owner_truth_media_content_safety_provider="clamav",
        )
        disabled = Settings(
            environment="production",
            owner_truth_media_content_safety_provider="disabled",
        )

        failed = collect_clamav_runtime_evidence(
            configured,
            probe=lambda: (_ for _ in ()).throw(TimeoutError()),
        )
        unavailable = collect_clamav_runtime_evidence(disabled)

        self.assertEqual(failed["reason"], "clamavRuntimeProbeFailed")
        self.assertFalse(failed["ready"])
        self.assertEqual(unavailable["reason"], "clamavRuntimeNotConfigured")
        self.assertFalse(unavailable["ready"])

    def test_malformed_provider_output_is_not_copied_to_evidence(self):
        settings = Settings(
            environment="production",
            owner_truth_media_content_safety_provider="clamav",
        )

        result = collect_clamav_runtime_evidence(
            settings,
            probe=lambda: "token=raw-provider-output",
        )

        self.assertEqual(result, {
            "ready": False,
            "reason": "clamavRuntimeVersionInvalid",
            "engineVersion": None,
            "signatureVersion": None,
        })


if __name__ == "__main__":
    unittest.main()
