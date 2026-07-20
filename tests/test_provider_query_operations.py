from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from app.async_effects.provider_effects import ProviderEffectCatalogEntry
from app.async_effects.provider_query_operations import (
    ProviderQueryBacklogEntry,
    ProviderQueryOperationsObservationState,
    build_provider_query_operations_evidence,
)


class ProviderQueryOperationsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 7, 20, 8, 0, tzinfo=timezone.utc)
        self.catalog = (
            ProviderEffectCatalogEntry(
                key="deepseek.kbExtract",
                provider="deepseek",
                capability="kbExtract",
                source_paths=("app/services/deepseek.py:DeepSeekKnowledgeExtractionProxy",),
                current_execution="syncDirect",
                request_id_strategy="none",
                query_reconcile_support="unsupported",
                migration_disposition="stableRequestBeforeEnable",
                default_exposure="existingPublicShell",
            ),
            ProviderEffectCatalogEntry(
                key="volcengineVoiceClone.training",
                provider="volcengineVoiceClone",
                capability="voiceCloneTraining",
                source_paths=("app/services/voice_clone.py:VolcEngineVoiceCloneV3Provider.query_training",),
                current_execution="adapterOnly",
                request_id_strategy="providerRequestId",
                query_reconcile_support="providerQuery",
                migration_disposition="stableRequestBeforeEnable",
                default_exposure="defaultOff",
            ),
        )

    def test_observed_backlog_is_value_free_and_cannot_enable_execution(self):
        evidence = build_provider_query_operations_evidence(
            catalog_entries=self.catalog,
            backlog_entries=(
                ProviderQueryBacklogEntry(
                    provider="volcengineVoiceClone",
                    capability="voiceCloneTraining",
                    unknown_effect_count=2,
                    pending_reconciliation_count=1,
                    manual_review_count=1,
                    reconciliation_conflict_count=0,
                ),
            ),
            observed_at=self.now,
            expires_at=self.now + timedelta(minutes=5),
        )

        summary = evidence.value_free_summary(now=self.now)

        self.assertEqual(evidence.observation_state, ProviderQueryOperationsObservationState.OBSERVED)
        self.assertEqual(summary["unknownEffectCount"], 2)
        self.assertEqual(summary["pendingReconciliationCount"], 1)
        self.assertEqual(summary["manualReviewCount"], 1)
        self.assertTrue(summary["externalProviderQueryGateOpen"])
        self.assertFalse(summary["providerQueryExecutionEnabled"])
        self.assertFalse(summary["automaticReconciliationEnabled"])
        self.assertFalse(summary["replayEnabled"])
        self.assertTrue(summary["requiresManualReview"])
        serialized = str(summary)
        for forbidden in (
            "owner-private",
            "vault-private",
            "resource-private",
            "provider-request-private",
            "credential",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_clear_skipped_unknown_and_expired_observations_fail_closed(self):
        clear = build_provider_query_operations_evidence(
            catalog_entries=self.catalog,
            backlog_entries=(),
            observed_at=self.now,
            expires_at=self.now + timedelta(minutes=5),
        )
        skipped = build_provider_query_operations_evidence(
            catalog_entries=self.catalog,
            backlog_entries=(),
            observed_at=self.now,
            expires_at=self.now + timedelta(minutes=5),
            store_supported=False,
        )
        unknown = build_provider_query_operations_evidence(
            catalog_entries=self.catalog,
            backlog_entries=(),
            observed_at=self.now,
            expires_at=self.now + timedelta(minutes=5),
            collection_error_code="providerQueryOperationsObservationFailed",
        )

        self.assertEqual(clear.value_free_summary(now=self.now)["observationState"], "clear")
        self.assertFalse(clear.value_free_summary(now=self.now)["requiresManualReview"])
        self.assertTrue(clear.value_free_summary(now=self.now)["externalProviderQueryGateOpen"])
        self.assertEqual(skipped.value_free_summary(now=self.now)["observationState"], "skipped")
        self.assertTrue(skipped.value_free_summary(now=self.now)["requiresManualReview"])
        self.assertEqual(unknown.value_free_summary(now=self.now)["observationState"], "unknown")
        self.assertTrue(unknown.value_free_summary(now=self.now)["requiresManualReview"])
        self.assertEqual(
            clear.value_free_summary(now=self.now + timedelta(minutes=6))["observationState"],
            "expired",
        )

    def test_uncatalogued_backlog_is_visible_and_manual_review_only(self):
        evidence = build_provider_query_operations_evidence(
            catalog_entries=self.catalog,
            backlog_entries=(
                ProviderQueryBacklogEntry(
                    provider="unknownProvider",
                    capability="unknownCapability",
                    unknown_effect_count=1,
                    pending_reconciliation_count=1,
                    manual_review_count=0,
                    reconciliation_conflict_count=0,
                ),
            ),
            observed_at=self.now,
            expires_at=self.now + timedelta(minutes=5),
        )

        summary = evidence.value_free_summary(now=self.now)
        self.assertEqual(summary["uncataloguedUnknownEffectCount"], 1)
        self.assertFalse(summary["providerQueryExecutionEnabled"])
        self.assertTrue(summary["requiresManualReview"])


if __name__ == "__main__":
    unittest.main()
