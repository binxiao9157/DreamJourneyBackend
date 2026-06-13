from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
import uuid


class PostgresStore:
    def __init__(self, dsn: str = None, connection_factory: Callable[[], Any] = None):
        self.dsn = dsn
        self._connection_factory = connection_factory
        self._connection = None

    def init_schema(self) -> None:
        statements = [
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                phone TEXT NOT NULL,
                nickname TEXT NOT NULL,
                payload JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS kb_snapshots (
                user_id TEXT PRIMARY KEY,
                graph JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                payload JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_memories_user_created
                ON memories(user_id, created_at DESC)
            """,
            """
            CREATE TABLE IF NOT EXISTS archive_items (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                payload JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_archive_items_user_created
                ON archive_items(user_id, created_at DESC)
            """,
            """
            CREATE TABLE IF NOT EXISTS family_members (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                payload JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_family_members_user_created
                ON family_members(user_id, created_at ASC)
            """,
        ]
        connection = self._connect()
        with connection.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)
        connection.commit()

    def upsert_user(self, phone: str, nickname: str) -> Dict[str, Any]:
        user_id = f"user_{phone[-4:]}" if phone else f"user_{uuid.uuid4().hex[:8]}"
        user = {
            "id": user_id,
            "phone": phone,
            "nickname": nickname or "寻梦环游用户",
            "updatedAt": self._now(),
        }
        row = self._fetchone(
            """
            INSERT INTO users (id, phone, nickname, payload, updated_at)
            VALUES (%s, %s, %s, %s, NOW())
            ON CONFLICT (id) DO UPDATE SET
                phone = EXCLUDED.phone,
                nickname = EXCLUDED.nickname,
                payload = EXCLUDED.payload,
                updated_at = NOW()
            RETURNING payload
            """,
            (user_id, phone, user["nickname"], user),
            commit=True,
        )
        return deepcopy(row["payload"])

    def save_kb_snapshot(self, user_id: str, graph: Dict[str, Any]) -> Dict[str, Any]:
        row = self._fetchone(
            """
            INSERT INTO kb_snapshots (user_id, graph, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (user_id) DO UPDATE SET
                graph = EXCLUDED.graph,
                updated_at = NOW()
            RETURNING graph
            """,
            (user_id, graph),
            commit=True,
        )
        return {
            "userId": user_id,
            "graph": deepcopy(row["graph"]),
            "updatedAt": self._now(),
        }

    def get_kb_snapshot(self, user_id: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone(
            "SELECT graph FROM kb_snapshots WHERE user_id = %s",
            (user_id,),
        )
        return None if row is None else deepcopy(row["graph"])

    def add_memory(self, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        item = self._with_identity(payload, "memory", user_id)
        return self._insert_payload("memories", user_id, item)

    def list_memories(self, user_id: str) -> List[Dict[str, Any]]:
        return self._list_payloads("memories", user_id)

    def add_archive_item(self, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        item = self._with_identity(payload, "archive", user_id)
        return self._insert_payload("archive_items", user_id, item)

    def add_family_member(self, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        item = self._with_identity(payload, "family", user_id)
        return self._insert_payload("family_members", user_id, item)

    def list_family_members(self, user_id: str) -> List[Dict[str, Any]]:
        return self._list_payloads("family_members", user_id)

    def _insert_payload(self, table: str, user_id: str, item: Dict[str, Any]) -> Dict[str, Any]:
        row = self._fetchone(
            f"""
            INSERT INTO {table} (user_id, id, payload, created_at)
            VALUES (%s, %s, %s, NOW())
            RETURNING payload
            """,
            (user_id, item["id"], item),
            commit=True,
        )
        return deepcopy(row["payload"])

    def _list_payloads(self, table: str, user_id: str) -> List[Dict[str, Any]]:
        rows = self._fetchall(
            f"""
            SELECT payload FROM {table}
            WHERE user_id = %s
            ORDER BY created_at DESC
            """,
            (user_id,),
        )
        return [deepcopy(row["payload"]) for row in rows]

    def _fetchone(self, sql: str, params: tuple = (), commit: bool = False) -> Optional[Dict[str, Any]]:
        connection = self._connect()
        with connection.cursor(row_factory=self._dict_row_factory()) as cursor:
            cursor.execute(sql, params)
            row = cursor.fetchone()
        if commit:
            connection.commit()
        return row

    def _fetchall(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        connection = self._connect()
        with connection.cursor(row_factory=self._dict_row_factory()) as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        return rows

    def _connect(self):
        if self._connection is not None:
            return self._connection
        if self._connection_factory is not None:
            self._connection = self._connection_factory()
            return self._connection
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("psycopg is not installed. Run `pip install -r requirements.txt`.") from exc
        self._connection = psycopg.connect(self.dsn)
        return self._connection

    @staticmethod
    def _dict_row_factory():
        try:
            from psycopg.rows import dict_row
            return dict_row
        except ImportError:
            return None

    @staticmethod
    def _with_identity(payload: Dict[str, Any], prefix: str, user_id: str) -> Dict[str, Any]:
        item = deepcopy(payload)
        item.setdefault("id", f"{prefix}_{uuid.uuid4().hex}")
        item["userId"] = user_id
        item.setdefault("createdAt", PostgresStore._now())
        return item

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
