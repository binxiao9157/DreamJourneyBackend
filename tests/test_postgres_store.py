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
        elif normalized.startswith("SELECT user_id, id, payload FROM archive_items"):
            cutoff_iso, limit = params
            matches = [
                (user_id, item)
                for user_id, items in self.connection.archive_items.items()
                for item in items
                if item.get("kind") == "timeLetter"
                and (item.get("deliveryState") or (item.get("metadata") or {}).get("deliveryState")) == "sealed"
                and (
                    item.get("deliveryStatus")
                    or (item.get("metadata") or {}).get("deliveryStatus")
                    or (item.get("metadata") or {}).get("deliveryExecutionState")
                ) == "scheduled"
                and str(item.get("openAt") or (item.get("metadata") or {}).get("openAt") or "") <= cutoff_iso
            ]
            matches = sorted(
                matches,
                key=lambda match: str(match[1].get("openAt") or (match[1].get("metadata") or {}).get("openAt") or ""),
            )[:limit]
            self.result = [
                {"user_id": user_id, "id": item.get("id"), "payload": item}
                for user_id, item in matches
            ]
        elif normalized.startswith("SELECT payload FROM archive_items"):
            user_id = params[0]
            self.result = [{"payload": item} for item in self.connection.archive_items.get(user_id, [])]
        elif normalized.startswith("SELECT payload FROM mailbox_letters"):
            user_id = params[0]
            if len(params) > 1:
                item_id = params[1]
                self.result = next(
                    (
                        {"payload": item}
                        for item in self.connection.mailbox_letters.get(user_id, [])
                        if item.get("id") == item_id
                    ),
                    None,
                )
            else:
                self.result = [{"payload": item} for item in self.connection.mailbox_letters.get(user_id, [])]
        elif normalized.startswith("SELECT user_id, id, payload FROM echo_delayed_replies"):
            cutoff_iso, limit = params
            matches = [
                item for replies in self.connection.echo_delayed_replies.values()
                for item in replies
                if item.get("deliveryState") == "scheduled"
                and str(item.get("deliverAt") or "") <= cutoff_iso
            ]
            matches = sorted(matches, key=lambda item: str(item.get("deliverAt") or ""))[:limit]
            self.result = [
                {"user_id": item.get("userId"), "id": item.get("id"), "payload": item}
                for item in matches
            ]
        elif normalized.startswith("SELECT payload FROM echo_delayed_replies"):
            user_id = params[0]
            self.result = [{"payload": item} for item in self.connection.echo_delayed_replies.get(user_id, [])]
        elif normalized.startswith("SELECT payload FROM push_device_tokens"):
            user_id = params[0]
            self.result = [{"payload": item} for item in self.connection.push_device_tokens.get(user_id, [])]
        elif normalized.startswith("SELECT payload FROM voice_profiles WHERE user_id = %s AND id = %s"):
            user_id, voice_profile_id = params
            profiles = [
                item for item in self.connection.voice_profiles.get(user_id, [])
                if item.get("voiceProfileId") == voice_profile_id
            ]
            self.result = None if not profiles else {"payload": profiles[0]}
        elif normalized.startswith("SELECT payload FROM voice_profiles"):
            user_id = params[0]
            self.result = [{"payload": item} for item in self.connection.voice_profiles.get(user_id, [])]
        elif normalized.startswith("SELECT payload FROM profiles"):
            user_id = params[0]
            profile = self.connection.profiles.get(user_id)
            self.result = None if profile is None else {"payload": profile}
        elif normalized.startswith("SELECT payload FROM password_credentials"):
            user_id = params[0]
            credential = self.connection.password_credentials.get(user_id)
            self.result = None if credential is None else {"payload": credential}
        elif normalized.startswith("SELECT payload FROM users WHERE id = %s"):
            user_id = params[0]
            user = self.connection.users.get(user_id)
            self.result = None if user is None else {"payload": user}
        elif normalized.startswith("SELECT id, payload FROM users"):
            self.result = [
                {"id": user_id, "payload": user}
                for user_id, user in self.connection.users.items()
                if user.get("deletionState") == "softDeleted"
            ]
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
        elif normalized.startswith("UPDATE users"):
            if len(params) == 2:
                payload, user_id = params
            elif len(params) == 4:
                _, _, payload, user_id = params
            elif len(params) == 3:
                _, payload, user_id = params
            else:
                payload, user_id = params[-2], params[-1]
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
            items = self.connection.archive_items.setdefault(user_id, [])
            items[:] = [item for item in items if item.get("id") != item_id]
            items.insert(0, dict(payload))
            self.result = {"payload": payload}
        elif normalized.startswith("DELETE FROM archive_items"):
            user_id, item_id = params
            items = self.connection.archive_items.get(user_id, [])
            for index, item in enumerate(items):
                if item.get("id") == item_id:
                    self.result = {"payload": items.pop(index)}
                    break
            else:
                self.result = None
        elif normalized.startswith("UPDATE archive_items"):
            payload, user_id, item_id = params
            payload = unwrap_jsonb(payload)
            items = self.connection.archive_items.get(user_id, [])
            for index, item in enumerate(items):
                if item.get("id") == item_id:
                    items[index] = dict(payload)
                    self.result = {"payload": payload}
                    break
            else:
                self.result = None
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
        elif normalized.startswith("UPDATE echo_delayed_replies"):
            payload, user_id, item_id = params
            payload = unwrap_jsonb(payload)
            replies = self.connection.echo_delayed_replies.get(user_id, [])
            for index, item in enumerate(replies):
                if item.get("id") == item_id:
                    replies[index] = dict(payload)
                    self.result = {"payload": payload}
                    break
            else:
                self.result = None
        elif normalized.startswith("UPDATE mailbox_letters"):
            payload, user_id, item_id = params
            payload = unwrap_jsonb(payload)
            letters = self.connection.mailbox_letters.get(user_id, [])
            for index, item in enumerate(letters):
                if item.get("id") == item_id:
                    letters[index] = dict(payload)
                    self.result = {"payload": payload}
                    break
            else:
                self.result = None
        elif normalized.startswith("INSERT INTO push_device_tokens"):
            user_id, item_id, payload = params
            payload = unwrap_jsonb(payload)
            tokens = self.connection.push_device_tokens.setdefault(user_id, [])
            tokens[:] = [item for item in tokens if item.get("id") != item_id]
            tokens.insert(0, dict(payload))
            self.result = {"payload": payload}
        elif normalized.startswith("INSERT INTO voice_profiles"):
            user_id, item_id, payload = params
            payload = unwrap_jsonb(payload)
            profiles = self.connection.voice_profiles.setdefault(user_id, [])
            profiles[:] = [item for item in profiles if item.get("voiceProfileId") != item_id]
            profiles.insert(0, dict(payload))
            self.result = {"payload": payload}
        elif normalized.startswith("INSERT INTO profiles"):
            user_id, payload = params
            payload = unwrap_jsonb(payload)
            self.connection.profiles[user_id] = dict(payload)
            self.result = {"payload": payload}
        elif normalized.startswith("INSERT INTO password_credentials"):
            user_id, payload = params
            payload = unwrap_jsonb(payload)
            self.connection.password_credentials[user_id] = dict(payload)
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
        self.push_device_tokens = {}
        self.voice_profiles = {}
        self.profiles = {}
        self.password_credentials = {}
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
        self.assertIn("CREATE TABLE IF NOT EXISTS push_device_tokens", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS profiles", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS password_credentials", sql)
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

    def test_store_persists_password_credentials_by_user(self):
        connection = FakeConnection()
        store = PostgresStore(connection_factory=lambda: connection)

        saved = store.save_password_credential(
            "u1",
            {"algorithm": "pbkdf2_sha256", "salt": "salt", "hash": "hash", "iterations": 210000},
        )
        store.save_password_credential(
            "u2",
            {"algorithm": "pbkdf2_sha256", "salt": "other", "hash": "other-hash", "iterations": 210000},
        )
        loaded = store.get_password_credential("u1")

        self.assertEqual(saved["userId"], "u1")
        self.assertEqual(loaded["hash"], "hash")
        self.assertEqual(store.get_password_credential("u2")["hash"], "other-hash")
        self.assertIsNone(store.get_password_credential("missing"))

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

    def test_store_upserts_and_deletes_archive_items_by_user_and_id(self):
        connection = FakeConnection()
        store = PostgresStore(connection_factory=lambda: connection)

        store.add_archive_item("u1", {"id": "time-letter-1", "kind": "timeLetter", "note": "草稿"})
        updated = store.add_archive_item("u1", {"id": "time-letter-1", "kind": "timeLetter", "note": "封存"})
        listed = store.list_archive_items("u1")
        deleted = store.delete_archive_item("u1", "time-letter-1")
        missing = store.delete_archive_item("u1", "time-letter-1")

        self.assertEqual(updated["note"], "封存")
        self.assertEqual(len([item for item in listed if item.get("id") == "time-letter-1"]), 1)
        self.assertEqual(listed[0]["note"], "封存")
        self.assertEqual(deleted["id"], "time-letter-1")
        self.assertIsNone(missing)
        self.assertEqual(store.list_archive_items("u1"), [])

    def test_store_marks_due_time_letters_delivered_once(self):
        connection = FakeConnection()
        store = PostgresStore(connection_factory=lambda: connection)

        store.add_archive_item(
            "u1",
            {
                "id": "time-letter-due",
                "kind": "timeLetter",
                "deliveryState": "sealed",
                "deliveryStatus": "scheduled",
                "openAt": "2026-07-02T08:00:00Z",
                "metadata": {
                    "deliveryState": "sealed",
                    "deliveryStatus": "scheduled",
                    "openAt": "2026-07-02T08:00:00Z",
                },
            },
        )
        store.add_archive_item(
            "u1",
            {
                "id": "time-letter-future",
                "kind": "timeLetter",
                "deliveryState": "sealed",
                "deliveryStatus": "scheduled",
                "openAt": "2999-01-01T00:00:00Z",
                "metadata": {
                    "deliveryState": "sealed",
                    "deliveryStatus": "scheduled",
                    "openAt": "2999-01-01T00:00:00Z",
                },
            },
        )

        dispatched = store.mark_due_time_letters_delivered(
            cutoff_iso="2026-07-02T09:00:00Z",
            delivered_at_iso="2026-07-02T09:00:00Z",
            limit=10,
        )
        repeated = store.mark_due_time_letters_delivered(
            cutoff_iso="2026-07-02T09:00:00Z",
            delivered_at_iso="2026-07-02T09:01:00Z",
            limit=10,
        )

        self.assertEqual([item["id"] for item in dispatched], ["time-letter-due"])
        self.assertEqual(repeated, [])
        listed = {item["id"]: item for item in store.list_archive_items("u1")}
        self.assertEqual(listed["time-letter-due"]["deliveryStatus"], "delivered")
        self.assertEqual(listed["time-letter-due"]["metadata"]["deliveryStatus"], "delivered")
        self.assertEqual(listed["time-letter-due"]["metadata"]["deliveryExecutionState"], "delivered")
        self.assertEqual(listed["time-letter-due"]["metadata"]["deliveredAt"], "2026-07-02T09:00:00Z")
        self.assertEqual(listed["time-letter-future"]["deliveryStatus"], "scheduled")

    def test_store_persists_family_member_revocation(self):
        connection = FakeConnection()
        store = PostgresStore(connection_factory=lambda: connection)

        member = store.add_family_member("u1", {"name": "林桂芳"})
        revoked = store.revoke_family_member("u1", member["id"])

        self.assertEqual(revoked["accessStatus"], "revoked")
        self.assertEqual(revoked["invitationStatus"], "revoked")
        self.assertFalse(revoked["isOnline"])
        self.assertEqual(store.list_family_members("u1")[0]["accessStatus"], "revoked")

    def test_store_persists_family_member_digital_human_contract(self):
        connection = FakeConnection()
        store = PostgresStore(connection_factory=lambda: connection)

        member = store.add_family_member(
            "u1",
            {
                "name": "林桂芳",
                "personaScope": "family",
                "digitalHumanId": "family_linguifang",
                "digitalHumanMode": "silent",
                "digitalHumanModeLabel": "静默",
                "backendContractMode": "mockFamilyPersona",
                "familyPersonaContractVersion": 1,
                "defaultReleaseVisible": False,
            },
        )
        listed = store.list_family_members("u1")

        self.assertEqual(member["digitalHumanMode"], "silent")
        self.assertEqual(member["digitalHumanModeLabel"], "静默")
        self.assertEqual(member["backendContractMode"], "mockFamilyPersona")
        self.assertEqual(member["familyPersonaContractVersion"], 1)
        self.assertFalse(member["defaultReleaseVisible"])
        self.assertEqual(listed[0]["digitalHumanId"], "family_linguifang")

    def test_store_persists_voice_profiles_disable_and_delete_states(self):
        connection = FakeConnection()
        store = PostgresStore(connection_factory=lambda: connection)

        profile = store.save_voice_profile(
            "u1",
            {
                "voiceProfileId": "voice_profile_1",
                "sampleStatus": "pending",
                "authorizationConfirmed": True,
            },
        )
        disabled = store.save_voice_profile(
            "u1",
            {
                **profile,
                "sampleStatus": "disabled",
                "isEnabled": False,
                "disabledAt": "2026-06-19T00:00:00+00:00",
            },
        )
        deleted = store.save_voice_profile(
            "u1",
            {
                **disabled,
                "sampleStatus": "deleted",
                "deletionState": "deleted",
                "deletedAt": "2026-06-19T00:01:00+00:00",
            },
        )
        listed = store.list_voice_profiles("u1")
        fetched = store.get_voice_profile("u1", "voice_profile_1")

        self.assertEqual(profile["voiceProfileId"], "voice_profile_1")
        self.assertEqual(disabled["sampleStatus"], "disabled")
        self.assertEqual(deleted["sampleStatus"], "deleted")
        self.assertEqual(deleted["deletionState"], "deleted")
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["sampleStatus"], "deleted")
        self.assertEqual(fetched["voiceProfileId"], "voice_profile_1")

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

    def test_store_marks_due_echo_delayed_replies_for_dispatch(self):
        connection = FakeConnection()
        store = PostgresStore(connection_factory=lambda: connection)

        store.add_echo_delayed_reply(
            "u1",
            {
                "id": "reply_due",
                "delayedReplyId": "reply_due",
                "deliverAt": "2026-06-18T12:05:00Z",
                "minutes": 7,
                "trigger": "tenRoundBaseline",
                "deliveryState": "scheduled",
                "pushProviderState": "pending",
            },
        )
        store.add_echo_delayed_reply(
            "u1",
            {
                "id": "reply_future",
                "delayedReplyId": "reply_future",
                "deliverAt": "2026-06-18T12:20:00Z",
                "minutes": 7,
                "trigger": "contentSignal",
                "deliveryState": "scheduled",
                "pushProviderState": "pending",
            },
        )

        dispatched = store.mark_due_echo_delayed_replies_for_dispatch(
            cutoff_iso="2026-06-18T12:06:00Z",
            dispatched_at_iso="2026-06-18T12:06:00Z",
            limit=10,
        )

        self.assertEqual(len(dispatched), 1)
        self.assertEqual(dispatched[0]["id"], "reply_due")
        self.assertEqual(dispatched[0]["deliveryState"], "readyForProvider")
        self.assertEqual(dispatched[0]["pushProviderState"], "queued")
        self.assertEqual(dispatched[0]["dispatchAttemptedAt"], "2026-06-18T12:06:00Z")
        listed = {item["id"]: item for item in store.list_echo_delayed_replies("u1")}
        self.assertEqual(listed["reply_due"]["deliveryState"], "readyForProvider")
        self.assertEqual(listed["reply_future"]["deliveryState"], "scheduled")

    def test_store_persists_push_device_tokens_by_user(self):
        connection = FakeConnection()
        store = PostgresStore(connection_factory=lambda: connection)

        store.save_push_device_token(
            "u1",
            {
                "id": "push_1",
                "deviceTokenId": "push_1",
                "userId": "u1",
                "deviceTokenHash": "hash_1",
                "platform": "ios",
                "environment": "sandbox",
            },
        )
        store.save_push_device_token(
            "u2",
            {
                "id": "push_2",
                "deviceTokenId": "push_2",
                "userId": "u2",
                "deviceTokenHash": "hash_2",
                "platform": "ios",
                "environment": "production",
            },
        )

        self.assertEqual(store.list_push_device_tokens("u1")[0]["deviceTokenId"], "push_1")
        self.assertEqual(store.list_push_device_tokens("u2")[0]["environment"], "production")

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
