import asyncio
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from starlette.requests import Request
import app.main as main
from app.db.pool import ConnectionPoolExhausted
from app.services.postgres_store import PostgresStore
from tests.test_db_uow import FakeConnection

class BoundedPool:
    def __init__(self, size=2):
        self.cv=threading.Condition()
        self.available=[FakeConnection(str(i)) for i in range(size)]
        self.returned=[]
    def getconn(self, *, timeout=None):
        with self.cv:
            if not self.cv.wait_for(lambda: bool(self.available), timeout=timeout):
                raise ConnectionPoolExhausted('test pool exhausted')
            return self.available.pop()
    def putconn(self, connection):
        with self.cv:
            self.returned.append(connection); self.available.append(connection); self.cv.notify()
    def stats(self): return {'poolAvailable':len(self.available)}

class APIIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_requests_can_finish_and_return_pool_without_loop_starvation(self):
        pool=BoundedPool(); store=PostgresStore(pool=pool,pool_timeout_seconds=.3)
        async def handler(request):
            active=store._current_uow.get()
            self.assertIsNotNone(active)
            await asyncio.sleep(.015)  # request body/downstream scheduling boundary
            self.assertIs(store._current_uow.get(),active)
            return SimpleNamespace(status_code=200,headers={})
        ticks=[]
        async def ticker():
            start=time.monotonic(); await asyncio.sleep(.02); ticks.append(time.monotonic()-start)
        with patch.object(main,'store',store):
            tick=asyncio.create_task(ticker())
            results=await asyncio.gather(*(main.database_request_unit_of_work(SimpleNamespace(url=SimpleNamespace(path='/profile/test')),handler) for _ in range(8)))
            await tick
        self.assertEqual([r.status_code for r in results],[200]*8)
        self.assertLess(ticks[0],.15)
        self.assertEqual(len(pool.available),2)
        self.assertEqual(store.uow_metrics()['active'],0)

    async def test_slow_statistics_does_not_delay_response_or_event_loop(self):
        entered=threading.Event(); release=threading.Event()
        def blocked(**kwargs):
            entered.set(); release.wait(.5)
        req=Request({'type':'http','method':'GET','path':'/health','headers':[]})
        async def handler(_): return SimpleNamespace(status_code=200,headers={})
        try:
            with patch.object(main.OPERATION_METRIC_RECORDER,'record_attempt',side_effect=blocked):
                started=time.monotonic()
                response=await main.shadow_operation_metric_attempt(req,handler)
                elapsed=time.monotonic()-started
                self.assertEqual(response.status_code,200)
                self.assertLess(elapsed,.15)
                await asyncio.to_thread(entered.wait,.5)
                self.assertTrue(entered.is_set())
        finally:
            release.set()
            dispatcher=getattr(main,'OPERATION_METRIC_DISPATCHER',None)
            if dispatcher: await asyncio.to_thread(dispatcher.wait_for_idle,2)


class CancellationIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_anyio_cancelled_checkout_never_enters_handler(self):
        import anyio
        pool = BoundedPool(1)
        held = pool.getconn(timeout=.1)
        store = PostgresStore(pool=pool, pool_timeout_seconds=.3)
        entered = []
        async def release():
            await asyncio.sleep(.08)
            pool.putconn(held)
        release_task = asyncio.create_task(release())
        with anyio.move_on_after(.03) as scope:
            async with store.async_request_unit_of_work(correlation_id='scope-entry', command_id='scope-entry'):
                entered.append(True)
        await release_task
        self.assertTrue(scope.cancel_called)
        self.assertEqual(entered, [])
        self.assertEqual(held.commits, 0)
        self.assertEqual(held.rollbacks, 1)
        self.assertEqual(len(pool.available), 1)

    async def test_cancelled_checkout_returns_late_acquired_connection(self):
        pool=BoundedPool(1); held=pool.getconn(timeout=.1)
        store=PostgresStore(pool=pool,pool_timeout_seconds=.4)
        entered=threading.Event(); original=pool.getconn
        def checkout(**kw): entered.set(); return original(**kw)
        pool.getconn=checkout
        async def operation():
            async with store.async_request_unit_of_work(correlation_id='cancel',command_id='cancel'):
                self.fail('cancelled acquisition must not enter handler')
        task=asyncio.create_task(operation())
        self.assertTrue(await asyncio.to_thread(entered.wait,.2))
        task.cancel()
        pool.putconn(held)
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(len(pool.available),1)
        self.assertEqual(held.commits,0); self.assertEqual(held.rollbacks,1)
        self.assertIsNone(store._current_uow.get())
        self.assertEqual(store.uow_metrics()['active'],0)

    async def test_cancelled_handler_rolls_back_and_does_not_commit(self):
        pool=BoundedPool(1); connection=pool.available[0]
        store=PostgresStore(pool=pool)
        started=asyncio.Event()
        async def operation():
            async with store.async_request_unit_of_work(correlation_id='body',command_id='body'):
                started.set(); await asyncio.Event().wait()
        task=asyncio.create_task(operation()); await started.wait(); task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(connection.rollbacks,1); self.assertEqual(connection.commits,0)
        self.assertEqual(len(pool.available),1)

    async def test_cancellation_during_commit_waits_for_exactly_one_finalization(self):
        pool=BoundedPool(1); connection=pool.available[0]; entered=threading.Event(); release=threading.Event()
        original=connection.commit
        def commit(): entered.set(); release.wait(.5); original()
        connection.commit=commit
        store=PostgresStore(pool=pool)
        async def operation():
            async with store.async_request_unit_of_work(correlation_id='commit',command_id='commit'): pass
        task=asyncio.create_task(operation())
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait,.2))
            task.cancel(); await asyncio.sleep(.01)
            self.assertFalse(task.done())
        finally: release.set()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(connection.commits,1); self.assertEqual(connection.rollbacks,0)
        self.assertEqual(len(pool.available),1); self.assertEqual(store.uow_metrics()['active'],0)

    async def test_anyio_level_cancellation_preserves_connection_cleanup(self):
        import anyio
        pool=BoundedPool(1); store=PostgresStore(pool=pool)
        async def operation():
            async with store.async_request_unit_of_work(correlation_id='scope',command_id='scope'):
                await anyio.sleep_forever()
        with anyio.move_on_after(.03) as scope:
            await operation()
        self.assertTrue(scope.cancel_called)
        self.assertEqual(len(pool.available),1); self.assertEqual(pool.available[0].rollbacks,1)
        self.assertEqual(store.uow_metrics()['active'],0)

class MetricQueueTests(unittest.TestCase):
    def test_full_or_blocked_sink_never_waits_and_context_is_detached(self):
        from app.observability.metric_dispatch import OperationMetricDispatcher
        from contextvars import ContextVar
        context=ContextVar('request_transaction',default=None)
        release=threading.Event(); entered=threading.Event(); seen=[]
        class Recorder:
            def record_attempt(self, **fields):
                seen.append(context.get()); entered.set(); release.wait(.5); return {'sinkOutcome':'failed'}
        queue=OperationMetricDispatcher(capacity=2); token=context.set('borrowed-request-transaction')
        try:
            self.assertTrue(queue.submit(Recorder()))
            self.assertTrue(entered.wait(.2))
            self.assertTrue(queue.submit(Recorder())); self.assertTrue(queue.submit(Recorder()))
            started=time.monotonic(); self.assertFalse(queue.submit(Recorder()))
            self.assertLess(time.monotonic()-started,.05)
            self.assertFalse(queue.close(timeout=.01))
            self.assertFalse(queue.submit(Recorder()))
        finally: context.reset(token); release.set(); self.assertTrue(queue.wait_for_idle(1))
        self.assertEqual(seen,[None])
        summary=queue.snapshot(); self.assertEqual(summary['completed'],1); self.assertEqual(summary['failed'],1)
        self.assertEqual(summary['dropped'],4); self.assertEqual(summary['queued'],0)

if __name__ == "__main__":
    unittest.main()
