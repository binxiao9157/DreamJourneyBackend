from datetime import datetime, timezone

from app.core.config import Settings
from app.services.in_memory_store import InMemoryStore
from app.services.postgres_store import PostgresStore


def make_store(settings: Settings):
    if settings.store_backend == "memory":
        return InMemoryStore()
    if settings.store_backend == "postgres":
        return PostgresStore(dsn=settings.database_url)
    raise ValueError(f"Unsupported STORE_BACKEND: {settings.store_backend}")


def init_store(store) -> None:
    init_schema = getattr(store, "init_schema", None)
    if callable(init_schema):
        init_schema()
    drain_expired_sessions = getattr(store, "drain_expired_digital_human_session_leases", None)
    if callable(drain_expired_sessions):
        drain_expired_sessions(now_iso=datetime.now(timezone.utc).isoformat())
