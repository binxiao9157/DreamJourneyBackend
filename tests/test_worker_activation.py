import io
import json
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.async_effects.worker_activation import (
    WorkerReadinessProbeError,
    evaluate_worker_activation,
    live_schema_ready,
    main,
    run_worker_activation_preflight,
)
from app.async_effects.worker_deployment_registry import (
    LONG_RUNNING_WORKERS,
    deployment_inventory,
)
from app.core.config import Settings


class WorkerDeploymentRegistryTests(unittest.TestCase):
    def test_registry_is_complete_unique_and_value_free(self):
        inventory = deployment_inventory()
        serialized = json.dumps(inventory, sort_keys=True)

        self.assertEqual(len(inventory), 7)
        self.assertEqual(len({item["worker"] for item in inventory}), 7)
        self.assertEqual(len({item["settingsFlag"] for item in inventory}), 7)
        self.assertEqual(len({item["composeService"] for item in inventory}), 7)
        self.assertEqual(
            {item["composeService"] for item in inventory},
            {
                "narrative-generation-worker",
                "owner-truth-candidate-extraction-worker",
                "owner-truth-memory-projection-worker",
                "owner-truth-media-processing-worker",
                "owner-truth-media-deletion-worker",
                "business-message-projection-worker",
                "publication-external-cleanup-materializer-worker",
            },
        )
        for spec in LONG_RUNNING_WORKERS:
            self.assertTrue(hasattr(Settings, spec.settings_flag))
        self.assertNotIn("secret", serialized.lower())
        self.assertNotIn("token", serialized.lower())
        self.assertNotIn("password", serialized.lower())

    def test_registry_resolves_enabled_state_without_values(self):
        settings = Settings(
            business_message_projection_worker_enabled=True,
            publication_external_cleanup_materializer_enabled=False,
        )
        inventory = {
            item["worker"]: item
            for item in deployment_inventory(settings=settings)
        }

        self.assertTrue(inventory["businessMessageProjection"]["enabled"])
        self.assertFalse(
            inventory["publicationExternalCleanupMaterializer"]["enabled"]
        )


