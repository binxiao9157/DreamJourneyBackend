from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "backend-runtime-capability-deployed-smoke.py"
SPEC = spec_from_file_location("backend_runtime_capability_deployed_smoke", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def provider_descriptor(**overrides):
    value = {
        "capability": "ownerTruthMediaStorage",
        "enabled": True,
        "providerReady": True,
        "provider": "filesystem",
        "providerKind": "privateObjectStorage",
        "operation": "writeReadDeleteWithSafetyScan",
        "dataClass": "ownerPrivateMedia",
        "region": "serverLocal",
        "retentionPolicyVersion": "ownerTruthMediaRetention-v1",
        "fallbackMode": "captureDisabled",
        "reason": "externalEvidenceMissing",
        "configurationStatus": "valid",
        "evidenceStatus": "notVerified",
    }
    value.update(overrides)
    return value


def capability_snapshot(**overrides):
    value = {
        **provider_descriptor(),
        "implemented": True,
        "releaseVisible": False,
        "externalVerified": False,
        "reason": "internalProviderOnly",
        "evidenceStatus": "internalOnly",
    }
    value.update(overrides)
    return value


class BackendRuntimeCapabilityDeployedSmokeContractTests(unittest.TestCase):
    def test_filesystem_inventory_and_public_snapshot_may_have_different_evidence(self):
        MODULE.require_inventory_snapshot_alignment(
            "ownerTruthMediaStorage",
            provider_descriptor(),
            capability_snapshot(),
        )

    def test_filesystem_snapshot_cannot_claim_external_verification(self):
        with self.assertRaisesRegex(AssertionError, "external evidence state"):
            MODULE.require_inventory_snapshot_alignment(
                "ownerTruthMediaStorage",
                provider_descriptor(),
                capability_snapshot(externalVerified=True),
            )

    def test_provider_identity_mismatch_still_fails_closed(self):
        with self.assertRaisesRegex(AssertionError, "provider inventory/snapshot mismatch"):
            MODULE.require_inventory_snapshot_alignment(
                "ownerTruthMediaStorage",
                provider_descriptor(),
                capability_snapshot(provider="cos"),
            )

    def test_non_media_evidence_mismatch_still_fails_closed(self):
        descriptor = provider_descriptor(
            capability="identityChallenge",
            provider="testOtp",
            providerKind="otp",
            operation="verifyOtp",
            dataClass="identityChallenge",
            region="serviceManaged",
            retentionPolicyVersion="identityChallenge-v1",
            fallbackMode="disabled",
            reason="ready",
            evidenceStatus="internalServiceVerified",
        )
        snapshot = capability_snapshot(**descriptor)
        snapshot.update(
            releaseVisible=True,
            externalVerified=False,
            evidenceStatus="notVerified",
        )
        with self.assertRaisesRegex(AssertionError, "evidenceStatus inventory/snapshot mismatch"):
            MODULE.require_inventory_snapshot_alignment(
                "identityChallenge",
                descriptor,
                snapshot,
            )


if __name__ == "__main__":
    unittest.main()
