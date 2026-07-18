import hashlib
import unittest
from datetime import datetime, timezone

from pydantic import ValidationError

from app.observability.events import validate_evidence_event
from app.observability.operation_metrics import (
    OperationMetricRecorder,
    summarize_operation_metrics,
)
from app.services.in_memory_store import InMemoryStore


class OperationMetricContractTests(unittest.TestCase):
    def setUp(self):
        self.occurred_at = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)

    def test_retry_attempts_have_one_logical_operation_and_two_requests(self):
        recorder = OperationMetricRecorder(
            environment="test",
            build="backend-test",
            identifier_hmac_key="operation-metrics-test-key-" + ("x" * 32),
        )
        first = recorder.build_event(
            request_key="request-private-1",
            operation_key="operation-private-1",
            attempt=1,
            route="POST /context/build",
            operation="contextBuild",
            outcome="failed",
            feedback_state="missing",
            occurred_at=self.occurred_at,
            latency_ms=81,
        )
        second = recorder.build_event(
            request_key="request-private-2",
            operation_key="operation-private-1",
            attempt=2,
            route="POST /context/build",
            operation="contextBuild",
            outcome="succeeded",
            feedback_state="received",
            occurred_at=datetime(2026, 7, 18, 12, 1, tzinfo=timezone.utc),
            latency_ms=34,
        )

        summary = summarize_operation_metrics(
            [first.model_dump(mode="json"), second.model_dump(mode="json")],
            expected_routes={"POST /context/build", "GET /profile/{user_id}"},
        )

        self.assertEqual(summary["eventCount"], 2)
        self.assertEqual(summary["requestCount"], 2)
        self.assertEqual(summary["operationCount"], 1)
        self.assertEqual(summary["attemptCount"], 2)
        self.assertEqual(summary["retryOperationCount"], 1)
        self.assertEqual(summary["outcomeCounts"], {"failed": 1, "succeeded": 1})
        self.assertEqual(summary["feedbackCounts"], {"missing": 1, "received": 1})
        self.assertEqual(summary["missingFeedbackOperationCount"], 1)
        self.assertEqual(summary["routeCoverage"], {
            "expectedRouteCount": 2,
            "coveredRouteCount": 1,
            "missingRouteCount": 1,
            "missingRoutes": ["GET /profile/{user_id}"],
            "unregisteredObservedRouteCount": 0,
            "unregisteredObservedRoutes": [],
        })
        serialized = str([first.model_dump(mode="json"), second.model_dump(mode="json")])
        self.assertNotIn("request-private", serialized)
        self.assertNotIn("operation-private", serialized)
        self.assertNotEqual(
            first.requestIdHash,
            hashlib.sha256(
                b"evidence-id-v1|request-private-1"
            ).hexdigest(),
        )

    def test_outcomes_remain_distinct_from_success(self):
        recorder = OperationMetricRecorder(environment="test", build="backend-test")
        events = [
            recorder.build_event(
                request_key=f"request-{outcome}",
                operation_key=f"operation-{outcome}",
                attempt=1,
                route="POST /context/build",
                operation="contextBuild",
                outcome=outcome,
                feedback_state="notApplicable",
                occurred_at=self.occurred_at,
            )
            for outcome in ("cancelled", "timedOut", "deduplicated", "unknown", "feedbackMissing")
        ]

        summary = summarize_operation_metrics(
            [event.model_dump(mode="json") for event in events],
            expected_routes={"POST /context/build"},
        )

        self.assertEqual(summary["operationCount"], 5)
        self.assertEqual(summary["outcomeCounts"], {
            "cancelled": 1,
            "deduplicated": 1,
            "feedbackMissing": 1,
            "timedOut": 1,
            "unknown": 1,
        })
        self.assertEqual(summary["successfulOperationCount"], 0)
        self.assertEqual(summary["unknownOperationCount"], 2)
        self.assertEqual(summary["cancelledOperationCount"], 1)
        self.assertEqual(summary["timedOutOperationCount"], 1)

    def test_event_schema_rejects_body_prompt_media_and_identity_fields(self):
        event = OperationMetricRecorder(environment="test", build="backend-test").build_event(
            request_key="request-1",
            operation_key="operation-1",
            attempt=1,
            route="POST /context/build",
            operation="contextBuild",
            outcome="succeeded",
            feedback_state="received",
            occurred_at=self.occurred_at,
        ).model_dump(mode="json")

        for forbidden in ("body", "prompt", "media", "phone", "userId", "token"):
            with self.subTest(forbidden=forbidden):
                with self.assertRaises(ValidationError):
                    validate_evidence_event({**event, forbidden: "private-value"})


