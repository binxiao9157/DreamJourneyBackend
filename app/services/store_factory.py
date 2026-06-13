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
