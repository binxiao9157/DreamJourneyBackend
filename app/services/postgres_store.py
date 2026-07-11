from copy import deepcopy
from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Any, Callable, Dict, List, Optional
import uuid

from psycopg.types.json import Jsonb

from app.services.user_identity import stable_user_id
from app.services.knowledge_store import (
    KB_OPERATION_ARCHIVE_DELETE,
    KB_OPERATION_MUTATION,
    KnowledgeRevisionConflict,
    apply_kb_mutation_v2,
    knowledge_operation_payload_fingerprint,
    normalize_kb_mutation_v2,
    verify_knowledge_operation_receipt,
)
from app.services.archive_store import (
    ArchiveItemDeletionForbidden,
    ArchiveItemNotFound,
    ArchiveItemOwnershipConflict,
    is_sealed_time_letter,
)


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
                revision BIGINT NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            ALTER TABLE kb_snapshots
                ADD COLUMN IF NOT EXISTS revision BIGINT NOT NULL DEFAULT 0
            """,
            """
            CREATE TABLE IF NOT EXISTS kb_changes (
                user_id TEXT NOT NULL,
                revision BIGINT NOT NULL,
                operation_id TEXT NOT NULL,
                graph JSONB NOT NULL,
                mutation JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (user_id, revision),
                UNIQUE (user_id, operation_id)
            )
            """,
            """
            ALTER TABLE kb_changes
                ADD COLUMN IF NOT EXISTS mutation JSONB
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_kb_changes_user_revision
                ON kb_changes(user_id, revision ASC)
            """,
            """
            CREATE TABLE IF NOT EXISTS kb_operation_receipts (
                user_id TEXT NOT NULL,
                operation_id TEXT NOT NULL,
                operation_kind TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                payload_hash TEXT NOT NULL,
                result JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (user_id, operation_id)
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
            CREATE TABLE IF NOT EXISTS voice_clone_slots (
                provider_speaker_id TEXT PRIMARY KEY,
                voice_profile_id TEXT UNIQUE,
                user_id TEXT,
                persona_scope TEXT,
                digital_human_id TEXT,
                status TEXT NOT NULL DEFAULT 'available',
                training_attempts INTEGER NOT NULL DEFAULT 0,
                configured BOOLEAN NOT NULL DEFAULT TRUE,
                assigned_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_voice_clone_slots_user_updated
                ON voice_clone_slots(user_id, updated_at DESC)
            """,
            """
            CREATE TABLE IF NOT EXISTS digital_human_sessions (
                id TEXT PRIMARY KEY,
                resource_key TEXT NOT NULL,
                user_id TEXT NOT NULL,
                device_id TEXT NOT NULL,
                persona_id TEXT NOT NULL,
                scene TEXT NOT NULL,
                status TEXT NOT NULL,
                payload JSONB NOT NULL,
                heartbeat_at TIMESTAMPTZ NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL,
                released_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_digital_human_sessions_resource_status
                ON digital_human_sessions(resource_key, status, expires_at)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_digital_human_sessions_user_device
                ON digital_human_sessions(user_id, device_id, status)
            """,
            """
            CREATE TABLE IF NOT EXISTS auth_sessions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                access_token_hash TEXT UNIQUE NOT NULL,
                refresh_token_hash TEXT UNIQUE NOT NULL,
                status TEXT NOT NULL,
                payload JSONB NOT NULL,
                access_expires_at TIMESTAMPTZ NOT NULL,
                refresh_expires_at TIMESTAMPTZ NOT NULL,
                revoked_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_auth_sessions_user_status
                ON auth_sessions(user_id, status, updated_at DESC)
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

    def save_auth_session(self, session: Dict[str, Any]) -> Dict[str, Any]:
        item = deepcopy(session)
        row = self._fetchone(
            """
            INSERT INTO auth_sessions (
                id, user_id, access_token_hash, refresh_token_hash, status, payload,
                access_expires_at, refresh_expires_at, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            RETURNING payload
            """,
            (
                item["sessionId"],
                item["userId"],
                item["accessTokenHash"],
                item["refreshTokenHash"],
                item["status"],
                item,
                item["accessExpiresAt"],
                item["refreshExpiresAt"],
                item["createdAt"],
            ),
            commit=True,
        )
        return deepcopy(row["payload"])

    def get_auth_session_by_access_token_hash(self, token_hash: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone(
            "SELECT payload FROM auth_sessions WHERE access_token_hash = %s",
            (token_hash,),
        )
        return None if row is None else deepcopy(row["payload"])

    def consume_auth_session_refresh(
        self,
        refresh_token_hash: str,
        consumed_at_iso: str,
    ) -> Optional[Dict[str, Any]]:
        patch = {"status": "rotated", "rotatedAt": consumed_at_iso}
        row = self._fetchone(
            """
            UPDATE auth_sessions
            SET status = 'rotated',
                payload = payload || %s,
                revoked_at = %s,
                updated_at = NOW()
            WHERE refresh_token_hash = %s
              AND status = 'active'
              AND refresh_expires_at > %s
            RETURNING payload
            """,
            (patch, consumed_at_iso, refresh_token_hash, consumed_at_iso),
            commit=True,
        )
        return None if row is None else deepcopy(row["payload"])

    def revoke_auth_session_by_access_token_hash(
        self,
        access_token_hash: str,
        revoked_at_iso: str,
        reason: str,
    ) -> Optional[Dict[str, Any]]:
        patch = {
            "status": "revoked",
            "revokedAt": revoked_at_iso,
            "revokeReason": reason,
        }
        row = self._fetchone(
            """
            UPDATE auth_sessions
            SET status = 'revoked',
                payload = payload || %s,
                revoked_at = %s,
                updated_at = NOW()
            WHERE access_token_hash = %s
              AND status = 'active'
            RETURNING payload
            """,
            (patch, revoked_at_iso, access_token_hash),
            commit=True,
        )
        return None if row is None else deepcopy(row["payload"])

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
            self._fetchall(
                """
                UPDATE voice_clone_slots
                SET status = 'retired', updated_at = NOW()
                WHERE user_id = %s AND status NOT IN ('retired', 'deleted')
                RETURNING provider_speaker_id
                """,
                (user_id,),
            )
            self._fetchall(
                "DELETE FROM kb_changes WHERE user_id = %s RETURNING revision",
                (user_id,),
            )
            self._fetchall(
                "DELETE FROM kb_operation_receipts WHERE user_id = %s RETURNING operation_id",
                (user_id,),
            )
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
                "digital_human_sessions",
                "auth_sessions",
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
        return self.apply_kb_mutation(
            user_id,
            graph,
            operation_id=f"legacy-sync-{uuid.uuid4().hex}",
            base_revision=None,
        )

    def apply_kb_mutation(
        self,
        user_id: str,
        graph: Optional[Dict[str, Any]],
        *,
        operation_id: str,
        base_revision: Optional[int],
        mutation: Optional[Dict[str, Any]] = None,
        operation_kind: str = KB_OPERATION_MUTATION,
        operation_schema_version: Optional[int] = None,
        operation_payload: Optional[Any] = None,
        allow_revision_noop: bool = False,
    ) -> Dict[str, Any]:
        normalized_requested_mutation = None
        if mutation is not None:
            normalized_requested_mutation = normalize_kb_mutation_v2(
                mutation.get("upserts", {}) if isinstance(mutation, dict) else None,
                mutation.get("tombstones", []) if isinstance(mutation, dict) else None,
            )
        schema_version = operation_schema_version or (
            2 if normalized_requested_mutation is not None else 1
        )
        semantic_payload = operation_payload
        if semantic_payload is None:
            semantic_payload = (
                normalized_requested_mutation
                if normalized_requested_mutation is not None
                else graph
            )
        payload_hash = knowledge_operation_payload_fingerprint(
            operation_kind,
            schema_version,
            semantic_payload,
        )
        connection = self._open_connection()
        try:
            with connection.cursor(row_factory=self._dict_row_factory()) as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (f"knowledge:{user_id}",),
                )
                receipt_result = self._kb_operation_receipt_replay_cursor(
                    cursor,
                    user_id,
                    operation_id,
                    operation_kind=operation_kind,
                    payload_hash=payload_hash,
                )
                if receipt_result is not None:
                    connection.commit()
                    return receipt_result
                cursor.execute(
                    """
                    SELECT revision, graph, mutation, created_at
                    FROM kb_changes
                    WHERE user_id = %s AND operation_id = %s
                    """,
                    (user_id, operation_id),
                )
                existing = cursor.fetchone()
                if existing is not None:
                    stored_mutation = existing.get("mutation")
                    connection.commit()
                    return {
                        "userId": user_id,
                        "graph": deepcopy(existing["graph"]),
                        "revision": int(existing["revision"]),
                        "updatedAt": self._iso_value(existing.get("created_at")),
                        "operationId": operation_id,
                        "duplicate": True,
                        "operationPayloadVerified": False,
                        "mutationSchemaVersion": 2 if stored_mutation is not None else 1,
                        "mutation": deepcopy(stored_mutation),
                    }

                cursor.execute(
                    """
                    SELECT graph, revision, updated_at
                    FROM kb_snapshots
                    WHERE user_id = %s
                    FOR UPDATE
                    """,
                    (user_id,),
                )
                current = cursor.fetchone()
                current_revision = int((current or {}).get("revision") or 0)
                if base_revision is not None and base_revision != current_revision:
                    if allow_revision_noop and current is not None:
                        result = {
                            "userId": user_id,
                            "graph": deepcopy(current["graph"]),
                            "revision": current_revision,
                            "updatedAt": self._iso_value(current.get("updated_at")),
                            "operationId": operation_id,
                            "duplicate": False,
                            "operationPayloadVerified": True,
                            "mutationSchemaVersion": 1,
                            "mutation": None,
                            "compatibilityNoOp": True,
                        }
                        self._insert_kb_operation_receipt_cursor(
                            cursor,
                            user_id,
                            operation_id,
                            operation_kind=operation_kind,
                            schema_version=schema_version,
                            payload_hash=payload_hash,
                            result=result,
                        )
                        connection.commit()
                        return result
                    raise KnowledgeRevisionConflict(
                        current_revision=current_revision,
                        expected_revision=base_revision,
                    )

                if mutation is None:
                    if not isinstance(graph, dict):
                        raise ValueError("graph must be an object")
                    next_graph = deepcopy(graph)
                    normalized_mutation = None
                else:
                    next_graph, normalized_mutation = apply_kb_mutation_v2(
                        deepcopy((current or {}).get("graph") or {}),
                        normalized_requested_mutation,
                    )

                revision = current_revision + 1
                cursor.execute(
                    """
                    INSERT INTO kb_snapshots (user_id, graph, revision, updated_at)
                    VALUES (%s, %s, %s, NOW())
                    ON CONFLICT (user_id) DO UPDATE SET
                        graph = EXCLUDED.graph,
                        revision = EXCLUDED.revision,
                        updated_at = NOW()
                    RETURNING graph, revision, updated_at
                    """,
                    self._adapt_params((user_id, next_graph, revision)),
                )
                saved = cursor.fetchone()
                cursor.execute(
                    """
                    INSERT INTO kb_changes (
                        user_id, revision, operation_id, graph, mutation, created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, NOW())
                    RETURNING revision, operation_id, graph, mutation, created_at
                    """,
                    self._adapt_params(
                        (user_id, revision, operation_id, next_graph, normalized_mutation)
                    ),
                )
                change = cursor.fetchone()
                result = {
                    "userId": user_id,
                    "graph": deepcopy(saved["graph"]),
                    "revision": int(saved["revision"]),
                    "updatedAt": self._iso_value(saved.get("updated_at") or change.get("created_at")),
                    "operationId": operation_id,
                    "duplicate": False,
                    "operationPayloadVerified": True,
                    "mutationSchemaVersion": 2 if normalized_mutation is not None else 1,
                    "mutation": deepcopy(normalized_mutation),
                }
                self._insert_kb_operation_receipt_cursor(
                    cursor,
                    user_id,
                    operation_id,
                    operation_kind=operation_kind,
                    schema_version=schema_version,
                    payload_hash=payload_hash,
                    result=result,
                )
            connection.commit()
            return result
        except Exception:
            self._rollback(connection)
            raise
        finally:
            self._close(connection)

    def get_kb_operation_replay(
        self,
        user_id: str,
        operation_id: str,
        *,
        operation_kind: str,
        operation_schema_version: int,
        operation_payload: Any,
    ) -> Optional[Dict[str, Any]]:
        payload_hash = knowledge_operation_payload_fingerprint(
            operation_kind,
            operation_schema_version,
            operation_payload,
        )
        receipt = self._fetchone(
            """
            SELECT operation_kind, schema_version, payload_hash, result
            FROM kb_operation_receipts
            WHERE user_id = %s AND operation_id = %s
            """,
            (user_id, operation_id),
        )
        if receipt is not None:
            verify_knowledge_operation_receipt(
                {
                    "operationKind": receipt["operation_kind"],
                    "payloadHash": receipt["payload_hash"],
                },
                operation_kind=operation_kind,
                payload_hash=payload_hash,
            )
            result = deepcopy(receipt["result"])
            result["duplicate"] = True
            result["operationPayloadVerified"] = True
            return result
        existing = self._fetchone(
            """
            SELECT revision, graph, mutation, created_at
            FROM kb_changes
            WHERE user_id = %s AND operation_id = %s
            """,
            (user_id, operation_id),
        )
        if existing is None:
            return None
        stored_mutation = existing.get("mutation")
        return {
            "userId": user_id,
            "graph": deepcopy(existing["graph"]),
            "revision": int(existing["revision"]),
            "updatedAt": self._iso_value(existing.get("created_at")),
            "operationId": operation_id,
            "duplicate": True,
            "operationPayloadVerified": False,
            "mutationSchemaVersion": 2 if stored_mutation is not None else 1,
            "mutation": deepcopy(stored_mutation),
        }

    def get_kb_snapshot(self, user_id: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone(
            "SELECT graph FROM kb_snapshots WHERE user_id = %s",
            (user_id,),
        )
        return None if row is None else deepcopy(row["graph"])

    def get_kb_snapshot_record(self, user_id: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone(
            "SELECT graph, revision, updated_at FROM kb_snapshots WHERE user_id = %s",
            (user_id,),
        )
        if row is None:
            return None
        return {
            "userId": user_id,
            "graph": deepcopy(row["graph"]),
            "revision": int(row.get("revision") or 0),
            "updatedAt": self._iso_value(row.get("updated_at")),
        }

    def list_kb_changes(
        self,
        user_id: str,
        since_revision: int,
        through_revision: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        where_clauses = ["user_id = %s", "revision > %s"]
        params: List[Any] = [user_id, since_revision]
        if through_revision is not None:
            where_clauses.append("revision <= %s")
            params.append(through_revision)
        limit_clause = ""
        if limit is not None:
            limit_clause = "LIMIT %s"
            params.append(limit)
        rows = self._fetchall(
            f"""
            SELECT revision, operation_id, graph, mutation, created_at
            FROM kb_changes
            WHERE {' AND '.join(where_clauses)}
            ORDER BY revision ASC
            {limit_clause}
            """,
            tuple(params),
        )
        return [
            {
                "revision": int(row["revision"]),
                "operationId": str(row["operation_id"]),
                "graph": deepcopy(row["graph"]),
                "createdAt": self._iso_value(row.get("created_at")),
                "mutationSchemaVersion": 2 if row.get("mutation") is not None else 1,
                "mutation": deepcopy(row.get("mutation")),
            }
            for row in rows
        ]

    def acquire_digital_human_session_lease(
        self,
        candidate: Dict[str, Any],
        *,
        max_concurrent_sessions: int,
        now_iso: str,
    ) -> Dict[str, Any]:
        item = deepcopy(candidate)
        resource_key = str(item.get("resourceKey") or "")
        user_id = str(item.get("userId") or "")
        device_id = str(item.get("deviceId") or "")
        now = self._parse_iso_datetime(now_iso)
        bounded_capacity = max(1, max_concurrent_sessions)
        connection = self._connect()
        try:
            with connection.cursor(row_factory=self._dict_row_factory()) as cursor:
                lock_keys = sorted(
                    {
                        f"digital-human-device:{user_id}:{device_id}",
                        f"digital-human-resource:{resource_key}",
                    }
                )
                for lock_key in lock_keys:
                    cursor.execute(
                        "SELECT pg_advisory_xact_lock(hashtext(%s))",
                        (lock_key,),
                    )
                cursor.execute(
                    """
                    SELECT id, payload FROM digital_human_sessions
                    WHERE status = 'active'
                      AND (resource_key = %s OR (user_id = %s AND device_id = %s))
                    FOR UPDATE
                    """,
                    (resource_key, user_id, device_id),
                )
                rows = cursor.fetchall()
                active: List[Dict[str, Any]] = []
                for row in rows:
                    lease = deepcopy(row["payload"])
                    if self._parse_iso_datetime(str(lease.get("expiresAt") or "")) <= now:
                        expired = deepcopy(lease)
                        expired["status"] = "expired"
                        expired["expiredAt"] = now_iso
                        expired["updatedAt"] = now_iso
                        self._update_digital_human_session_cursor(cursor, expired)
                    else:
                        active.append(lease)

                reusable = next(
                    (lease for lease in active if self._same_digital_human_context(lease, item)),
                    None,
                )
                if reusable is not None:
                    updated = deepcopy(reusable)
                    updated["heartbeatAt"] = item.get("heartbeatAt") or now_iso
                    updated["expiresAt"] = item.get("expiresAt")
                    updated["updatedAt"] = now_iso
                    self._update_digital_human_session_cursor(cursor, updated)
                    connection.commit()
                    return {
                        "outcome": "reused",
                        "lease": updated,
                        "activeSessionCount": len(
                            [lease for lease in active if lease.get("resourceKey") == resource_key]
                        ),
                        "retryAfterSeconds": 0,
                    }

                remaining: List[Dict[str, Any]] = []
                for lease in active:
                    if lease.get("userId") == user_id and lease.get("deviceId") == device_id:
                        released = deepcopy(lease)
                        released["status"] = "released"
                        released["releasedAt"] = now_iso
                        released["releaseReason"] = "supersededByDeviceContext"
                        released["updatedAt"] = now_iso
                        self._update_digital_human_session_cursor(cursor, released)
                    else:
                        remaining.append(lease)

                resource_active = [
                    lease
                    for lease in remaining
                    if lease.get("resourceKey") == resource_key and lease.get("status") == "active"
                ]
                if len(resource_active) >= bounded_capacity:
                    retry_after = min(
                        max(
                            1,
                            ceil(
                                (
                                    self._parse_iso_datetime(str(lease.get("expiresAt") or "")) - now
                                ).total_seconds()
                            ),
                        )
                        for lease in resource_active
                    )
                    connection.commit()
                    return {
                        "outcome": "conflict",
                        "lease": None,
                        "activeSessionCount": len(resource_active),
                        "retryAfterSeconds": retry_after,
                    }

                item["status"] = "active"
                item.setdefault("createdAt", now_iso)
                item.setdefault("heartbeatAt", now_iso)
                item["updatedAt"] = now_iso
                cursor.execute(
                    """
                    INSERT INTO digital_human_sessions (
                        id, resource_key, user_id, device_id, persona_id, scene, status,
                        payload, heartbeat_at, expires_at, released_at, created_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, %s, NOW())
                    RETURNING payload
                    """,
                    self._adapt_params(
                        (
                            item["sessionId"],
                            resource_key,
                            user_id,
                            device_id,
                            item.get("personaId"),
                            item.get("scene"),
                            "active",
                            item,
                            item.get("heartbeatAt"),
                            item.get("expiresAt"),
                            item.get("createdAt"),
                        )
                    ),
                )
                row = cursor.fetchone()
            connection.commit()
            saved = deepcopy(row["payload"] if row is not None else item)
            return {
                "outcome": "created",
                "lease": saved,
                "activeSessionCount": len(resource_active) + 1,
                "retryAfterSeconds": 0,
            }
        except Exception:
            self._rollback(connection)
            raise

    def heartbeat_digital_human_session_lease(
        self,
        session_id: str,
        *,
        user_id: str,
        device_id: str,
        heartbeat_at_iso: str,
        expires_at_iso: str,
    ) -> Optional[Dict[str, Any]]:
        connection = self._connect()
        try:
            with connection.cursor(row_factory=self._dict_row_factory()) as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (f"digital-human-session:{session_id}",),
                )
                cursor.execute(
                    "SELECT payload FROM digital_human_sessions WHERE id = %s FOR UPDATE",
                    (session_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    connection.commit()
                    return None
                lease = deepcopy(row["payload"])
                if lease.get("userId") != user_id or lease.get("deviceId") != device_id:
                    connection.commit()
                    return None
                if lease.get("status") != "active":
                    connection.commit()
                    return {"outcome": self._inactive_digital_human_outcome(lease), "lease": lease}
                if self._parse_iso_datetime(str(lease.get("expiresAt") or "")) <= self._parse_iso_datetime(heartbeat_at_iso):
                    lease["status"] = "expired"
                    lease["expiredAt"] = heartbeat_at_iso
                    lease["updatedAt"] = heartbeat_at_iso
                    self._update_digital_human_session_cursor(cursor, lease)
                    connection.commit()
                    return {"outcome": "expired", "lease": lease}
                lease["heartbeatAt"] = heartbeat_at_iso
                lease["expiresAt"] = expires_at_iso
                lease["updatedAt"] = heartbeat_at_iso
                self._update_digital_human_session_cursor(cursor, lease)
            connection.commit()
            return {"outcome": "active", "lease": deepcopy(lease)}
        except Exception:
            self._rollback(connection)
            raise

    def release_digital_human_session_lease(
        self,
        session_id: str,
        *,
        user_id: str,
        device_id: str,
        released_at_iso: str,
        reason: str,
    ) -> Optional[Dict[str, Any]]:
        connection = self._connect()
        try:
            with connection.cursor(row_factory=self._dict_row_factory()) as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (f"digital-human-session:{session_id}",),
                )
                cursor.execute(
                    "SELECT payload FROM digital_human_sessions WHERE id = %s FOR UPDATE",
                    (session_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    connection.commit()
                    return None
                lease = deepcopy(row["payload"])
                if lease.get("userId") != user_id or lease.get("deviceId") != device_id:
                    connection.commit()
                    return None
                if lease.get("status") == "released":
                    connection.commit()
                    return {"outcome": "alreadyReleased", "lease": lease}
                if lease.get("status") == "expired":
                    connection.commit()
                    return {"outcome": "alreadyExpired", "lease": lease}
                lease["status"] = "released"
                lease["releasedAt"] = released_at_iso
                lease["releaseReason"] = reason
                lease["updatedAt"] = released_at_iso
                self._update_digital_human_session_cursor(cursor, lease)
            connection.commit()
            return {"outcome": "released", "lease": deepcopy(lease)}
        except Exception:
            self._rollback(connection)
            raise

    def get_digital_human_session_lease(self, session_id: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone(
            "SELECT payload FROM digital_human_sessions WHERE id = %s",
            (session_id,),
        )
        return None if row is None else deepcopy(row["payload"])

    def add_memory(self, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        item = self._with_identity(payload, "memory", user_id)
        return self._insert_payload("memories", user_id, item)

    def list_memories(self, user_id: str) -> List[Dict[str, Any]]:
        return self._list_payloads("memories", user_id)

    def add_archive_item(self, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        item = self._with_identity(payload, "archive", user_id)
        row = self._fetchone(
            """
            INSERT INTO archive_items (user_id, id, payload, created_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (id) DO UPDATE SET
                payload = EXCLUDED.payload,
                created_at = NOW()
            WHERE archive_items.user_id = EXCLUDED.user_id
            RETURNING payload
            """,
            (user_id, item["id"], item),
            commit=True,
        )
        if row is None:
            raise ArchiveItemOwnershipConflict("archive item id belongs to another owner")
        return deepcopy(row["payload"])

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

    def delete_archive_item_with_kb_mutation(
        self,
        user_id: str,
        item_id: str,
        *,
        operation_id: str,
        base_revision: int,
        mutation: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        operation_kind = KB_OPERATION_ARCHIVE_DELETE
        schema_version = 1
        payload_hash = knowledge_operation_payload_fingerprint(
            operation_kind,
            schema_version,
            {"itemId": item_id},
        )
        connection = self._open_connection()
        try:
            with connection.cursor(row_factory=self._dict_row_factory()) as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (f"knowledge:{user_id}",),
                )
                receipt_result = self._kb_operation_receipt_replay_cursor(
                    cursor,
                    user_id,
                    operation_id,
                    operation_kind=operation_kind,
                    payload_hash=payload_hash,
                )
                if receipt_result is not None:
                    receipt_result["item"] = None
                    connection.commit()
                    return receipt_result
                cursor.execute(
                    """
                    SELECT revision, graph, mutation, created_at
                    FROM kb_changes
                    WHERE user_id = %s AND operation_id = %s
                    """,
                    (user_id, operation_id),
                )
                existing = cursor.fetchone()
                if existing is not None:
                    stored_mutation = existing.get("mutation")
                    connection.commit()
                    return {
                        "item": None,
                        "duplicate": True,
                        "operationPayloadVerified": False,
                        "revision": int(existing["revision"]),
                        "graph": deepcopy(existing["graph"]),
                        "mutationSchemaVersion": 2 if stored_mutation is not None else 1,
                        "mutation": deepcopy(stored_mutation),
                    }

                cursor.execute(
                    """
                    SELECT payload
                    FROM archive_items
                    WHERE user_id = %s AND id = %s
                    FOR UPDATE
                    """,
                    (user_id, item_id),
                )
                archive_row = cursor.fetchone()
                if archive_row is None:
                    raise ArchiveItemNotFound("archive item not found")
                item = deepcopy(archive_row["payload"])
                if is_sealed_time_letter(item):
                    raise ArchiveItemDeletionForbidden("sealed timeLetter cannot be deleted")

                cursor.execute(
                    """
                    SELECT graph, revision, updated_at
                    FROM kb_snapshots
                    WHERE user_id = %s
                    FOR UPDATE
                    """,
                    (user_id,),
                )
                current = cursor.fetchone()
                current_revision = int((current or {}).get("revision") or 0)
                if base_revision != current_revision:
                    raise KnowledgeRevisionConflict(
                        current_revision=current_revision,
                        expected_revision=base_revision,
                    )

                graph = deepcopy((current or {}).get("graph") or {})
                revision = current_revision
                normalized_mutation = None
                if mutation is not None:
                    graph, normalized_mutation = apply_kb_mutation_v2(graph, mutation)
                    revision += 1
                    cursor.execute(
                        """
                        INSERT INTO kb_snapshots (user_id, graph, revision, updated_at)
                        VALUES (%s, %s, %s, NOW())
                        ON CONFLICT (user_id) DO UPDATE SET
                            graph = EXCLUDED.graph,
                            revision = EXCLUDED.revision,
                            updated_at = NOW()
                        RETURNING graph, revision, updated_at
                        """,
                        self._adapt_params((user_id, graph, revision)),
                    )
                    saved = cursor.fetchone()
                    graph = deepcopy(saved["graph"])
                    revision = int(saved["revision"])
                    cursor.execute(
                        """
                        INSERT INTO kb_changes (
                            user_id, revision, operation_id, graph, mutation, created_at
                        )
                        VALUES (%s, %s, %s, %s, %s, NOW())
                        RETURNING revision, operation_id, graph, mutation, created_at
                        """,
                        self._adapt_params(
                            (user_id, revision, operation_id, graph, normalized_mutation)
                        ),
                    )
                    cursor.fetchone()

                cursor.execute(
                    """
                    DELETE FROM archive_items
                    WHERE user_id = %s AND id = %s
                    RETURNING payload
                    """,
                    (user_id, item_id),
                )
                deleted = cursor.fetchone()
                if deleted is None:
                    raise ArchiveItemNotFound("archive item not found")
                result = {
                    "item": deepcopy(deleted["payload"]),
                    "duplicate": False,
                    "operationPayloadVerified": True,
                    "revision": revision,
                    "graph": graph,
                    "mutationSchemaVersion": 2 if normalized_mutation is not None else None,
                    "mutation": deepcopy(normalized_mutation),
                }
                self._insert_kb_operation_receipt_cursor(
                    cursor,
                    user_id,
                    operation_id,
                    operation_kind=operation_kind,
                    schema_version=schema_version,
                    payload_hash=payload_hash,
                    result={**deepcopy(result), "item": None},
                )
            connection.commit()
            return result
        except Exception:
            self._rollback(connection)
            raise
        finally:
            self._close(connection)

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
                    AND payload->>'kind' = 'timeLetter'
                    AND COALESCE(
                        payload->>'deliveryStatus',
                        payload->'metadata'->>'deliveryStatus',
                        payload->'metadata'->>'deliveryExecutionState'
                    ) = 'scheduled'
                    AND COALESCE(payload->>'openAt', payload->'metadata'->>'openAt') <= %s
                RETURNING payload
                """,
                (item, row["user_id"], row["id"], cutoff_iso),
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

    def archive_mailbox_letter(
        self,
        user_id: str,
        letter_id: str,
        archived_at_iso: str,
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
        item["status"] = "archived"
        item["archivedAt"] = archived_at_iso
        item["updatedAt"] = archived_at_iso
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
                payload = EXCLUDED.payload,
                updated_at = NOW()
            WHERE voice_profiles.user_id = EXCLUDED.user_id
            RETURNING payload
            """,
            (user_id, item["voiceProfileId"], item),
            commit=True,
        )
        if row is None:
            raise ValueError("voiceProfileId is already owned by another user")
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

    def allocate_voice_clone_slot(
        self,
        provider_speaker_ids: List[str],
        *,
        user_id: str,
        voice_profile_id: str,
        persona_scope: str,
        digital_human_id: str,
    ) -> Optional[Dict[str, Any]]:
        configured_ids = [speaker_id.strip() for speaker_id in provider_speaker_ids if speaker_id.strip()]
        if not configured_ids:
            return None

        existing = self.get_voice_clone_slot(voice_profile_id)
        if existing is not None and existing.get("userId") != user_id:
            return None

        for provider_speaker_id in configured_ids:
            self._fetchone(
                """
                INSERT INTO voice_clone_slots (provider_speaker_id, status, configured, updated_at)
                VALUES (%s, 'available', TRUE, NOW())
                ON CONFLICT (provider_speaker_id) DO UPDATE SET
                    configured = TRUE,
                    updated_at = NOW()
                RETURNING provider_speaker_id, voice_profile_id, user_id, persona_scope,
                    digital_human_id, status, training_attempts, configured, assigned_at, updated_at
                """,
                (provider_speaker_id,),
                commit=True,
            )

        try:
            row = self._fetchone(
                """
            WITH candidate AS (
                SELECT provider_speaker_id
                FROM voice_clone_slots
                WHERE provider_speaker_id = ANY(%s)
                    AND configured = TRUE
                    AND (
                        (voice_profile_id = %s AND user_id = %s AND status NOT IN ('retired', 'deleted'))
                        OR (voice_profile_id IS NULL AND status = 'available')
                    )
                ORDER BY CASE WHEN voice_profile_id = %s THEN 0 ELSE 1 END,
                    provider_speaker_id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE voice_clone_slots AS slots
            SET voice_profile_id = %s,
                user_id = %s,
                persona_scope = %s,
                digital_human_id = %s,
                status = CASE
                    WHEN slots.voice_profile_id = %s THEN slots.status
                    ELSE 'assigned'
                END,
                assigned_at = COALESCE(slots.assigned_at, NOW()),
                updated_at = NOW()
            FROM candidate
            WHERE slots.provider_speaker_id = candidate.provider_speaker_id
            RETURNING slots.provider_speaker_id, slots.voice_profile_id, slots.user_id,
                slots.persona_scope, slots.digital_human_id, slots.status,
                slots.training_attempts, slots.configured, slots.assigned_at, slots.updated_at
            """,
                (
                    configured_ids,
                    voice_profile_id,
                    user_id,
                    voice_profile_id,
                    voice_profile_id,
                    user_id,
                    persona_scope,
                    digital_human_id,
                    voice_profile_id,
                ),
                commit=True,
            )
        except Exception:
            existing = self.get_voice_clone_slot(voice_profile_id)
            if existing is not None and existing.get("userId") == user_id:
                return existing
            if existing is not None:
                return None
            raise
        return None if row is None else self._voice_clone_slot_payload(row)

    def get_voice_clone_slot(self, voice_profile_id: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone(
            """
            SELECT provider_speaker_id, voice_profile_id, user_id, persona_scope,
                digital_human_id, status, training_attempts, configured, assigned_at, updated_at
            FROM voice_clone_slots
            WHERE voice_profile_id = %s
            """,
            (voice_profile_id,),
        )
        return None if row is None else self._voice_clone_slot_payload(row)

    def list_voice_clone_slots(self) -> List[Dict[str, Any]]:
        rows = self._fetchall(
            """
            SELECT provider_speaker_id, voice_profile_id, user_id, persona_scope,
                digital_human_id, status, training_attempts, configured, assigned_at, updated_at
            FROM voice_clone_slots
            ORDER BY provider_speaker_id ASC
            """
        )
        return [self._voice_clone_slot_payload(row) for row in rows]

    def update_voice_clone_slot(
        self,
        voice_profile_id: str,
        *,
        status: str,
        increment_training_attempts: bool = False,
    ) -> Optional[Dict[str, Any]]:
        row = self._fetchone(
            """
            UPDATE voice_clone_slots
            SET status = %s,
                training_attempts = training_attempts + %s,
                updated_at = NOW()
            WHERE voice_profile_id = %s
            RETURNING provider_speaker_id, voice_profile_id, user_id, persona_scope,
                digital_human_id, status, training_attempts, configured, assigned_at, updated_at
            """,
            (status, 1 if increment_training_attempts else 0, voice_profile_id),
            commit=True,
        )
        return None if row is None else self._voice_clone_slot_payload(row)

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

    def _kb_operation_receipt_replay_cursor(
        self,
        cursor: Any,
        user_id: str,
        operation_id: str,
        *,
        operation_kind: str,
        payload_hash: str,
    ) -> Optional[Dict[str, Any]]:
        cursor.execute(
            """
            SELECT operation_kind, schema_version, payload_hash, result
            FROM kb_operation_receipts
            WHERE user_id = %s AND operation_id = %s
            """,
            (user_id, operation_id),
        )
        receipt = cursor.fetchone()
        if receipt is None:
            return None
        verify_knowledge_operation_receipt(
            {
                "operationKind": receipt["operation_kind"],
                "payloadHash": receipt["payload_hash"],
            },
            operation_kind=operation_kind,
            payload_hash=payload_hash,
        )
        result = deepcopy(receipt["result"])
        result["duplicate"] = True
        result["operationPayloadVerified"] = True
        return result

    def _insert_kb_operation_receipt_cursor(
        self,
        cursor: Any,
        user_id: str,
        operation_id: str,
        *,
        operation_kind: str,
        schema_version: int,
        payload_hash: str,
        result: Dict[str, Any],
    ) -> None:
        cursor.execute(
            """
            INSERT INTO kb_operation_receipts (
                user_id, operation_id, operation_kind, schema_version,
                payload_hash, result, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
            """,
            self._adapt_params(
                (
                    user_id,
                    operation_id,
                    operation_kind,
                    schema_version,
                    payload_hash,
                    result,
                )
            ),
        )

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
        self._connection = self._open_connection()
        return self._connection

    def _open_connection(self):
        if self._connection_factory is not None:
            return self._connection_factory()
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("psycopg is not installed. Run `pip install -r requirements.txt`.") from exc
        return psycopg.connect(self.dsn)

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
    def _close(connection: Any) -> None:
        close = getattr(connection, "close", None)
        if callable(close):
            close()

    @staticmethod
    def _iso_value(value: Any) -> str:
        if value is None:
            return PostgresStore._now()
        isoformat = getattr(value, "isoformat", None)
        return isoformat() if callable(isoformat) else str(value)

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

    def _update_digital_human_session_cursor(self, cursor: Any, lease: Dict[str, Any]) -> None:
        released_at = lease.get("releasedAt")
        cursor.execute(
            """
            UPDATE digital_human_sessions
            SET resource_key = %s,
                user_id = %s,
                device_id = %s,
                persona_id = %s,
                scene = %s,
                status = %s,
                payload = %s,
                heartbeat_at = %s,
                expires_at = %s,
                released_at = %s,
                updated_at = NOW()
            WHERE id = %s
            RETURNING payload
            """,
            self._adapt_params(
                (
                    lease.get("resourceKey"),
                    lease.get("userId"),
                    lease.get("deviceId"),
                    lease.get("personaId"),
                    lease.get("scene"),
                    lease.get("status"),
                    lease,
                    lease.get("heartbeatAt") or lease.get("createdAt"),
                    lease.get("expiresAt"),
                    released_at,
                    lease.get("sessionId"),
                )
            ),
        )
        cursor.fetchone()

    @staticmethod
    def _same_digital_human_context(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
        return all(
            left.get(key) == right.get(key)
            for key in ("resourceKey", "userId", "deviceId", "personaId", "scene", "lifecycleMode")
        )

    @staticmethod
    def _inactive_digital_human_outcome(lease: Dict[str, Any]) -> str:
        if lease.get("status") == "released":
            return "alreadyReleased"
        if lease.get("status") == "expired":
            return "expired"
        return "inactive"

    @staticmethod
    def _voice_clone_slot_payload(row: Dict[str, Any]) -> Dict[str, Any]:
        assigned_at = row.get("assigned_at")
        updated_at = row.get("updated_at")
        return {
            "providerSpeakerId": str(row.get("provider_speaker_id") or ""),
            "voiceProfileId": row.get("voice_profile_id"),
            "userId": row.get("user_id"),
            "personaScope": row.get("persona_scope"),
            "digitalHumanId": row.get("digital_human_id"),
            "status": str(row.get("status") or "available"),
            "trainingAttempts": int(row.get("training_attempts") or 0),
            "configured": bool(row.get("configured")),
            "assignedAt": assigned_at.isoformat() if hasattr(assigned_at, "isoformat") else assigned_at,
            "updatedAt": updated_at.isoformat() if hasattr(updated_at, "isoformat") else updated_at,
        }