class WorkerActivationTests(unittest.TestCase):
    @staticmethod
    def settings(**overrides):
        values = {
            "environment": "production",
            "store_backend": "postgres",
            "async_effect_v1_enabled": True,
            "async_effect_worker_enabled": True,
            "business_message_projection_worker_enabled": True,
            "publication_external_cleanup_materializer_enabled": True,
        }
        values.update(overrides)
        return Settings(**values)

    def test_non_owner_workers_use_the_shared_fail_closed_contract(self):
        for worker, ready_reason in (
            (
                "businessMessageProjection",
                "businessMessageProjectionWorkerActivationReady",
            ),
            (
                "publicationExternalCleanupMaterializer",
                "publicationExternalCleanupMaterializerActivationReady",
            ),
        ):
            with self.subTest(worker=worker):
                ready = evaluate_worker_activation(
                    worker=worker,
                    settings=self.settings(),
                    schema_ready=True,
                )
                schema_blocked = evaluate_worker_activation(
                    worker=worker,
                    settings=self.settings(),
                    schema_ready=False,
                )

                self.assertTrue(ready.ready)
                self.assertEqual(ready.reason, ready_reason)
                self.assertFalse(schema_blocked.ready)
                self.assertEqual(schema_blocked.reason, "asyncEffectSchemaNotReady")

    def test_worker_specific_kill_switches_remain_independent(self):
        business = evaluate_worker_activation(
            worker="businessMessageProjection",
            settings=self.settings(business_message_projection_worker_enabled=False),
            schema_ready=True,
        )
        publication = evaluate_worker_activation(
            worker="publicationExternalCleanupMaterializer",
            settings=self.settings(
                publication_external_cleanup_materializer_enabled=False
            ),
            schema_ready=True,
        )

        self.assertEqual(business.reason, "businessMessageProjectionWorkerDisabled")
        self.assertEqual(
            publication.reason,
            "publicationExternalCleanupMaterializerDisabled",
        )

    def test_narrative_worker_fails_closed_until_provider_is_fully_configured(self):
        disabled = evaluate_worker_activation(
            worker="narrativeGeneration",
            settings=self.settings(narrative_generation_worker_enabled=False),
            schema_ready=True,
        )
        provider_missing = evaluate_worker_activation(
            worker="narrativeGeneration",
            settings=self.settings(narrative_generation_worker_enabled=True),
            schema_ready=True,
        )
        credential_missing = evaluate_worker_activation(
            worker="narrativeGeneration",
            settings=self.settings(
                narrative_generation_worker_enabled=True,
                narrative_generation_provider="deepseek",
                narrative_generation_model="deepseek-chat",
            ),
            schema_ready=True,
        )
        model_missing = evaluate_worker_activation(
            worker="narrativeGeneration",
            settings=self.settings(
                narrative_generation_worker_enabled=True,
                narrative_generation_provider="deepseek",
                deepseek_api_key="fixture-key",
            ),
            schema_ready=True,
        )
        ready = evaluate_worker_activation(
            worker="narrativeGeneration",
            settings=self.settings(
                narrative_generation_worker_enabled=True,
                narrative_generation_provider="deepseek",
                narrative_generation_model="deepseek-chat",
                deepseek_api_key="fixture-key",
            ),
            schema_ready=True,
        )

        self.assertEqual(disabled.reason, "narrativeGenerationWorkerDisabled")
        self.assertEqual(
            provider_missing.reason,
            "narrativeGenerationProviderNotConfigured",
        )
        self.assertEqual(
            credential_missing.reason,
            "narrativeGenerationProviderCredentialNotConfigured",
        )
        self.assertEqual(
            model_missing.reason,
            "narrativeGenerationModelNotConfigured",
        )
        self.assertTrue(ready.ready)
        self.assertEqual(ready.reason, "narrativeGenerationWorkerActivationReady")

    def test_typed_probe_failure_is_structured_and_retryable(self):
        def failed_probe(_settings):
            raise WorkerReadinessProbeError(
                stage="openStore",
                code="storeOpenFailed",
                retryable=True,
            )

        decision = run_worker_activation_preflight(
            worker="businessMessageProjection",
            settings=self.settings(),
            schema_probe=failed_probe,
        )

        self.assertFalse(decision.ready)
        self.assertEqual(decision.failure_stage, "openStore")
        self.assertEqual(decision.failure_code, "storeOpenFailed")
        self.assertTrue(decision.retryable)
        self.assertEqual(len(decision.correlation_id or ""), 16)

    def test_owner_truth_probe_failure_preserves_the_legacy_reason_code(self):
        def failed_probe(_settings):
            raise WorkerReadinessProbeError(
                stage="openStore",
                code="storeOpenFailed",
                retryable=True,
            )

        decision = run_worker_activation_preflight(
            worker="ownerTruthCandidateExtraction",
            settings=self.settings(),
            schema_probe=failed_probe,
        )

        self.assertEqual(decision.reason, "ownerTruthWorkerReadinessProbeFailed")

    def test_unknown_probe_exception_never_exposes_the_raw_message(self):
        provider_secret = "postgres://owner:super-secret@example.invalid/db"

        def failed_probe(_settings):
            raise RuntimeError(provider_secret)

        decision = run_worker_activation_preflight(
            worker="businessMessageProjection",
            settings=self.settings(),
            schema_probe=failed_probe,
        )
        serialized = json.dumps(decision.public_descriptor(), sort_keys=True)

        self.assertEqual(decision.failure_code, "unexpectedReadinessFailure")
        self.assertNotIn(provider_secret, serialized)
        self.assertNotIn("super-secret", serialized)

    def test_open_failure_is_not_masked_by_close_failure(self):
        store = object()
        with patch(
            "app.async_effects.worker_activation.make_store",
            return_value=store,
        ), patch(
            "app.async_effects.worker_activation.open_store",
            side_effect=RuntimeError("open-sensitive-value"),
        ), patch(
            "app.async_effects.worker_activation.close_store",
            side_effect=RuntimeError("close-sensitive-value"),
        ):
            with self.assertRaises(WorkerReadinessProbeError) as context:
                live_schema_ready(self.settings())

        self.assertEqual(context.exception.stage, "openStore")
        self.assertEqual(context.exception.code, "storeOpenFailed")

    def test_close_failure_after_successful_probe_is_classified(self):
        store = SimpleNamespace(readiness_probe=lambda: {"status": "ready"})
        with patch(
            "app.async_effects.worker_activation.make_store",
            return_value=store,
        ), patch(
            "app.async_effects.worker_activation.open_store"
        ), patch(
            "app.async_effects.worker_activation.is_async_effect_store_ready",
            return_value=True,
        ), patch(
            "app.async_effects.worker_activation.close_store",
            side_effect=RuntimeError("close-sensitive-value"),
        ):
            with self.assertRaises(WorkerReadinessProbeError) as context:
                live_schema_ready(self.settings())

        self.assertEqual(context.exception.stage, "closeStore")
        self.assertEqual(context.exception.code, "storeCloseFailed")

    def test_cli_diagnostic_stdout_and_stderr_are_value_free(self):
        secret = "provider-token-must-not-appear"
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch(
            "app.async_effects.worker_activation.Settings.from_env",
            return_value=self.settings(),
        ), patch(
            "app.async_effects.worker_activation.live_schema_ready",
            side_effect=RuntimeError(secret),
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = main(["--worker", "businessMessageProjection"])

        descriptor = json.loads(stdout.getvalue())
        diagnostic = json.loads(stderr.getvalue())
        combined = stdout.getvalue() + stderr.getvalue()
        self.assertEqual(exit_code, 1)
        self.assertEqual(descriptor["failureCode"], "unexpectedReadinessFailure")
        self.assertEqual(diagnostic["failureCode"], "unexpectedReadinessFailure")
        self.assertEqual(descriptor["correlationId"], diagnostic["correlationId"])
        self.assertNotIn(secret, combined)


if __name__ == "__main__":
    unittest.main()
