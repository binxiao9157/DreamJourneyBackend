import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.main as main_module
from app.core.config import Settings
from app.main import app
from app.observability.provider_costs import (
    ProviderCostEvidenceRecorder,
    summarize_provider_cost_evidence,
)
from app.services.in_memory_store import InMemoryStore


class ProviderCostEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.occurred_at = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)

    def test_recorder_hashes_provider_request_and_principal_identifiers(self):
        recorder = ProviderCostEvidenceRecorder(
            environment="test",
            build="backend-test",
            identifier_hmac_key="provider-cost-test-key-" + ("x" * 32),
        )

        event = recorder.build_event(
            request_key="request-private-1",
            operation_key="operation-private-1",
            provider="volcengineVoiceClone",
            capability="voiceCloneSynthesis",
            unit_type="character",
            units=12,
            state="succeeded",
            reason="providerUsageObserved",
            principal_key="owner-private-1",
            provider_request_key="provider-request-private-1",
            occurred_at=self.occurred_at,
        )

        serialized = json.dumps(event.model_dump(mode="json"), sort_keys=True)
        for raw_value in (
            "request-private-1",
            "operation-private-1",
            "owner-private-1",
            "provider-request-private-1",
        ):
            self.assertNotIn(raw_value, serialized)
        self.assertEqual(event.costSource, "unknown")
        self.assertIsNone(event.costMicros)

    def test_unknown_cost_events_never_claim_cost_readiness(self):
        recorder = ProviderCostEvidenceRecorder(
            environment="test",
            build="backend-test",
        )
        observed = recorder.build_event(
            request_key="request-one",
            operation_key="operation-one",
            provider="deepseek",
            capability="kbExtract",
            unit_type="request",
            units=1,
            state="succeeded",
            reason="providerUsageObserved",
            occurred_at=self.occurred_at,
        )
        failed = recorder.build_event(
            request_key="request-two",
            operation_key="operation-two",
            provider="deepseek",
            capability="kbExtract",
            unit_type="request",
            units=1,
            state="failed",
            reason="providerCallFailed",
            occurred_at=self.occurred_at,
        )

        summary = summarize_provider_cost_evidence(
            [observed.model_dump(mode="json"), failed.model_dump(mode="json")]
        )

        self.assertEqual(summary["eventCount"], 2)
        self.assertEqual(summary["unknownCostEventCount"], 2)
        self.assertEqual(summary["knownCostEventCount"], 0)
        self.assertEqual(summary["readiness"]["status"], "notReady")
        self.assertEqual(summary["readiness"]["reason"], "providerCostUnknown")
        self.assertFalse(summary["readiness"]["costLimitEnforcementAllowed"])
        self.assertFalse(summary["readiness"]["providerExpansionAllowed"])

    def test_approved_rate_card_requires_a_version_but_still_does_not_close_budget_gate(self):
        recorder = ProviderCostEvidenceRecorder(
            environment="test",
            build="backend-test",
        )
        event = recorder.build_event(
            request_key="request-one",
            operation_key="operation-one",
            provider="volcengineVoiceClone",
            capability="voiceCloneSynthesis",
            unit_type="character",
            units=20,
            state="succeeded",
            reason="providerUsageObserved",
            cost_source="approvedRateCard",
            cost_micros=200,
            rate_card_version="volc-2026-07",
            occurred_at=self.occurred_at,
        )

        summary = summarize_provider_cost_evidence([event.model_dump(mode="json")])

        self.assertEqual(summary["knownCostEventCount"], 1)
        self.assertEqual(summary["knownCostMicros"], 200)
        self.assertEqual(summary["readiness"]["status"], "notReady")
        self.assertEqual(summary["readiness"]["reason"], "commercialBudgetDeferred")

        with self.assertRaises(ValueError):
            recorder.build_event(
                request_key="request-invalid",
                operation_key="operation-invalid",
                provider="volcengineVoiceClone",
                capability="voiceCloneSynthesis",
                unit_type="character",
                units=20,
                state="succeeded",
                reason="providerUsageObserved",
                cost_source="approvedRateCard",
                cost_micros=200,
                occurred_at=self.occurred_at,
            )

    def test_persistent_sink_uses_provider_cost_retention_without_raw_identifier_export(self):
        store = InMemoryStore()
        recorder = ProviderCostEvidenceRecorder(
            environment="test",
            build="backend-test",
            identifier_hmac_key="provider-cost-persistence-key-" + ("y" * 32),
            event_sink=store.append_evidence_event,
            event_source=lambda: store.list_evidence_events(event_type="providerCost"),
        )

        receipt = recorder.record_attempt(
            request_key="request-private-1",
            operation_key="operation-private-1",
            provider="amap",
            capability="districtLookup",
            unit_type="request",
            units=1,
            state="succeeded",
            reason="providerUsageObserved",
            occurred_at=self.occurred_at,
        )
        summary = recorder.summary()

        self.assertEqual(receipt["sinkOutcome"], "appended")
        self.assertEqual(summary["evidenceSource"], "persistent")
        self.assertEqual(summary["eventCount"], 1)
        self.assertEqual(summary["retentionClass"], "providerCost")
        self.assertNotIn("request-private-1", json.dumps(summary, sort_keys=True))


