import unittest

from psycopg.types.json import Jsonb

from app.services.postgres_store import PostgresStore


def unwrap_jsonb(value):
    return value.obj if isinstance(value, Jsonb) else value


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
        elif normalized.startswith("SELECT payload FROM archive_items"):
            user_id = params[0]
            self.result = [{"payload": item} for item in self.connection.archive_items.get(user_id, [])]
        elif normalized.startswith("SELECT payload FROM mailbox_letters"):
            user_id = params[0]
            self.result = [{"payload": item} for item in self.connection.mailbox_letters.get(user_id, [])]
        elif normalized.startswith("SELECT payload FROM echo_delayed_replies"):
            user_id = params[0]
            self.result = [{"payload": item} for item in self.connection.echo_delayed_replies.get(user_id, [])]
        elif normalized.startswith("SELECT payload FROM profiles"):
            user_id = params[0]
            profile = self.connection.profiles.get(user_id)
            self.result = None if profile is None else {"payload": profile}
        elif normalized.startswith("SELECT payload FROM family_members WHERE user_id = %s AND id = %s"):
            user_id, item_id = params
            members = [
                item for item in self.connection.family_members.get(user_id, [])
                if item.get("id") == item_id
            ]
            self.result = None if not members else {"payload": members[0]}
        elif normalized.startswith("SELECT payload FROM family_members WHERE payload->>'invitationCode'"):
            invitation_code = params[0]
            matches = [
                item for members in self.connection.family_members.values()
                for item in members
                if item.get("invitationCode") == invitation_code
            ]
            self.result = None if not matches else {"payload": matches[0]}
        elif normalized.startswith("SELECT payload FROM family_members"):
            user_id = params[0]
            self.result = [{"payload": item} for item in self.connection.family_members.get(user_id, [])]
        elif normalized.startswith("SELECT payload FROM care_snapshots"):
            if "viewer_family_member_id = %s" in normalized:
                user_id, viewer_family_member_id = params
                snapshots = [
                    item for item in self.connection.care_snapshots.get(user_id, [])
                    if item.get("viewerFamilyMemberID") == viewer_family_member_id
                ]
            else:
                user_id = params[0]
                snapshots = [
                    item for item in self.connection.care_snapshots.get(user_id, [])
                    if item.get("viewerFamilyMemberID") is None
                ]
            self.result = {"payload": snapshots[0]} if snapshots else None
        elif normalized.startswith("INSERT INTO users"):
            user_id, phone, nickname, payload = params
            payload = unwrap_jsonb(payload)
            self.connection.users[user_id] = dict(payload)
            self.result = {"payload": payload}
        elif normalized.startswith("INSERT INTO kb_snapshots"):
            user_id, graph = params
            graph = unwrap_jsonb(graph)
            self.connection.kb_snapshots[user_id] = dict(graph)
            self.result = {"graph": graph}
        elif normalized.startswith("INSERT INTO memories"):
            user_id, item_id, payload = params
            payload = unwrap_jsonb(payload)
            self.connection.memories.setdefault(user_id, []).insert(0, dict(payload))
            self.result = {"payload": payload}
        elif normalized.startswith("INSERT INTO archive_items"):
            user_id, item_id, payload = params
            payload = unwrap_jsonb(payload)
            self.connection.archive_items.setdefault(user_id, []).insert(0, dict(payload))
            self.result = {"payload": payload}
        elif normalized.startswith("INSERT INTO mailbox_letters"):
            user_id, item_id, payload = params
            payload = unwrap_jsonb(payload)
            letters = self.connection.mailbox_letters.setdefault(user_id, [])
            letters[:] = [item for item in letters if item.get("id") != item_id]
            letters.insert(0, dict(payload))
            self.result = {"payload": payload}
        elif normalized.startswith("INSERT INTO echo_delayed_replies"):
            user_id, item_id, payload = params
            payload = unwrap_jsonb(payload)
            replies = self.connection.echo_delayed_replies.setdefault(user_id, [])
            replies[:] = [item for item in replies if item.get("id") != item_id]
            replies.insert(0, dict(payload))
            self.result = {"payload": payload}
        elif normalized.startswith("INSERT INTO profiles"):
            user_id, payload = params
            payload = unwrap_jsonb(payload)
            self.connection.profiles[user_id] = dict(payload)
            self.result = {"payload": payload}
        elif normalized.startswith("INSERT INTO family_members"):
            user_id, item_id, payload = params
            payload = unwrap_jsonb(payload)
            self.connection.family_members.setdefault(user_id, []).append(dict(payload))
            self.result = {"payload": payload}
        elif normalized.startswith("UPDATE family_members"):
            payload, user_id, item_id = params
            payload = unwrap_jsonb(payload)
            members = self.connection.family_members.get(user_id, [])
            for index, item in enumerate(members):
                if item.get("id") == item_id:
                    members[index] = dict(payload)
                    self.result = {"payload": payload}
                    break
            else:
                self.result = None
        elif normalized.startswith("INSERT INTO care_snapshots"):
            user_id, item_id, viewer_family_member_id, payload = params
            payload = unwrap_jsonb(payload)
            self.connection.care_snapshots.setdefault(user_id, []).insert(0, dict(payload))
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
        self.mailbox_letters = {}
        self.echo_delayed_replies = {}
        self.profiles = {}
        self.family_members = {}
        self.care_snapshots = {}

    def cursor(self, row_factory=None):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1


