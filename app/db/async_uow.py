"""Keep synchronous PostgreSQL checkout/finalization off the ASGI event loop.

Binding the request ContextVar stays on the request task. Enter and exit have
separate executors: waiters for a full pool must never occupy finalizer slots.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from contextvars import copy_context
from functools import partial
import sys
import time
from weakref import WeakKeyDictionary

import anyio

from app.db.pool import ConnectionPoolExhausted
from app.db.uow import DatabaseUnitOfWork

_ENTER = ThreadPoolExecutor(max_workers=16, thread_name_prefix="db-checkout")
_EXIT = ThreadPoolExecutor(max_workers=4, thread_name_prefix="db-finalize")

async def _complete_io(executor, function):
    """Do not abandon a connection on cancellation, including Task.cancel()."""
    with anyio.CancelScope(shield=True):
        future = asyncio.get_running_loop().run_in_executor(executor, copy_context().run, function)
        cancelled = None
        while True:
            try:
                return await asyncio.shield(future), cancelled
            except asyncio.CancelledError as exc:
                cancelled = exc
                if future.cancelled():
                    raise

class AsyncRequestUnitOfWork:
    def __init__(self, pool, metrics, current, *, timeout, concurrency):
        self.pool, self.metrics, self.current = pool, metrics, current
        self.timeout = timeout
        self.concurrency = min(16, max(1, concurrency))
        self.limiters = WeakKeyDictionary()

    @asynccontextmanager
    async def open(self, *, correlation_id, command_id):
        existing = self.current.get()
        if existing is not None:
            try:
                yield existing
            except BaseException:
                existing.mark_rollback("nestedWorkUnitFailure")
                raise
            return
        loop = asyncio.get_running_loop()
        limiter = self.limiters.setdefault(loop, asyncio.Semaphore(self.concurrency))
        deadline = time.monotonic() + self.timeout
        try:
            await asyncio.wait_for(limiter.acquire(), timeout=self.timeout)
        except asyncio.TimeoutError as exc:
            self.metrics.pool_exhausted()
            raise ConnectionPoolExhausted("database connection pool exhausted") from exc
        candidate = DatabaseUnitOfWork(self.pool, self.metrics, correlation_id=correlation_id,
                                       command_id=command_id, checkout_timeout_seconds=self.timeout)
        def enter():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.metrics.pool_exhausted()
                raise ConnectionPoolExhausted("database connection pool exhausted")
            candidate.checkout_timeout_seconds = remaining
            return candidate.__enter__()
        try:
            active, cancelled = await _complete_io(_ENTER, enter)
        finally:
            limiter.release()
        if cancelled is not None:
            await _complete_io(_EXIT, partial(candidate.__exit__, type(cancelled), cancelled, None))
            raise cancelled
        # A shielded checkout may complete after an AnyIO cancel scope fired.
        # Observe that pending cancellation before exposing the transaction.
        try:
            await anyio.lowlevel.checkpoint_if_cancelled()
        except BaseException:
            await _complete_io(_EXIT, partial(candidate.__exit__, *sys.exc_info()))
            raise
        token = self.current.set(active)
        error = (None, None, None)
        try:
            yield active
        except BaseException:
            error = sys.exc_info()
            raise
        finally:
            self.current.reset(token)
            _, cancelled = await _complete_io(_EXIT, partial(candidate.__exit__, *error))
            if cancelled is not None and error[0] is None:
                raise cancelled