class OperationMetricStoreTests(unittest.TestCase):
    def test_in_memory_summary_is_persisted_and_excludes_expired_events(self):
        store = InMemoryStore()
        recorder = OperationMetricRecorder(environment="test", build="backend-test")
        visible = recorder.build_event(
            request_key="request-visible",
            operation_key="operation-visible",
            attempt=1,
            route="POST /context/build",
            operation="contextBuild",
            outcome="succeeded",
            feedback_state="received",
            occurred_at=datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc),
        )
        expired = recorder.build_event(
            request_key="request-expired",
            operation_key="operation-expired",
            attempt=1,
            route="POST /context/build",
            operation="contextBuild",
            outcome="failed",
            feedback_state="missing",
            occurred_at=datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc),
        )
        store.append_evidence_event(
            visible.model_dump(mode="json"),
            retention_class="operationalTemporary",
            expires_at_iso="2026-07-19T12:00:00+00:00",
        )
        store.append_evidence_event(
            expired.model_dump(mode="json"),
            retention_class="operationalTemporary",
            expires_at_iso="2026-07-18T11:00:00+00:00",
        )

        summary = store.summarize_operation_metrics(
            expected_routes={"POST /context/build"},
            now_iso="2026-07-18T12:30:00+00:00",
        )

        self.assertEqual(summary["eventCount"], 1)
        self.assertEqual(summary["operationCount"], 1)
        self.assertEqual(summary["outcomeCounts"], {"succeeded": 1})

    def test_recorder_persists_shadow_attempts_and_reads_the_store_summary(self):
        store = InMemoryStore()
        recorder = OperationMetricRecorder(
            environment="test",
            build="backend-test",
            event_sink=store.append_evidence_event,
            event_summary_source=lambda: store.summarize_operation_metrics(
                expected_routes={"POST /context/build"},
                now_iso="2026-07-18T12:30:00+00:00",
            ),
        )

        receipt = recorder.record_attempt(
            request_key="request-shadow",
            operation_key="operation-shadow",
            attempt=1,
            route="POST /context/build",
            operation="contextBuild",
            outcome="succeeded",
            feedback_state="received",
            occurred_at=datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc),
        )
        summary = recorder.summary()

        self.assertEqual(receipt["sinkOutcome"], "appended")
        self.assertEqual(summary["evidenceSource"], "persistent")
        self.assertEqual(summary["eventCount"], 1)
        self.assertEqual(summary["sinkPersistedCount"], 1)


