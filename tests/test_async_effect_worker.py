from __future__ import annotations

from contextlib import contextmanager
import unittest

from app.async_effects.lease_repository import AsyncEffectJobPreview
from app.async_effects.worker import AsyncEffectWorkerRuntime
from app.core.config import Settings


class _LeaseRepository:
    def __init__(self, previews):
        self.previews = list(previews)
        self.preview_calls = 0

    def preview_eligible(self, *, limit: int):
        self.preview_calls += 1
        return self.previews[:limit]


class _FailingLeaseRepository(_LeaseRepository):
    def preview_eligible(self, *, limit: int):
        self.preview_calls += 1
        raise RuntimeError("synthetic observation failure")


class _Store:
    def __init__(self, *, ready: bool = True, previews=()):
        self.ready = ready
        self.repository = _LeaseRepository(previews)
        self.uow_calls = 0

    def readiness_probe(self):
        return {"status": "ready" if self.ready else "notReady"}

    @contextmanager
    def request_unit_of_work(self, **_kwargs):
        self.uow_calls += 1
        yield self

    def async_effect_lease_repository(self):
        return self.repository


class AsyncEffectWorkerRuntimeTests(unittest.TestCase):
    def test_run_once_fails_closed_without_runtime_flags(self):
        store = _Store()
        worker = AsyncEffectWorkerRuntime(settings=Settings(), store=store, worker_id="worker-test")

        result = worker.run_once()

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "asyncEffectV1Disabled")
        self.assertEqual(store.repository.preview_calls, 0)

    def test_run_once_with_flags_does_not_claim_before_a_handler_is_registered(self):
        store = _Store()
        worker = AsyncEffectWorkerRuntime(
            settings=Settings(async_effect_v1_enabled=True, async_effect_worker_enabled=True),
            store=store,
            worker_id="worker-test",
        )

        result = worker.run_once()

        self.assertEqual(result["status"], "idle")
        self.assertEqual(result["reason"], "asyncEffectNoRunnableHandlers")
        self.assertEqual(store.repository.preview_calls, 0)
        self.assertEqual(result["readinessEvidence"]["observationState"], "blocked")
        self.assertFalse(result["readinessEvidence"]["ready"])

    def test_shadow_once_reports_value_free_job_summary_without_claiming(self):
        store = _Store(
            previews=[
                AsyncEffectJobPreview(
                    job_id="job-1",
                    operation_id="operation-1",
                    job_type="asyncEffect.synthetic.noop",
                    state="pending",
                    attempt=0,
                    available_at="2026-07-19T00:00:00+00:00",
                )
            ]
        )
        worker = AsyncEffectWorkerRuntime(settings=Settings(), store=store, worker_id="worker-test")

        result = worker.shadow_once()

        self.assertEqual(result["status"], "observed")
        self.assertEqual(result["eligibleJobCount"], 1)
        self.assertEqual(result["eligibleJobTypes"], ["asyncEffect.synthetic.noop"])
        self.assertEqual(store.repository.preview_calls, 1)
        self.assertEqual(store.uow_calls, 1)
        self.assertEqual(result["readinessEvidence"]["observationState"], "blocked")
        self.assertEqual(result["readinessEvidence"]["backlogEligibleCount"], 1)

    def test_shadow_once_fails_closed_when_readiness_is_not_ready(self):
        store = _Store(ready=False)
        worker = AsyncEffectWorkerRuntime(
            settings=Settings(async_effect_v1_enabled=True, async_effect_worker_enabled=True),
            store=store,
            worker_id="worker-test",
        )

        result = worker.run_once()

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "asyncEffectSchemaNotReady")

    def test_shadow_once_marks_an_unsupported_store_as_skipped_evidence(self):
        worker = AsyncEffectWorkerRuntime(
            settings=Settings(),
            store=object(),
            worker_id="worker-test",
        )

        result = worker.shadow_once()

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "asyncEffectWorkerStoreUnsupported")
        self.assertEqual(result["readinessEvidence"]["observationState"], "skipped")
        self.assertFalse(result["readinessEvidence"]["ready"])

    def test_shadow_once_marks_a_collection_failure_as_unknown_evidence(self):
        store = _Store()
        store.repository = _FailingLeaseRepository(())
        worker = AsyncEffectWorkerRuntime(
            settings=Settings(),
            store=store,
            worker_id="worker-test",
        )

        result = worker.shadow_once()

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "asyncEffectBacklogObservationFailed")
        self.assertEqual(result["readinessEvidence"]["observationState"], "unknown")
        self.assertFalse(result["readinessEvidence"]["ready"])

    def test_run_once_accepts_the_postgres_readiness_probe_contract(self):
        store = _Store()
        store.readiness_probe = lambda: {
            "databaseReason": "readWriteProbeSucceeded",
            "schemaReason": "migrationHeadVerified",
        }
        worker = AsyncEffectWorkerRuntime(
            settings=Settings(async_effect_v1_enabled=True, async_effect_worker_enabled=True),
            store=store,
            worker_id="worker-test",
        )

        result = worker.run_once()

        self.assertEqual(result["status"], "idle")
        self.assertEqual(result["reason"], "asyncEffectNoRunnableHandlers")


if __name__ == "__main__":
    unittest.main()
