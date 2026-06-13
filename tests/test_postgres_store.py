import unittest

from app.services.postgres_store import PostgresStore


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.result = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        self.connection.executed.append((normalized, params))
        params = params or ()

        if normalized.startswith("SELECT graph FROM kb_snapshots"):
            user_id = params[0]
            value = self.connection.kb_snapshots.get(user_id)
            self.result = None if value is None else {"graph": value}
        elif normalized.startswith("SELECT payload FROM memories"):
            user_id = params[0]
            self.result = [{"payload": item} for item in self.connection.memories.get(user_id, [])]
        elif normalized.startswith("SELECT payload FROM family_members"):
            user_id = params[0]
            self.result = [{"payload": item} for item in self.connection.family_members.get(user_id, [])]
        elif normalized.startswith("INSERT INTO users"):
            user_id, phone, nickname, payload = params
            self.connection.users[user_id] = dict(payload)
            self.result = {"payload": payload}
        elif normalized.startswith("INSERT INTO kb_snapshots"):
            user_id, graph = params
            self.connection.kb_snapshots[user_id] = dict(graph)
            self.result = {"graph": graph}
        elif normalized.startswith("INSERT INTO memories"):
            user_id, item_id, payload = params
            self.connection.memories.setdefault(user_id, []).insert(0, dict(payload))
            self.result = {"payload": payload}
        elif normalized.startswith("INSERT INTO archive_items"):
            user_id, item_id, payload = params
            self.connection.archive_items.setdefault(user_id, []).insert(0, dict(payload))
            self.result = {"payload": payload}
        elif normalized.startswith("INSERT INTO family_members"):
            user_id, item_id, payload = params
            self.connection.family_members.setdefault(user_id, []).append(dict(payload))
            self.result = {"payload": payload}
        else:
            self.result = None

    def fetchone(self):
        return self.result

    def fetchall(self):
        return self.result or []


class FakeConnection:
    def __init__(self):
        self.executed = []
        self.commits = 0
        self.users = {}
        self.kb_snapshots = {}
        self.memories = {}
        self.archive_items = {}
        self.family_members = {}

    def cursor(self, row_factory=None):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1


class PostgresStoreTests(unittest.TestCase):
    def test_init_schema_creates_required_tables(self):
        connection = FakeConnection()
        store = PostgresStore(connection_factory=lambda: connection)

        store.init_schema()

        sql = "\n".join(statement for statement, _ in connection.executed)
        self.assertIn("CREATE TABLE IF NOT EXISTS users", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS kb_snapshots", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS memories", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS archive_items", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS family_members", sql)
        self.assertGreaterEqual(connection.commits, 1)

    def test_store_persists_kb_snapshot_by_user(self):
        connection = FakeConnection()
        store = PostgresStore(connection_factory=lambda: connection)

        store.save_kb_snapshot("u1", {"people": [{"id": "p1"}]})
        store.save_kb_snapshot("u2", {"people": [{"id": "p2"}]})

        self.assertEqual(store.get_kb_snapshot("u1")["people"][0]["id"], "p1")
        self.assertEqual(store.get_kb_snapshot("u2")["people"][0]["id"], "p2")

    def test_store_persists_memories_and_family_members(self):
        connection = FakeConnection()
        store = PostgresStore(connection_factory=lambda: connection)

        memory = store.add_memory("u1", {"title": "绍兴记忆"})
        archive = store.add_archive_item("u1", {"title": "老照片"})
        member = store.add_family_member("u1", {"name": "林桂芳"})

        self.assertTrue(memory["id"].startswith("memory_"))
        self.assertTrue(archive["id"].startswith("archive_"))
        self.assertTrue(member["id"].startswith("family_"))
        self.assertEqual(store.list_memories("u1")[0]["title"], "绍兴记忆")
        self.assertEqual(store.list_family_members("u1")[0]["name"], "林桂芳")


if __name__ == "__main__":
    unittest.main()
