from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional
import uuid

from psycopg.types.json import Jsonb

from app.services.user_identity import stable_user_id


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
            CREATE TABLE IF NOT EXISTS mailbox_letters (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                payload JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_mailbox_letters_user_created
                ON mailbox_letters(user_id, created_at DESC)
            """,
            """
            CREATE TABLE IF NOT EXISTS echo_delayed_replies (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                payload JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_echo_delayed_replies_user_created
                ON echo_delayed_replies(user_id, created_at DESC)
            """,
            """
            CREATE TABLE IF NOT EXISTS push_device_tokens (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                payload JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_push_device_tokens_user_updated
                ON push_device_tokens(user_id, updated_at DESC)
            """,
            """
            CREATE TABLE IF NOT EXISTS voice_profiles (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                payload JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_voice_profiles_user_updated
                ON voice_profiles(user_id, updated_at DESC)
            """,
            """
            CREATE TABLE IF NOT EXISTS profiles (
                user_id TEXT PRIMARY KEY,
                payload JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS password_credentials (
                user_id TEXT PRIMARY KEY,
                payload JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
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
            """
            CREATE INDEX IF NOT EXISTS idx_family_members_invitation_code
                ON family_members ((payload->>'invitationCode'))
            """,
            """
            CREATE TABLE IF NOT EXISTS care_snapshots (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                viewer_family_member_id TEXT,
                payload JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_care_snapshots_user_viewer_created
                ON care_snapshots(user_id, viewer_family_member_id, created_at DESC)
            """,
        ]
        connection = self._connect()
        with connection.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)
        connection.commit()

    def upsert_user(self, phone: str, nickname: str) -> Dict[str, Any]:
        user_id = stable_user_id(phone)
        existing = self.get_user(user_id) or {}
        user = {
            "id": user_id,
            "phone": phone,
            "nickname": nickname or "寻梦环游用户",
            "updatedAt": self._now(),
            "restoreCount": int(existing.get("restoreCount") or 0),
            "deletionState": "active",
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

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone(
            "SELECT payload FROM users WHERE id = %s",
            (user_id,),
        )
        return None if row is None else deepcopy(row["payload"])

    def soft_delete_user(
        self,
        user_id: str,
        *,
        phone: str,
        requested_at_iso: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        user = self.get_user(user_id)
        if user is None:
            return None
        if self._normalized_phone(str(user.get("phone") or "")) != self._normalized_phone(phone):
            return None

        requested_at = self._parse_iso_datetime(requested_at_iso) if requested_at_iso else datetime.now(timezone.utc)
        purge_after = requested_at + timedelta(days=30)
        item = deepcopy(user)
        item["deletionState"] = "softDeleted"
        item["deletedAt"] = requested_at.isoformat()
        item["purgeAfter"] = purge_after.isoformat()
        item["restoreDeadline"] = purge_after.isoformat()
        item["retentionDays"] = 30
        item["dataExportSupported"] = False
        item["restoreLimit"] = 1
        item["restoreCount"] = int(item.get("restoreCount") or 0)
        item["updatedAt"] = self._now()

        row = self._fetchone(
            """
            UPDATE users
            SET payload = %s, updated_at = NOW()
            WHERE id = %s
            RETURNING payload
            """,
            (item, user_id),
            commit=True,
        )
        return None if row is None else deepcopy(row["payload"])

    def restore_user(
        self,
        user_id: str,
        *,
        phone: str,
        nickname: str = "",
        restored_at_iso: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        user = self.get_user(user_id)
        if user is None:
            return None
        if self._normalized_phone(str(user.get("phone") or "")) != self._normalized_phone(phone):
            return None

        restored_at = restored_at_iso or self._now()
        item = deepcopy(user)
        item["nickname"] = nickname or item.get("nickname") or "寻梦环游用户"
        item["deletionState"] = "active"
        item["restoreCount"] = int(item.get("restoreCount") or 0) + 1
        item["restoredAt"] = restored_at
        item["updatedAt"] = restored_at
        for key in ("deletedAt", "purgeAfter", "restoreDeadline", "retentionDays", "dataExportSupported", "restoreLimit"):
            item.pop(key, None)

        row = self._fetchone(
            """
            UPDATE users
            SET phone = %s, nickname = %s, payload = %s, updated_at = NOW()
            WHERE id = %s
            RETURNING payload
            """,
            (phone, item["nickname"], item, user_id),
            commit=True,
        )
        return None if row is None else deepcopy(row["payload"])

    def purge_expired_deleted_users(self, cutoff_iso: str) -> List[Dict[str, Any]]:
        cutoff = self._parse_iso_datetime(cutoff_iso)
        rows = self._fetchall(
            """
            SELECT id, payload FROM users
            WHERE payload->>'deletionState' = 'softDeleted'
            """,
        )
        purged: List[Dict[str, Any]] = []
        for row in rows:
            user_id = str(row.get("id") or row["payload"].get("id") or "")
            user = deepcopy(row["payload"])
            deadline = self._parse_iso_datetime(str(user.get("restoreDeadline") or user.get("purgeAfter") or ""))
            if not user_id or deadline > cutoff:
                continue
            for table in (
                "profiles",
                "password_credentials",
                "kb_snapshots",
                "memories",
                "archive_items",
                "mailbox_letters",
                "family_members",
                "care_snapshots",
                "echo_delayed_replies",
                "push_device_tokens",
                "voice_profiles",
            ):
                self._fetchall(f"DELETE FROM {table} WHERE user_id = %s RETURNING payload", (user_id,))
            tombstone = {
                "id": user_id,
                "phone": user.get("phone", ""),
                "nickname": "",
                "deletionState": "purged",
                "purgedAt": cutoff.isoformat(),
                "restoreCount": int(user.get("restoreCount") or 0),
            }
            updated = self._fetchone(
                """
                UPDATE users
                SET nickname = %s, payload = %s, updated_at = NOW()
                WHERE id = %s
                RETURNING payload
                """,
                ("", tombstone, user_id),
                commit=True,
            )
            if updated is not None:
                purged.append(deepcopy(updated["payload"]))
        return purged

    def save_profile(self, user_id: str, profile: Dict[str, Any]) -> Dict[str, Any]:
        item = deepcopy(profile)
        item["userId"] = user_id
        item["updatedAt"] = self._now()
        row = self._fetchone(
            """
            INSERT INTO profiles (user_id, payload, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (user_id) DO UPDATE SET
                payload = EXCLUDED.payload,
                updated_at = NOW()
            RETURNING payload
            """,
            (user_id, item),
            commit=True,
        )
        return deepcopy(row["payload"])

    def get_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone(
            "SELECT payload FROM profiles WHERE user_id = %s",
            (user_id,),
        )
        return None if row is None else deepcopy(row["payload"])

    def save_password_credential(self, user_id: str, credential: Dict[str, Any]) -> Dict[str, Any]:
        item = deepcopy(credential)
        item["userId"] = user_id
        item["updatedAt"] = self._now()
        row = self._fetchone(
            """
            INSERT INTO password_credentials (user_id, payload, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (user_id) DO UPDATE SET
                payload = EXCLUDED.payload,
                updated_at = NOW()
            RETURNING payload
            """,
            (user_id, item),
            commit=True,
        )
        return deepcopy(row["payload"])

    def get_password_credential(self, user_id: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone(
            "SELECT payload FROM password_credentials WHERE user_id = %s",
            (user_id,),
        )
        return None if row is None else deepcopy(row["payload"])

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

    def list_archive_items(self, user_id: str) -> List[Dict[str, Any]]:
        return self._list_payloads("archive_items", user_id)

    def delete_archive_item(self, user_id: str, item_id: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone(
            """
            DELETE FROM archive_items
            WHERE user_id = %s AND id = %s
            RETURNING payload
            """,
            (user_id, item_id),
            commit=True,
        )
        return None if row is None else deepcopy(row["payload"])

    def mark_due_time_letters_delivered(
        self,
        cutoff_iso: str,
        delivered_at_iso: str,
        limit: int = 25,
    ) -> List[Dict[str, Any]]:
        bounded_limit = max(1, min(limit, 100))
        rows = self._fetchall(
            """
            SELECT user_id, id, payload FROM archive_items
            WHERE payload->>'kind' = 'timeLetter'
                AND COALESCE(payload->>'deliveryState', payload->'metadata'->>'deliveryState') = 'sealed'
                AND COALESCE(
                    payload->>'deliveryStatus',
                    payload->'metadata'->>'deliveryStatus',
                    payload->'metadata'->>'deliveryExecutionState'
                ) = 'scheduled'
                AND COALESCE(payload->>'openAt', payload->'metadata'->>'openAt') <= %s
            ORDER BY COALESCE(payload->>'openAt', payload->'metadata'->>'openAt') ASC
            LIMIT %s
            """,
            (cutoff_iso, bounded_limit),
        )

        dispatched: List[Dict[str, Any]] = []
        for row in rows:
            item = deepcopy(row["payload"])
            metadata = deepcopy(item.get("metadata") if isinstance(item.get("metadata"), dict) else {})
            item["userId"] = row["user_id"]
            item["deliveryStatus"] = "delivered"
            item["deliveryExecutionState"] = "delivered"
            item["deliveryScheduleState"] = "dispatched"
            item["deliveryProviderState"] = "local_notification_and_in_app"
            item["deliveredAt"] = delivered_at_iso
            item["dispatchAttemptedAt"] = delivered_at_iso
            item["providerDeliveryAttempted"] = False
            metadata["deliveryStatus"] = "delivered"
            metadata["deliveryExecutionState"] = "delivered"
            metadata["deliveryScheduleState"] = "dispatched"
            metadata["deliveryProviderState"] = "local_notification_and_in_app"
            metadata["deliveredAt"] = delivered_at_iso
            metadata["dispatchAttemptedAt"] = delivered_at_iso
            item["metadata"] = metadata
            item["updatedAt"] = delivered_at_iso
            updated = self._fetchone(
                """
                UPDATE archive_items
                SET payload = %s
                WHERE user_id = %s AND id = %s
                RETURNING payload
                """,
                (item, row["user_id"], row["id"]),
                commit=True,
            )
            if updated is not None:
                dispatched.append(deepcopy(updated["payload"]))
        return dispatched

    def add_mailbox_letter(self, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        item = self._with_identity(payload, "mailbox", user_id)
        item["updatedAt"] = self._now()
        row = self._fetchone(
            """
            INSERT INTO mailbox_letters (user_id, id, payload, created_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (id) DO UPDATE SET
                user_id = EXCLUDED.user_id,
                payload = EXCLUDED.payload,
                created_at = NOW()
            RETURNING payload
            """,
            (user_id, item["id"], item),
            commit=True,
        )
        return deepcopy(row["payload"])

    def list_mailbox_letters(self, user_id: str) -> List[Dict[str, Any]]:
        return self._list_payloads("mailbox_letters", user_id)

    def mark_mailbox_letter_read(
        self,
        user_id: str,
        letter_id: str,
        read_at_iso: str,
    ) -> Optional[Dict[str, Any]]:
        row = self._fetchone(
            """
            SELECT payload FROM mailbox_letters
            WHERE user_id = %s AND id = %s
            """,
            (user_id, letter_id),
        )
        if row is None:
            return None
        item = deepcopy(row["payload"])
        item["status"] = "read"
        item["readAt"] = read_at_iso
        item["updatedAt"] = read_at_iso
        updated = self._fetchone(
            """
            UPDATE mailbox_letters
            SET payload = %s
            WHERE user_id = %s AND id = %s
            RETURNING payload
            """,
            (item, user_id, letter_id),
            commit=True,
        )
        return None if updated is None else deepcopy(updated["payload"])

    def add_echo_delayed_reply(self, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        item = self._with_identity(payload, "echo_delayed", user_id)
        row = self._fetchone(
            """
            INSERT INTO echo_delayed_replies (user_id, id, payload, created_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (id) DO UPDATE SET
                user_id = EXCLUDED.user_id,
                payload = EXCLUDED.payload,
                created_at = NOW()
            RETURNING payload
            """,
            (user_id, item["id"], item),
            commit=True,
        )
        return deepcopy(row["payload"])

    def list_echo_delayed_replies(self, user_id: str) -> List[Dict[str, Any]]:
        return self._list_payloads("echo_delayed_replies", user_id)

    def mark_due_echo_delayed_replies_for_dispatch(
        self,
        cutoff_iso: str,
        dispatched_at_iso: str,
        limit: int = 25,
    ) -> List[Dict[str, Any]]:
        bounded_limit = max(1, min(limit, 100))
        rows = self._fetchall(
            """
            SELECT user_id, id, payload FROM echo_delayed_replies
            WHERE payload->>'deliveryState' = 'scheduled'
                AND payload->>'deliverAt' <= %s
            ORDER BY payload->>'deliverAt' ASC
            LIMIT %s
            """,
            (cutoff_iso, bounded_limit),
        )

        dispatched: List[Dict[str, Any]] = []
        for row in rows:
            item = deepcopy(row["payload"])
            item["deliveryState"] = "readyForProvider"
            item["pushProviderState"] = "queued"
            item["dispatchAttemptedAt"] = dispatched_at_iso
            item["providerDeliveryAttempted"] = False
            updated = self._fetchone(
                """
                UPDATE echo_delayed_replies
                SET payload = %s
                WHERE user_id = %s AND id = %s
                RETURNING payload
                """,
                (item, row["user_id"], row["id"]),
                commit=True,
            )
            if updated is not None:
                dispatched.append(deepcopy(updated["payload"]))
        return dispatched

    def save_push_device_token(self, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        item = deepcopy(payload)
        item["userId"] = user_id
        item.setdefault("id", item.get("deviceTokenId") or f"push_token_{uuid.uuid4().hex}")
        item.setdefault("deviceTokenId", item["id"])
        item["updatedAt"] = self._now()
        row = self._fetchone(
            """
            INSERT INTO push_device_tokens (user_id, id, payload, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (id) DO UPDATE SET
                user_id = EXCLUDED.user_id,
                payload = EXCLUDED.payload,
                updated_at = NOW()
            RETURNING payload
            """,
            (user_id, item["id"], item),
            commit=True,
        )
        return deepcopy(row["payload"])

    def list_push_device_tokens(self, user_id: str) -> List[Dict[str, Any]]:
        rows = self._fetchall(
            """
            SELECT payload FROM push_device_tokens
            WHERE user_id = %s
            ORDER BY updated_at DESC
            """,
            (user_id,),
        )
        return [deepcopy(row["payload"]) for row in rows]

    def save_voice_profile(self, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        item = deepcopy(payload)
        item["userId"] = user_id
        item.setdefault("id", item.get("voiceProfileId") or f"voice_profile_{uuid.uuid4().hex}")
        item.setdefault("voiceProfileId", item["id"])
        item["updatedAt"] = self._now()
        row = self._fetchone(
            """
            INSERT INTO voice_profiles (user_id, id, payload, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (id) DO UPDATE SET
                user_id = EXCLUDED.user_id,
                payload = EXCLUDED.payload,
                updated_at = NOW()
            RETURNING payload
            """,
            (user_id, item["voiceProfileId"], item),
            commit=True,
        )
        return deepcopy(row["payload"])

    def list_voice_profiles(self, user_id: str) -> List[Dict[str, Any]]:
        rows = self._fetchall(
            """
            SELECT payload FROM voice_profiles
            WHERE user_id = %s
            ORDER BY updated_at DESC
            """,
            (user_id,),
        )
        return [deepcopy(row["payload"]) for row in rows]

    def get_voice_profile(self, user_id: str, voice_profile_id: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone(
            """
            SELECT payload FROM voice_profiles
            WHERE user_id = %s AND id = %s
            """,
            (user_id, voice_profile_id),
        )
        return None if row is None else deepcopy(row["payload"])

    def add_family_member(self, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        item = self._with_identity(payload, "family", user_id)
        item["ownerUserId"] = user_id
        item.setdefault("invitationCode", "")
        item.setdefault("invitationURL", "")
        return self._insert_payload("family_members", user_id, item)

    def list_family_members(self, user_id: str) -> List[Dict[str, Any]]:
        return self._list_payloads("family_members", user_id)

    def accept_family_member(self, user_id: str, member_id: str, phone: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone(
            """
            SELECT payload FROM family_members
            WHERE user_id = %s AND id = %s
            """,
            (user_id, member_id),
        )
        if row is None:
            return None

        item = deepcopy(row["payload"])
        item["ownerUserId"] = item.get("ownerUserId") or item.get("userId")
        expected_phone = self._normalized_phone(str(item.get("phone") or ""))
        if expected_phone and self._normalized_phone(phone) != expected_phone:
            return None
        if item.get("accessStatus") == "revoked" or item.get("invitationStatus") == "revoked":
            return None
        if item.get("accessStatus") == "active" and item.get("invitationStatus") == "accepted":
            return deepcopy(item)

        item["accessStatus"] = "active"
        item["invitationStatus"] = "accepted"
        item["isOnline"] = True
        item["acceptedAt"] = self._now()
        item["lastUpdated"] = "刚刚接受邀请"

        updated = self._fetchone(
            """
            UPDATE family_members
            SET payload = %s
            WHERE user_id = %s AND id = %s
            RETURNING payload
            """,
            (item, user_id, member_id),
            commit=True,
        )
        return None if updated is None else deepcopy(updated["payload"])

    def accept_family_invitation_code(self, invitation_code: str, phone: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone(
            """
            SELECT payload FROM family_members
            WHERE payload->>'invitationCode' = %s
            LIMIT 1
            """,
            (invitation_code,),
        )
        if row is None:
            return None

        item = deepcopy(row["payload"])
        expected_phone = self._normalized_phone(str(item.get("phone") or ""))
        if expected_phone and self._normalized_phone(phone) != expected_phone:
            return None
        if item.get("accessStatus") == "revoked" or item.get("invitationStatus") == "revoked":
            return None
        if item.get("accessStatus") == "active" and item.get("invitationStatus") == "accepted":
            item["ownerUserId"] = item.get("ownerUserId") or item.get("userId")
            return deepcopy(item)

        item["accessStatus"] = "active"
        item["invitationStatus"] = "accepted"
        item["isOnline"] = True
        item["acceptedAt"] = self._now()
        item["lastUpdated"] = "刚刚接受邀请"

        updated = self._fetchone(
            """
            UPDATE family_members
            SET payload = %s
            WHERE user_id = %s AND id = %s
            RETURNING payload
            """,
            (item, item.get("userId"), item.get("id")),
            commit=True,
        )
        return None if updated is None else deepcopy(updated["payload"])

    def revoke_family_member(self, user_id: str, member_id: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone(
            """
            SELECT payload FROM family_members
            WHERE user_id = %s AND id = %s
            """,
            (user_id, member_id),
        )
        if row is None:
            return None

        item = deepcopy(row["payload"])
        item["accessStatus"] = "revoked"
        item["invitationStatus"] = "revoked"
        item["isOnline"] = False
        item["revokedAt"] = self._now()
        item["lastUpdated"] = "访问已撤回"

        updated = self._fetchone(
            """
            UPDATE family_members
            SET payload = %s
            WHERE user_id = %s AND id = %s
            RETURNING payload
            """,
            (item, user_id, member_id),
            commit=True,
        )
        return None if updated is None else deepcopy(updated["payload"])

    def save_care_snapshot(
        self,
        user_id: str,
        snapshot: Dict[str, Any],
        viewer_family_member_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        item = {
            "id": f"care_{uuid.uuid4().hex}",
            "userId": user_id,
            "viewerFamilyMemberID": viewer_family_member_id,
            "snapshot": deepcopy(snapshot),
            "createdAt": self._now(),
        }
        row = self._fetchone(
            """
            INSERT INTO care_snapshots (user_id, id, viewer_family_member_id, payload, created_at)
            VALUES (%s, %s, %s, %s, NOW())
            RETURNING payload
            """,
            (user_id, item["id"], viewer_family_member_id, item),
            commit=True,
        )
        return deepcopy(row["payload"])

    def get_latest_care_snapshot(
        self,
        user_id: str,
        viewer_family_member_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if viewer_family_member_id is None:
            row = self._fetchone(
                """
                SELECT payload FROM care_snapshots
                WHERE user_id = %s AND viewer_family_member_id IS NULL
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (user_id,),
            )
        else:
            row = self._fetchone(
                """
                SELECT payload FROM care_snapshots
                WHERE user_id = %s AND viewer_family_member_id = %s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (user_id, viewer_family_member_id),
            )
        return None if row is None else deepcopy(row["payload"])

    def list_care_snapshots(
        self,
        user_id: str,
        viewer_family_member_id: Optional[str] = None,
        limit: int = 7,
    ) -> List[Dict[str, Any]]:
        bounded_limit = max(1, min(limit, 30))
        if viewer_family_member_id is None:
            rows = self._fetchall(
                """
                SELECT payload FROM care_snapshots
                WHERE user_id = %s AND viewer_family_member_id IS NULL
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (user_id, bounded_limit),
            )
        else:
            rows = self._fetchall(
                """
                SELECT payload FROM care_snapshots
                WHERE user_id = %s AND viewer_family_member_id = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (user_id, viewer_family_member_id, bounded_limit),
            )
        return [deepcopy(row["payload"]) for row in rows]

    def _insert_payload(self, table: str, user_id: str, item: Dict[str, Any]) -> Dict[str, Any]:
        row = self._fetchone(
            f"""
            INSERT INTO {table} (user_id, id, payload, created_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (id) DO UPDATE SET
                user_id = EXCLUDED.user_id,
                payload = EXCLUDED.payload,
                created_at = NOW()
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
        try:
            with connection.cursor(row_factory=self._dict_row_factory()) as cursor:
                cursor.execute(sql, self._adapt_params(params))
                row = cursor.fetchone()
            if commit:
                connection.commit()
            return row
        except Exception:
            self._rollback(connection)
            raise

    def _fetchall(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        connection = self._connect()
        try:
            with connection.cursor(row_factory=self._dict_row_factory()) as cursor:
                cursor.execute(sql, self._adapt_params(params))
                rows = cursor.fetchall()
            return rows
        except Exception:
            self._rollback(connection)
            raise

    @staticmethod
    def _adapt_params(params: tuple) -> tuple:
        return tuple(Jsonb(param) if isinstance(param, dict) else param for param in params)

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
    def _rollback(connection: Any) -> None:
        rollback = getattr(connection, "rollback", None)
        if callable(rollback):
            rollback()

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

    @staticmethod
    def _normalized_phone(phone: str) -> str:
        return "".join(ch for ch in phone if ch.isdigit())

    @staticmethod
    def _parse_iso_datetime(value: str) -> datetime:
        if not value:
            return datetime.min.replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
