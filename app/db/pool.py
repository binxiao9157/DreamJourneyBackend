from __future__ import annotations

from threading import Lock
from typing import Any, Callable, Dict, Optional


class ConnectionPoolExhausted(RuntimeError):
    pass


class PsycopgConnectionPool:
    def __init__(
        self,
        dsn: str,
        *,
        min_size: int = 1,
        max_size: int = 10,
        timeout_seconds: float = 5.0,
    ) -> None:
        try:
            from psycopg_pool import ConnectionPool
        except ImportError as exc:
            raise RuntimeError(
                "psycopg_pool is not installed. Run pip install -r requirements.txt."
            ) from exc
        self._pool = ConnectionPool(
            conninfo=dsn,
            min_size=max(1, min_size),
            max_size=max(max(1, min_size), max_size),
            timeout=max(0.1, timeout_seconds),
            open=False,
            check=ConnectionPool.check_connection,
            name="dreamjourney-api",
        )

    def open(self, *, wait: bool = True) -> None:
        self._pool.open(wait=wait)

    def close(self) -> None:
        self._pool.close()

    def getconn(self, *, timeout: Optional[float] = None):
        try:
            return self._pool.getconn(timeout=timeout)
        except Exception as exc:
            try:
                from psycopg_pool import PoolTimeout
            except ImportError:  # pragma: no cover - guarded by constructor
                PoolTimeout = ()  # type: ignore[assignment]
            if isinstance(exc, PoolTimeout):
                raise ConnectionPoolExhausted("database connection pool exhausted") from exc
            raise

    def putconn(self, connection: Any) -> None:
        self._pool.putconn(connection)

    def stats(self) -> Dict[str, int]:
        stats = self._pool.get_stats()
        return {
            "poolSize": int(stats.get("pool_size") or 0),
            "poolAvailable": int(stats.get("pool_available") or 0),
            "requestsWaiting": int(stats.get("requests_waiting") or 0),
            "requestsNum": int(stats.get("requests_num") or 0),
            "requestsQueued": int(stats.get("requests_queued") or 0),
            "requestsErrors": int(stats.get("requests_errors") or 0),
            "requestsWaitMs": int(stats.get("requests_wait_ms") or 0),
        }


class FactoryConnectionPool:
    """Test/compatibility adapter. Every checkout calls the factory."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory
        self._lock = Lock()
        self._checkouts = 0
        self._returns = 0

    def open(self, *, wait: bool = True) -> None:
        _ = wait

    def close(self) -> None:
        return None

    def getconn(self, *, timeout: Optional[float] = None):
        _ = timeout
        connection = self._connection_factory()
        with self._lock:
            self._checkouts += 1
        return connection

    def putconn(self, connection: Any) -> None:
        close = getattr(connection, "close", None)
        if callable(close):
            close()
        with self._lock:
            self._returns += 1

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "poolSize": 0,
                "poolAvailable": 0,
                "requestsWaiting": 0,
                "requestsNum": self._checkouts,
                "requestsQueued": 0,
                "requestsErrors": 0,
                "requestsWaitMs": 0,
                "connectionsReturned": self._returns,
            }
