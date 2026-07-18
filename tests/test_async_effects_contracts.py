import hashlib
import unittest

from app.async_effects.contracts import (
    AsyncEffectContractError,
    AsyncEffectIntent,
    AsyncEffectTarget,
    is_async_effect_store_ready,
    resolve_async_effect_runtime_status,
)


def payload_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class AsyncEffectContractsTests(unittest.TestCase):
    def target(self, *, resource_version: int = 1) -> AsyncEffectTarget:
        return AsyncEffectTarget(
            owner_subject_id="owner-effect-1",
            vault_id="vault-effect-1",
            resource_type="timeLetter",
            resource_id="letter-effect-1",
            resource_version=resource_version,
            purpose="delivery",
            authority_epoch=0,
        )

    def test_stable_identifiers_are_deterministic_for_the_same_effect_target(self):
        first = AsyncEffectIntent(
            operation_type="timeLetter.delivery",
            target=self.target(),
            payload_hash=payload_hash("metadata-v1"),
        )
        replay = AsyncEffectIntent(
            operation_type="timeLetter.delivery",
            target=self.target(),
            payload_hash=payload_hash("metadata-v1"),
        )

        self.assertEqual(first.stable_key, replay.stable_key)
        self.assertEqual(first.operation_id, replay.operation_id)
        self.assertEqual(first.outbox_event_id, replay.outbox_event_id)
        self.assertEqual(first.job_id, replay.job_id)
        self.assertEqual(first.business_receipt_id, replay.business_receipt_id)

    def test_resource_version_changes_the_stable_effect_target(self):
        v1 = AsyncEffectIntent(
            operation_type="timeLetter.delivery",
            target=self.target(resource_version=1),
            payload_hash=payload_hash("metadata-v1"),
        )
        v2 = AsyncEffectIntent(
            operation_type="timeLetter.delivery",
            target=self.target(resource_version=2),
            payload_hash=payload_hash("metadata-v2"),
        )

        self.assertNotEqual(v1.stable_key, v2.stable_key)
        self.assertNotEqual(v1.operation_id, v2.operation_id)

    def test_payload_hash_must_be_opaque_sha256_evidence(self):
        with self.assertRaises(AsyncEffectContractError):
            AsyncEffectIntent(
                operation_type="timeLetter.delivery",
                target=self.target(),
                payload_hash="the private letter body must not be stored here",
            )

    def test_public_receipt_summary_does_not_expose_payload_hash_or_body(self):
        intent = AsyncEffectIntent(
            operation_type="timeLetter.delivery",
            target=self.target(),
            payload_hash=payload_hash("private letter body"),
        )
        summary = __import__(
            "app.async_effects.repository",
            fromlist=["InMemoryEffectKernelRepository"],
        ).InMemoryEffectKernelRepository().accept(intent).public_contract()

        serialized = str(summary)
        self.assertNotIn(intent.payload_hash, serialized)
        self.assertNotIn("private letter body", serialized)
        self.assertEqual(summary["operationState"], "accepted")
        self.assertEqual(summary["jobState"], "pending")

    def test_runtime_policy_fails_closed_until_feature_schema_and_worker_are_ready(self):
        disabled = resolve_async_effect_runtime_status(
            async_effect_v1_enabled=False,
            worker_enabled=True,
            schema_ready=True,
        )
        schema_missing = resolve_async_effect_runtime_status(
            async_effect_v1_enabled=True,
            worker_enabled=True,
            schema_ready=False,
        )
        worker_disabled = resolve_async_effect_runtime_status(
            async_effect_v1_enabled=True,
            worker_enabled=False,
            schema_ready=True,
        )

        self.assertFalse(disabled.allowed)
        self.assertEqual(disabled.reason, "asyncEffectV1Disabled")
        self.assertFalse(schema_missing.allowed)
        self.assertEqual(schema_missing.reason, "asyncEffectSchemaNotReady")
        self.assertFalse(worker_disabled.allowed)
        self.assertEqual(worker_disabled.reason, "asyncEffectWorkerDisabled")

    def test_store_readiness_accepts_only_complete_supported_probe_contracts(self):
        self.assertTrue(is_async_effect_store_ready({"status": "ready"}))
        self.assertTrue(
            is_async_effect_store_ready(
                {
                    "databaseReason": "readWriteProbeSucceeded",
                    "schemaReason": "migrationHeadVerified",
                }
            )
        )
        self.assertFalse(
            is_async_effect_store_ready(
                {
                    "status": "notReady",
                    "databaseReason": "readWriteProbeSucceeded",
                    "schemaReason": "migrationHeadVerified",
                }
            )
        )
        self.assertFalse(
            is_async_effect_store_ready({"databaseReason": "readWriteProbeSucceeded"})
        )


if __name__ == "__main__":
    unittest.main()