class ProviderCostEvidenceRuntimeTests(unittest.TestCase):
    def test_evidence_recorder_failure_does_not_change_provider_response(self):
        class ExplodingRecorder:
            def record_attempt(self, **kwargs):
                raise RuntimeError("evidence sink unavailable")

        previous_settings = main_module.settings
        main_module.settings = Settings(
            store_backend="memory",
            amap_web_service_key="fixture-amap-key",
        )
        try:
            with patch.object(
                main_module,
                "PROVIDER_COST_EVIDENCE_RECORDER",
                ExplodingRecorder(),
            ), patch.object(
                main_module.AMapDistrictProxy,
                "request_district",
                return_value={"districts": []},
            ):
                response = TestClient(app).get("/maps/district?keyword=qa-city")
        finally:
            main_module.settings = previous_settings

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"districts": []})

    def test_provider_runtime_attempt_persists_unknown_cost_without_query_leak(self):
        previous_store = main_module.store
        previous_settings = main_module.settings
        main_module.store = InMemoryStore()
        main_module.settings = Settings(
            store_backend="memory",
            amap_web_service_key="fixture-amap-key",
        )
        try:
            with patch.object(
                main_module.AMapDistrictProxy,
                "request_district",
                return_value={"districts": []},
            ):
                response = TestClient(app).get(
                    "/maps/district?keyword=MAP_QUERY_PRIVATE_CANARY"
                )
            summary = main_module.PROVIDER_COST_EVIDENCE_RECORDER.summary()
        finally:
            main_module.store = previous_store
            main_module.settings = previous_settings

        self.assertEqual(response.status_code, 200)
        self.assertEqual(summary["eventCount"], 1)
        self.assertEqual(summary["unknownCostEventCount"], 1)
        self.assertEqual(summary["providerCapabilityCounts"], {"amap:districtLookup": 1})
        self.assertEqual(summary["readiness"]["reason"], "providerCostUnknown")
        self.assertNotIn("MAP_QUERY_PRIVATE_CANARY", json.dumps(summary, sort_keys=True))

    def test_kb_local_post_processing_failure_is_not_recorded_as_provider_failure(self):
        store = InMemoryStore()
        recorder = ProviderCostEvidenceRecorder(
            environment="test",
            build="backend-test",
            event_sink=store.append_evidence_event,
            event_source=lambda: store.list_evidence_events(event_type="providerCost"),
        )
        previous_store = main_module.store
        previous_settings = main_module.settings
        main_module.store = store
        main_module.settings = Settings(
            store_backend="memory",
            deepseek_api_key="fixture-deepseek-key",
        )
        try:
            with patch.object(
                main_module,
                "PROVIDER_COST_EVIDENCE_RECORDER",
                recorder,
            ), patch.object(
                main_module.DeepSeekKnowledgeExtractionProxy,
                "request_extraction",
                return_value={"people": [], "places": [], "events": [], "facts": []},
            ), patch.object(
                main_module,
                "filter_extraction_by_evidence",
                side_effect=ValueError("local evidence filter failure"),
            ):
                response = TestClient(app).post(
                    "/kb/extract",
                    json={
                        "userId": "provider-cost-owner",
                        "transcript": "本地过滤失败不应改变 Provider 成功记录。",
                        "privacyMetadata": {"scope": "generationAllowed"},
                    },
                )
        finally:
            main_module.store = previous_store
            main_module.settings = previous_settings

        summary = recorder.summary()
        self.assertEqual(response.status_code, 502)
        self.assertEqual(summary["eventCount"], 1)
        self.assertEqual(summary["stateCounts"], {"succeeded": 1})


if __name__ == "__main__":
    unittest.main()
