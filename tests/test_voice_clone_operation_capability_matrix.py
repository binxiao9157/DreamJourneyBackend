import unittest

from app.services.voice_clone_operation_capabilities import (
    VOICE_CLONE_OPERATION_CAPABILITY_CONTRACT_VERSION,
    build_voice_clone_operation_capability_matrix,
)


class _Provider:
    def __init__(
        self,
        *,
        provider_mode: str,
        configured: bool,
        deletion_capability: str = "unsupported",
    ) -> None:
        self.provider_mode = provider_mode
        self.is_configured = configured
        self.profile_deletion_capability = deletion_capability


class VoiceCloneOperationCapabilityMatrixTests(unittest.TestCase):
    def _matrix(
        self,
        *,
        training_ready: bool = True,
        synthesis_ready: bool = True,
        admission_enabled: bool = True,
        admission_reason: str = "ready",
        deletion_capability: str = "unsupported",
        deletion_worker_enabled: bool = False,
    ):
        return build_voice_clone_operation_capability_matrix(
            training_provider=_Provider(
                provider_mode="fakeVoiceTraining",
                configured=training_ready,
                deletion_capability=deletion_capability,
            ),
            synthesis_provider=_Provider(
                provider_mode="fakeVoiceSynthesis",
                configured=synthesis_ready,
            ),
            training_admission_enabled=admission_enabled,
            training_admission_reason=admission_reason,
            deletion_worker_enabled=deletion_worker_enabled,
        )

    def test_matrix_covers_every_profile_operation(self) -> None:
        matrix = self._matrix()

        self.assertEqual(
            matrix["schemaVersion"],
            VOICE_CLONE_OPERATION_CAPABILITY_CONTRACT_VERSION,
        )
        self.assertEqual(
            set(matrix["operations"]),
            {"train", "query", "preview", "accept", "synthesize", "pause", "delete"},
        )
        self.assertEqual(matrix["operations"]["train"]["executionOwner"], "provider")
        self.assertEqual(matrix["operations"]["accept"]["executionOwner"], "serverAuthority")
        self.assertEqual(matrix["operations"]["delete"]["executionOwner"], "serverThenProvider")

    def test_training_and_synthesis_fail_closed_independently(self) -> None:
        matrix = self._matrix(
            training_ready=True,
            synthesis_ready=False,
            admission_enabled=False,
            admission_reason="identityLivenessProviderUnavailable",
        )
        operations = matrix["operations"]

        self.assertFalse(operations["train"]["available"])
        self.assertEqual(
            operations["train"]["reasonCode"],
            "identityLivenessProviderUnavailable",
        )
        self.assertTrue(operations["query"]["available"])
        self.assertFalse(operations["preview"]["available"])
        self.assertFalse(operations["synthesize"]["available"])
        self.assertEqual(operations["synthesize"]["providerCapability"], "unavailable")

    def test_local_authority_operations_remain_available_during_provider_outage(self) -> None:
        operations = self._matrix(
            training_ready=False,
            synthesis_ready=False,
            admission_enabled=False,
            admission_reason="voiceCloneProviderUnavailable",
        )["operations"]

        self.assertTrue(operations["accept"]["available"])
        self.assertEqual(operations["accept"]["providerCapability"], "notRequired")
        self.assertTrue(operations["pause"]["available"])
        self.assertEqual(operations["pause"]["providerCapability"], "notRequired")
        self.assertTrue(operations["delete"]["available"])

    def test_unsupported_provider_delete_is_partial_not_completed(self) -> None:
        delete = self._matrix(
            deletion_capability="unsupported",
            deletion_worker_enabled=True,
        )["operations"]["delete"]

        self.assertTrue(delete["available"])
        self.assertEqual(delete["providerCapability"], "unsupported")
        self.assertFalse(delete["providerCompletionAvailable"])
        self.assertEqual(delete["completionMode"], "revocationFirstAsyncReceipt")
        self.assertEqual(delete["reasonCode"], "providerVoiceDeletionUnsupported")

    def test_supported_delete_requires_worker_before_provider_completion_is_available(self) -> None:
        worker_off = self._matrix(
            deletion_capability="ready",
            deletion_worker_enabled=False,
        )["operations"]["delete"]
        worker_on = self._matrix(
            deletion_capability="ready",
            deletion_worker_enabled=True,
        )["operations"]["delete"]

        self.assertFalse(worker_off["providerCompletionAvailable"])
        self.assertEqual(worker_off["reasonCode"], "providerDeletionWorkerDisabled")
        self.assertTrue(worker_on["providerCompletionAvailable"])
        self.assertEqual(worker_on["reasonCode"], "ready")

    def test_matrix_contains_only_public_app_routes(self) -> None:
        matrix = self._matrix()
        rendered = repr(matrix).lower()

        self.assertNotIn("api_key", rendered)
        self.assertNotIn("authorization", rendered)
        self.assertNotIn("speaker_id", rendered)
        for descriptor in matrix["operations"].values():
            endpoint = descriptor["endpointTemplate"]
            self.assertTrue(endpoint.startswith("/voice/"), endpoint)


if __name__ == "__main__":
    unittest.main()
