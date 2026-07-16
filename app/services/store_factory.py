from datetime import datetime, timezone

from app.core.config import Settings
from app.services.in_memory_store import InMemoryStore
from app.services.postgres_store import PostgresStore


def make_store(settings: Settings):
    if settings.store_backend == "memory":
        return InMemoryStore()
    if settings.store_backend == "postgres":
        return PostgresStore(
            dsn=settings.database_url,
            pool_min_size=max(1, settings.database_pool_min_size),
            pool_max_size=max(1, settings.database_pool_max_size),
            pool_timeout_seconds=max(0.1, settings.database_pool_timeout_seconds),
        )
    raise ValueError(f"Unsupported STORE_BACKEND: {settings.store_backend}")


def open_store(store, *, wait: bool = True) -> None:
    open_pool = getattr(store, "open_pool", None)
    if callable(open_pool):
        open_pool(wait=wait)


def close_store(store) -> None:
    close_pool = getattr(store, "close_pool", None)
    if callable(close_pool):
        close_pool()


def init_store(store) -> None:
    open_store(store, wait=True)
    try:
        drain_expired_sessions = getattr(store, "drain_expired_digital_human_session_leases", None)
        if callable(drain_expired_sessions):
            drain_expired_sessions(now_iso=datetime.now(timezone.utc).isoformat())
    except Exception:
        close_store(store)
        raise
