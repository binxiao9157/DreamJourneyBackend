from __future__ import annotations

from contextlib import contextmanager
import unittest

from app.async_effects.scheduler import AsyncEffectSchedulerRuntime
from app.async_effects.scheduler_repository import AsyncEffectSchedulerPreview
from app.core.config import Settings


class _SchedulerRepository:
    def __init__(self, previews):
        self.previews = list(previews)
        self.preview_calls = 0

    def preview_eligible(self, *, limit: int):
        self.preview_calls += 1
        return self.previews[:limit]


class _Store:
    def __init__(self, *, ready: bool = True, previews=()):
        self.ready = ready
        self.repository = _SchedulerRepository(previews)
        self.uow_calls = 0

    def readiness_probe(self):
        return {"status": "ready" if self.ready else "notReady"}

    @contextmanager
    def request_unit_of_work(self, **_kwargs):
        self.uow_calls += 1
        yield self

    def async_effect_scheduler_repository(self):
        return self.repository


class AsyncEffectSchedulerRuntimeTests(unittest.TestCase):
    def test_run_once_fails_closed_without_runtime_flags(self):
        store = _Store()
        scheduler = AsyncEffectSchedulerRuntime(
            settings=Settings(),
            store=store,
            scheduler_id="scheduler-test",
        )

        result = scheduler.run_once()

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "asyncEffectV1Disabled")
        self.assertEqual(store.repository.preview_calls, 0)

    def test_run_once_with_flags_does_not_claim_before_scheduler_admission(self):
        store = _Store()
        scheduler = AsyncEffectSchedulerRuntime(
            settings=Settings(async_effect_v1_enabled=True, async_effect_worker_enabled=True),
            store=store,
            scheduler_id="scheduler-test",
        )

        result = scheduler.run_once()

        self.assertEqual(result["status"], "idle")
        self.assertEqual(result["reason"], "asyncEffectNoRunnableSchedulers")
        self.assertEqual(store.repository.preview_calls, 0)

    def test_shadow_once_reports_value_free_scheduler_summary_without_claiming(self):
        store = _Store(
            previews=[
                AsyncEffectSchedulerPreview(
                    lease_id="lease-1",
                    operation_id="operation-1",
                    scheduler_key="scheduler.synthetic.tick",
                    state="available",
                    attempt=0,
                )
            ]
        )
        scheduler = AsyncEffectSchedulerRuntime(
            settings=Settings(),
            store=store,
            scheduler_id="scheduler-test",
        )

        result = scheduler.shadow_once()

        self.assertEqual(result["status"], "observed")
        self.assertEqual(result["eligibleSchedulerLeaseCount"], 1)
        self.assertEqual(result["eligibleSchedulerKeys"], ["scheduler.synthetic.tick"])
        self.assertEqual(store.repository.preview_calls, 1)
        self.assertEqual(store.uow_calls, 1)

    def test_shadow_once_fails_closed_when_store_is_unsupported(self):
        scheduler = AsyncEffectSchedulerRuntime(
            settings=Settings(),
            store=object(),
            scheduler_id="scheduler-test",
        )

        result = scheduler.shadow_once()

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "asyncEffectSchedulerStoreUnsupported")


if __name__ == "__main__":
    unittest.main()