class OperationMetricFastAPITests(unittest.TestCase):
    def test_registered_routes_are_shadow_recorded_and_docs_are_excluded(self):
        from fastapi.testclient import TestClient

        import app.main as main_module

        store = InMemoryStore()
        recorder = OperationMetricRecorder(
            environment="test",
            build="backend-test",
            event_sink=store.append_evidence_event,
            event_summary_source=lambda: store.summarize_operation_metrics(
                expected_routes={"GET /health"},
            ),
        )
        previous = main_module.OPERATION_METRIC_RECORDER
        main_module.OPERATION_METRIC_RECORDER = recorder
        try:
            client = TestClient(main_module.app)
            first = client.get(
                "/health",
                headers={
                    "X-DreamJourney-Request-Id": "6a40d20a-16fd-4f01-9f6c-9f550df09e01",
                    "X-DreamJourney-Operation-Id": "7af79875-0292-4bf8-9f76-f30aac8f2b41",
                    "X-DreamJourney-Operation-Attempt": "1",
                    "X-DreamJourney-Feedback-State": "received",
                },
            )
            second = client.get(
                "/health",
                headers={
                    "X-DreamJourney-Request-Id": "aaa86144-8d8e-463c-894d-16a657f49743",
                    "X-DreamJourney-Operation-Id": "7af79875-0292-4bf8-9f76-f30aac8f2b41",
                    "X-DreamJourney-Operation-Attempt": "2",
                    "X-DreamJourney-Feedback-State": "received",
                },
            )
            docs = client.get("/docs")
        finally:
            main_module.OPERATION_METRIC_RECORDER = previous

        summary = recorder.summary()
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(docs.status_code, 200)
        self.assertEqual(summary["eventCount"], 2)
        self.assertEqual(summary["operationCount"], 1)
        self.assertEqual(summary["retryOperationCount"], 1)
        self.assertEqual(summary["routeCounts"], {"GET /health": 2})

    def test_untrusted_operation_headers_do_not_create_a_false_retry_or_raw_digest(self):
        from fastapi.testclient import TestClient

        import app.main as main_module

        store = InMemoryStore()
        recorder = OperationMetricRecorder(
            environment="test",
            build="backend-test",
            event_sink=store.append_evidence_event,
            event_summary_source=lambda: store.summarize_operation_metrics(
                expected_routes={"GET /health"},
            ),
            identifier_hmac_key="operation-metrics-test-key-" + ("x" * 32),
        )
        previous = main_module.OPERATION_METRIC_RECORDER
        main_module.OPERATION_METRIC_RECORDER = recorder
        try:
            response = TestClient(main_module.app).get(
                "/health",
                headers={
                    "X-DreamJourney-Request-Id": "user@example.com",
                    "X-DreamJourney-Operation-Id": "private-retry-group",
                    "X-DreamJourney-Operation-Attempt": "2",
                },
            )
        finally:
            main_module.OPERATION_METRIC_RECORDER = previous

        records = list(store._evidence_events.values())
        serialized = str(records)
        summary = recorder.summary()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(summary["retryOperationCount"], 0)
        self.assertNotIn("user@example.com", serialized)
        self.assertNotIn("private-retry-group", serialized)

    def test_shadow_sink_failure_never_changes_the_business_response(self):
        from fastapi.testclient import TestClient

        import app.main as main_module

        def failing_sink(*args, **kwargs):
            raise RuntimeError("evidence sink unavailable")

        recorder = OperationMetricRecorder(
            environment="test",
            build="backend-test",
            event_sink=failing_sink,
        )
        previous = main_module.OPERATION_METRIC_RECORDER
        main_module.OPERATION_METRIC_RECORDER = recorder
        try:
            response = TestClient(main_module.app).get("/health")
        finally:
            main_module.OPERATION_METRIC_RECORDER = previous

        self.assertEqual(response.status_code, 200)
        self.assertEqual(recorder.summary()["sinkFailureCount"], 1)

    def test_authentication_denial_is_shadow_recorded_as_a_failed_attempt(self):
        from fastapi.testclient import TestClient

        import app.main as main_module

        store = InMemoryStore()
        recorder = OperationMetricRecorder(
            environment="test",
            build="backend-test",
            event_sink=store.append_evidence_event,
            event_summary_source=lambda: store.summarize_operation_metrics(
                expected_routes={"GET /ops/release-policy/observations"},
            ),
        )
        previous = main_module.OPERATION_METRIC_RECORDER
        main_module.OPERATION_METRIC_RECORDER = recorder
        try:
            response = TestClient(main_module.app).get(
                "/ops/release-policy/observations"
            )
        finally:
            main_module.OPERATION_METRIC_RECORDER = previous

        summary = recorder.summary()
        self.assertEqual(response.status_code, 401)
        self.assertEqual(summary["failedOperationCount"], 1)
        self.assertEqual(summary["outcomeCounts"], {"failed": 1})

    def test_explicit_missing_feedback_is_not_counted_as_a_success(self):
        from fastapi.testclient import TestClient

        import app.main as main_module

        store = InMemoryStore()
        recorder = OperationMetricRecorder(
            environment="test",
            build="backend-test",
            event_sink=store.append_evidence_event,
            event_summary_source=lambda: store.summarize_operation_metrics(
                expected_routes={"GET /health"},
            ),
        )
        previous = main_module.OPERATION_METRIC_RECORDER
        main_module.OPERATION_METRIC_RECORDER = recorder
        try:
            response = TestClient(main_module.app).get(
                "/health",
                headers={"X-DreamJourney-Feedback-State": "missing"},
            )
        finally:
            main_module.OPERATION_METRIC_RECORDER = previous

        summary = recorder.summary()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(summary["successfulOperationCount"], 0)
        self.assertEqual(summary["unknownOperationCount"], 1)
        self.assertEqual(summary["outcomeCounts"], {"feedbackMissing": 1})

    def test_machine_observations_expose_only_operation_metric_summary(self):
        from fastapi.testclient import TestClient

        import app.main as main_module

        store = InMemoryStore()
        recorder = OperationMetricRecorder(
            environment="test",
            build="backend-test",
            event_sink=store.append_evidence_event,
            event_summary_source=lambda: store.summarize_operation_metrics(
                expected_routes={"GET /health"},
            ),
        )
        previous_recorder = main_module.OPERATION_METRIC_RECORDER
        previous_token = main_module.BACKEND_API_TOKEN
        main_module.OPERATION_METRIC_RECORDER = recorder
        main_module.BACKEND_API_TOKEN = "operation-metrics-machine-token"
        try:
            client = TestClient(main_module.app)
            health = client.get("/health")
            observations = client.get(
                "/ops/release-policy/observations",
                headers={"Authorization": "Bearer operation-metrics-machine-token"},
            )
        finally:
            main_module.OPERATION_METRIC_RECORDER = previous_recorder
            main_module.BACKEND_API_TOKEN = previous_token

        self.assertEqual(health.status_code, 200)
        self.assertEqual(observations.status_code, 200)
        metrics = observations.json()["operationMetrics"]
        self.assertEqual(metrics["eventCount"], 1)
        self.assertNotIn("events", metrics)
        self.assertNotIn("routeCounts", metrics)
        self.assertNotIn("missingRoutes", str(metrics))
        self.assertNotIn("request-private", str(metrics))


if __name__ == "__main__":
    unittest.main()