class FailingCursor:
    def __init__(self, error):
        self.error = error

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        raise self.error

    def fetchone(self):
        return None

    def fetchall(self):
        return []


class FailingConnection:
    def __init__(self, error):
        self.error = error
        self.rollbacks = 0

    def cursor(self, row_factory=None):
        return FailingCursor(self.error)

    def rollback(self):
        self.rollbacks += 1


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
        self.assertIn("CREATE TABLE IF NOT EXISTS mailbox_letters", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS echo_delayed_replies", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS profiles", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS family_members", sql)
        self.assertIn("idx_family_members_invitation_code", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS care_snapshots", sql)
        self.assertGreaterEqual(connection.commits, 1)

    def test_store_persists_kb_snapshot_by_user(self):
        connection = FakeConnection()
        store = PostgresStore(connection_factory=lambda: connection)

        store.save_kb_snapshot("u1", {"people": [{"id": "p1"}]})
        store.save_kb_snapshot("u2", {"people": [{"id": "p2"}]})

        self.assertEqual(store.get_kb_snapshot("u1")["people"][0]["id"], "p1")
        self.assertEqual(store.get_kb_snapshot("u2")["people"][0]["id"], "p2")

    def test_kb_snapshot_survives_store_recreation(self):
        connection = FakeConnection()
        writer = PostgresStore(connection_factory=lambda: connection)

        writer.save_kb_snapshot(
            "u1",
            {
                "people": [{"id": "p1", "name": "陈建国"}],
                "places": [{"id": "place_shaoxing", "name": "绍兴"}],
                "facts": [{"id": "fact_1", "statement": "1968 年住在绍兴越城区"}],
            },
        )

        reader_after_restart = PostgresStore(connection_factory=lambda: connection)
        snapshot = reader_after_restart.get_kb_snapshot("u1")

        self.assertEqual(snapshot["people"][0]["name"], "陈建国")
        self.assertEqual(snapshot["places"][0]["name"], "绍兴")
        self.assertEqual(snapshot["facts"][0]["statement"], "1968 年住在绍兴越城区")

    def test_upsert_user_uses_stable_full_phone_hash(self):
        connection = FakeConnection()
        store = PostgresStore(connection_factory=lambda: connection)

        first = store.upsert_user("19357579157", "陈建国")
        second = store.upsert_user("18300009157", "林桂芳")

        self.assertEqual(first["id"], "user_aef88d2439c15d38")
        self.assertNotEqual(first["id"], "user_9157")
        self.assertNotEqual(first["id"], second["id"])
        self.assertIn("user_aef88d2439c15d38", connection.users)

    def test_store_rolls_back_failed_payload_insert(self):
        connection = FailingConnection(RuntimeError("duplicate key"))
        store = PostgresStore(connection_factory=lambda: connection)

        with self.assertRaises(RuntimeError):
            store.add_archive_item("u1", {"id": "archive_1", "title": "老照片"})

        self.assertEqual(connection.rollbacks, 1)

    def test_store_persists_memories_and_family_members(self):
        connection = FakeConnection()
        store = PostgresStore(connection_factory=lambda: connection)

        memory = store.add_memory("u1", {"title": "绍兴记忆"})
        archive = store.add_archive_item(
            "u1",
            {
                "title": "老照片",
                "personaScope": "family",
                "digitalHumanId": "family_default",
            },
        )
        mailbox = store.add_mailbox_letter("u1", {"id": "letter_1", "title": "想说的话"})
        member = store.add_family_member("u1", {"name": "林桂芳"})

        self.assertTrue(memory["id"].startswith("memory_"))
        self.assertTrue(archive["id"].startswith("archive_"))
        self.assertEqual(archive["personaScope"], "family")
        self.assertEqual(archive["digitalHumanId"], "family_default")
        self.assertEqual(mailbox["id"], "letter_1")
        self.assertTrue(member["id"].startswith("family_"))
        self.assertEqual(store.list_memories("u1")[0]["title"], "绍兴记忆")
        self.assertEqual(store.list_archive_items("u1")[0]["personaScope"], "family")
        self.assertEqual(store.list_archive_items("u1")[0]["digitalHumanId"], "family_default")
        self.assertEqual(store.list_mailbox_letters("u1")[0]["title"], "想说的话")
        self.assertEqual(store.list_family_members("u1")[0]["name"], "林桂芳")

    def test_store_persists_family_member_revocation(self):
        connection = FakeConnection()
        store = PostgresStore(connection_factory=lambda: connection)

        member = store.add_family_member("u1", {"name": "林桂芳"})
        revoked = store.revoke_family_member("u1", member["id"])

        self.assertEqual(revoked["accessStatus"], "revoked")
        self.assertEqual(revoked["invitationStatus"], "revoked")
        self.assertFalse(revoked["isOnline"])
        self.assertEqual(store.list_family_members("u1")[0]["accessStatus"], "revoked")

    def test_store_persists_echo_delayed_replies_by_user(self):
        connection = FakeConnection()
        store = PostgresStore(connection_factory=lambda: connection)

        first = store.add_echo_delayed_reply(
            "u1",
            {
                "id": "reply_1",
                "delayedReplyId": "reply_1",
                "deliverAt": "2026-06-18T12:05:00Z",
                "minutes": 7,
                "trigger": "tenRoundBaseline",
            },
        )
        store.add_echo_delayed_reply(
            "u2",
            {
                "id": "reply_2",
                "delayedReplyId": "reply_2",
                "deliverAt": "2026-06-18T12:06:00Z",
                "minutes": 8,
                "trigger": "contentSignal",
            },
        )

        self.assertEqual(first["id"], "reply_1")
        self.assertEqual(first["userId"], "u1")
        self.assertEqual(store.list_echo_delayed_replies("u1")[0]["delayedReplyId"], "reply_1")
        self.assertEqual(store.list_echo_delayed_replies("u2")[0]["trigger"], "contentSignal")

    def test_store_persists_profile_metadata_by_user(self):
        connection = FakeConnection()
        store = PostgresStore(connection_factory=lambda: connection)

        store.save_profile(
            "profile_user_1",
            {
                "nickname": "陈建国",
                "gender": "男",
                "region": "绍兴",
                "avatarName": "person.crop.circle.fill",
            },
        )
        store.save_profile(
            "profile_user_2",
            {
                "nickname": "林桂芳",
                "gender": "女",
                "region": "上海",
                "avatarName": "person.circle.fill",
            },
        )
        updated = store.save_profile(
            "profile_user_1",
            {
                "nickname": "陈伯伯",
                "gender": "不便透露",
                "region": "杭州",
                "avatarName": "person.crop.circle",
            },
        )

        self.assertEqual(updated["userId"], "profile_user_1")
        self.assertEqual(updated["nickname"], "陈伯伯")
        self.assertEqual(updated["gender"], "不便透露")
        self.assertEqual(updated["region"], "杭州")
        self.assertEqual(updated["avatarName"], "person.crop.circle")
        self.assertEqual(store.get_profile("profile_user_1")["nickname"], "陈伯伯")
        self.assertEqual(store.get_profile("profile_user_2")["nickname"], "林桂芳")
        self.assertIsNone(store.get_profile("missing_user"))

    def test_store_persists_family_member_acceptance(self):
        connection = FakeConnection()
        store = PostgresStore(connection_factory=lambda: connection)

        member = store.add_family_member("u1", {"name": "林桂芳", "phone": "13900001111"})
        accepted = store.accept_family_member("u1", member["id"], phone="13900001111")

        self.assertEqual(accepted["accessStatus"], "active")
        self.assertEqual(accepted["invitationStatus"], "accepted")
        self.assertTrue(accepted["isOnline"])
        self.assertEqual(store.list_family_members("u1")[0]["invitationStatus"], "accepted")

    def test_store_accepts_family_invitation_code(self):
        connection = FakeConnection()
        store = PostgresStore(connection_factory=lambda: connection)

        member = store.add_family_member(
            "u1",
            {
                "id": "family_code_1",
                "name": "林桂芳",
                "phone": "13900001111",
                "invitationCode": "ABCD1234",
                "invitationURL": "dreamjourney://family/invite?code=ABCD1234",
            },
        )
        accepted = store.accept_family_invitation_code("ABCD1234", phone="13900001111")

        self.assertEqual(member["invitationCode"], "ABCD1234")
        self.assertEqual(accepted["ownerUserId"], "u1")
        self.assertEqual(accepted["accessStatus"], "active")
        self.assertEqual(accepted["invitationStatus"], "accepted")
        self.assertIsNone(store.accept_family_invitation_code("ABCD1234", phone="13900002222"))

    def test_store_persists_latest_care_snapshot_by_viewer(self):
        connection = FakeConnection()
        store = PostgresStore(connection_factory=lambda: connection)

        store.save_care_snapshot(
            "u1",
            {"riskLevel": "stable", "summary": "全家视角"},
            viewer_family_member_id=None,
        )
        store.save_care_snapshot(
            "u1",
            {"riskLevel": "watch", "summary": "女儿视角"},
            viewer_family_member_id="fm_daughter",
        )

        self.assertEqual(store.get_latest_care_snapshot("u1")["snapshot"]["summary"], "全家视角")
        self.assertEqual(
            store.get_latest_care_snapshot("u1", viewer_family_member_id="fm_daughter")["snapshot"]["summary"],
            "女儿视角",
        )


if __name__ == "__main__":
    unittest.main()
