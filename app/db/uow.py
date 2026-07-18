from __future__ import annotations

from threading import Lock
from typing import Any, Dict, Optional

from app.db.pool import ConnectionPoolExhausted


class UnitOfWorkMetrics:
    def __init__(self) -> None:
        self._lock = Lock()
        self._active = 0
        self._checkouts = 0
        self._committed = 0
        self._rolled_back = 0
        self._failed = 0
        self._pool_exhausted = 0
        self._return_failures = 0

    def checkout(self) -> None:
        with self._lock:
            self._active += 1
            self._checkouts += 1

    def release(self) -> None:
        with self._lock:
            self._active = max(0, self._active - 1)

    def committed(self) -> None:
        with self._lock:
            self._committed += 1

    def rolled_back(self) -> None:
        with self._lock:
            self._rolled_back += 1

    def failed(self) -> None:
        with self._lock:
            self._failed += 1

    def pool_exhausted(self) -> None:
        with self._lock:
            self._pool_exhausted += 1

    def return_failed(self) -> None:
        with self._lock:
            self._return_failures += 1

    def snapshot(self) -> Dict[str, int]:
        with self._lock:
            return {
                "schemaVersion": 1,
                "active": self._active,
                "checkouts": self._checkouts,
                "committed": self._committed,
                "rolledBack": self._rolled_back,
                "failed": self._failed,
                "poolExhausted": self._pool_exhausted,
                "connectionReturnFailures": self._return_failures,
            }


class DatabaseUnitOfWork:
    def __init__(
        self,
        pool: Any,
        metrics: UnitOfWorkMetrics,
        *,
        correlation_id: str,
        command_id: str,
        checkout_timeout_seconds: Optional[float] = None,
    ) -> None:
        self.pool = pool
        self.metrics = metrics
        self.correlation_id = correlation_id
        self.command_id = command_id
        self.checkout_timeout_seconds = checkout_timeout_seconds
        self.connection = None
        self.rollback_reason: Optional[str] = None

    def __enter__(self) -> "DatabaseUnitOfWork":
        try:
            self.connection = self.pool.getconn(
                timeout=self.checkout_timeout_seconds,
            )
        except ConnectionPoolExhausted:
            self.metrics.pool_exhausted()
            raise
        self.metrics.checkout()
        try:
            # Establish the root transaction before repository code can open a
            # nested psycopg transaction. Nested ``connection.transaction()``
            # blocks then become savepoints instead of independently committing
            # an aggregate before the request/job UoW has finished.
            with self.connection.cursor() as cursor:
                cursor.execute("BEGIN")
        except Exception:
            self.metrics.failed()
            try:
                self.connection.rollback()
                self.metrics.rolled_back()
            finally:
                try:
                    self.pool.putconn(self.connection)
                except Exception:
                    self.metrics.return_failed()
                    raise
                finally:
                    self.metrics.release()
                    self.connection = None
            raise
        return self

    def mark_rollback(self, reason: str) -> None:
        self.rollback_reason = reason or "rollbackOnly"

    def __exit__(self, exc_type, exc, traceback) -> bool:
        connection = self.connection
        if connection is None:
            return False
        should_rollback = exc_type is not None or self.rollback_reason is not None
        try:
            if should_rollback:
                if exc_type is not None:
                    self.metrics.failed()
                connection.rollback()
                self.metrics.rolled_back()
            else:
                try:
                    connection.commit()
                    self.metrics.committed()
                except Exception:
                    self.metrics.failed()
                    connection.rollback()
                    self.metrics.rolled_back()
                    raise
        finally:
            try:
                self.pool.putconn(connection)
            except Exception:
                self.metrics.return_failed()
                raise
            finally:
                self.metrics.release()
                self.connection = None
        return False
