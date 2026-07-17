from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from math import ceil
import secrets
from typing import Any, Callable, Dict, List, Optional
import uuid

from psycopg.types.json import Jsonb

from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.db.pool import ConnectionPoolExhausted, FactoryConnectionPool, PsycopgConnectionPool
from app.db.readiness import PostgresReadinessProbe
from app.db.uow import DatabaseUnitOfWork, UnitOfWorkMetrics
from app.observability.events import (
    EvidenceEventConflict,
    canonicalize_evidence_event,
    hash_evidence_identifier,
    normalize_evidence_timestamp,
    normalize_machine_code,
    normalize_retention_class,
)
from app.services.user_identity import stable_user_id
from app.services.knowledge_store import (
    KB_OPERATION_ARCHIVE_DELETE,
    KB_OPERATION_MUTATION,
    KnowledgeRevisionConflict,
    apply_kb_mutation_v2,
    compact_knowledge_operation_receipt_result,
    is_compact_knowledge_operation_receipt_result,
    knowledge_operation_payload_fingerprint,
    normalize_kb_mutation_v2,
    rebuild_compact_knowledge_operation_result,
    verify_knowledge_operation_receipt,
)
from app.services.knowledge_privacy_maintenance import (
    KnowledgePrivacyMetadataError,
    canonical_receipt_payload_hash,
    canonicalize_persisted_knowledge_graph,
    canonicalize_persisted_knowledge_mutation,
    canonicalize_persisted_receipt_result,
)
from app.services.knowledge_receipt_maintenance import (
    compact_persisted_knowledge_receipt_result,
)
from app.services.archive_store import (
    ArchiveItemDeletionForbidden,
    ArchiveItemNotFound,
    ArchiveItemOwnershipConflict,
    ResourceOwnershipConflict,
    ResourceVersionConflict,
    is_sealed_time_letter,
)


class PostgresStore:
    _RESOURCE_AUTHORITY_TABLES = {
        "archiveItem": "archive_items",
        "digitalHumanSession": "digital_human_sessions",
        "familyMember": "family_members",
        "mailboxLetter": "mailbox_letters",
        "voiceProfile": "voice_profiles",
    }
    def __init__(
        self,
        dsn: str = None,
        connection_factory: Callable[[], Any] = None,
        pool: Any = None,
        pool_min_size: int = 1,
        pool_max_size: int = 10,
        pool_timeout_seconds: float = 5.0,
    ):
        self.dsn = dsn
        self._connection_factory = connection_factory
        if pool is not None:
            self._pool = pool
        elif connection_factory is not None:
            self._pool = FactoryConnectionPool(connection_factory)
        else:
            self._pool = PsycopgConnectionPool(
                dsn,
                min_size=pool_min_size,
                max_size=pool_max_size,
                timeout_seconds=pool_timeout_seconds,
            )
        self._pool_timeout_seconds = max(0.1, pool_timeout_seconds)
        self._uow_metrics = UnitOfWorkMetrics()
        self._current_uow: ContextVar[Optional[DatabaseUnitOfWork]] = ContextVar(
            f"dreamjourney_postgres_uow_{id(self)}",
            default=None,
        )

    def open_pool(self, *, wait: bool = True) -> None:
        self._pool.open(wait=wait)

    def close_pool(self) -> None:
        self._pool.close()

    @contextmanager
    def request_unit_of_work(
        self,
        *,
        correlation_id: str,
        command_id: str,
    ):
        existing = self._current_uow.get()
        if existing is not None:
            try:
                yield existing
            except Exception:
                existing.mark_rollback("nestedWorkUnitFailure")
                raise
            return
        candidate = DatabaseUnitOfWork(
            self._pool,
            self._uow_metrics,
            correlation_id=correlation_id,
            command_id=command_id,
            checkout_timeout_seconds=self._pool_timeout_seconds,
        )
        with candidate as active:
            token = self._current_uow.set(active)
            try:
                yield active
            finally:
                self._current_uow.reset(token)

    def uow_metrics(self) -> Dict[str, Any]:
        return {
            **self._uow_metrics.snapshot(),
            "pool": self._pool.stats(),
        }

    def readiness_probe(self) -> Dict[str, str]:
        migrator = PostgresMigrator(
            dsn=self.dsn or "postgres-readiness",
            migrations_dir=default_migrations_dir(),
            build_id="readiness",
        )
        return PostgresReadinessProbe(
            pool=self._pool,
            checkout_timeout_seconds=self._pool_timeout_seconds,
            schema_verifier=migrator.verify_connection,
        ).run()

    def append_evidence_event(
        self,
        event: Dict[str, Any],
        *,
        retention_class: str,
        expires_at_iso: Optional[str],
        legal_hold: bool = False,
    ) -> Dict[str, Any]:
        model, payload, payload_hash = canonicalize_evidence_event(event)
        normalized_retention_class = normalize_retention_class(retention_class)
        expires_at = (
            None
            if not expires_at_iso
            else normalize_evidence_timestamp(expires_at_iso)
        )
        row = self._fetchone(
            """
            INSERT INTO evidence_events (
                event_id, operation_id, event_type, schema_version,
                retention_class, expires_at, legal_hold, payload_hash,
                payload, occurred_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (event_id) DO NOTHING
            RETURNING event_id, payload_hash, retention_class, expires_at, legal_hold
            """,
            (
                model.eventId,
                model.operationId,
                model.type,
                model.schemaVersion,
                normalized_retention_class,
                expires_at,
                bool(legal_hold),
                payload_hash,
                payload,
                model.occurredAt.isoformat(),
            ),
            commit=True,
        )
        outcome = "appended"
        if row is None:
            row = self._fetchone(
                """
                SELECT event_id, payload_hash, retention_class, expires_at, legal_hold
                FROM evidence_events
                WHERE event_id = %s
                """,
                (model.eventId,),
            )
            outcome = "deduplicated"
        if row is None:
            raise RuntimeError("evidence event insert did not produce a receipt")
        stored_expires_at = self._iso_value(row.get("expires_at")) if row.get("expires_at") else None
        if any(
            (
                str(row.get("payload_hash") or "") != payload_hash,
                str(row.get("retention_class") or "") != normalized_retention_class,
                stored_expires_at != expires_at,
                bool(row.get("legal_hold")) != bool(legal_hold),
            )
        ):
            raise EvidenceEventConflict(
                "eventId already exists with different payload or retention metadata"
            )
        return {
            "outcome": outcome,
            "eventId": str(row.get("event_id") or model.eventId),
            "payloadHash": payload_hash,
            "retentionClass": normalized_retention_class,
            "expiresAt": expires_at,
            "legalHold": bool(legal_hold),
        }

    def summarize_evidence_events(
        self,
        *,
        operation: str,
        now_iso: Optional[str] = None,
        event_limit: int = 500,
    ) -> Dict[str, Any]:
        normalized_operation = normalize_machine_code(operation)
        now = self._parse_iso_datetime(now_iso or self._now()).astimezone(timezone.utc).isoformat()
        where_sql = """
            event_type = 'operation'
            AND payload->>'operation' = %s
            AND (legal_hold = TRUE OR expires_at IS NULL OR expires_at > %s)
        """
        aggregate = self._fetchone(
            f"""
            SELECT COUNT(*) AS event_count,
                   MIN(occurred_at) AS window_started_at,
                   MAX(occurred_at) AS window_ended_at
            FROM evidence_events
            WHERE {where_sql}
            """,
            (normalized_operation, now),
        ) or {}
        decision_rows = self._fetchall(
            f"""
            SELECT payload->>'decision' AS value, COUNT(*) AS count
            FROM evidence_events
            WHERE {where_sql}
            GROUP BY payload->>'decision'
            """,
            (normalized_operation, now),
        )
        feature_rows = self._fetchall(
            f"""
            SELECT payload->>'feature' AS value, COUNT(*) AS count
            FROM evidence_events
            WHERE {where_sql}
            GROUP BY payload->>'feature'
            """,
            (normalized_operation, now),
        )
        bounded_limit = max(1, min(event_limit, 500))
        event_rows = self._fetchall(
            f"""
            SELECT payload
            FROM evidence_events
            WHERE {where_sql}
            ORDER BY occurred_at DESC
            LIMIT %s
            """,
            (normalized_operation, now, bounded_limit),
        )
        events = [deepcopy(row["payload"]) for row in reversed(event_rows)]
        return {
            "eventCount": int(aggregate.get("event_count") or 0),
            "decisionCounts": self._evidence_group_counts(decision_rows),
            "featureCounts": self._evidence_group_counts(feature_rows),
            "windowStartedAt": self._iso_value(aggregate.get("window_started_at"))
            if aggregate.get("window_started_at") is not None
            else None,
            "windowEndedAt": self._iso_value(aggregate.get("window_ended_at"))
            if aggregate.get("window_ended_at") is not None
            else None,
            "events": events,
        }

    def expire_evidence_events(self, cutoff_iso: str) -> Dict[str, Any]:
        cutoff = normalize_evidence_timestamp(cutoff_iso)
        connection = self._open_connection()
        try:
            with connection.cursor(row_factory=self._dict_row_factory()) as cursor:
                cursor.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM evidence_events
                    WHERE legal_hold = TRUE AND expires_at IS NOT NULL AND expires_at <= %s
                    """,
                    (cutoff,),
                )
                held_row = cursor.fetchone() or {}
                cursor.execute(
                    """
                    DELETE FROM evidence_events
                    WHERE legal_hold = FALSE AND expires_at IS NOT NULL AND expires_at <= %s
                    RETURNING event_id
                    """,
                    (cutoff,),
                )
                expired_rows = cursor.fetchall()
            self._commit(connection)
        except Exception:
            self._rollback(connection)
            raise
        finally:
            self._close(connection)
        expired_ids = sorted(str(row.get("event_id") or "") for row in expired_rows)
        return {
            "schemaVersion": 1,
            "retentionAction": "expire",
            "cutoff": cutoff,
            "expiredCount": len(expired_ids),
            "heldCount": int(held_row.get("count") or 0),
            "expiredEventIdHashes": [
                hash_evidence_identifier(event_id) for event_id in expired_ids if event_id
            ],
        }

    @staticmethod
    def _evidence_group_counts(rows: List[Dict[str, Any]]) -> Dict[str, int]:
        return dict(
            sorted(
                (
                    str(row.get("value") or "unknown"),
                    int(row.get("count") or 0),
                )
                for row in rows
            )
        )

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

    @contextmanager
    def auth_user_operation(self, user_id: str):
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id:
            raise ValueError("auth user id is required")
        if self._current_uow.get() is None:
            with self.request_unit_of_work(
                correlation_id=f"auth-user-operation-{uuid.uuid4().hex}",
                command_id="authUserOperation",
            ):
                with self.auth_user_operation(normalized_user_id):
                    yield
            return
        active = self._current_uow.get()
        if active is None:  # pragma: no cover - guarded above
            raise RuntimeError("auth user operation requires a unit of work")
        with active.connection.cursor(row_factory=self._dict_row_factory()) as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s)) AS locked",
                (f"auth-user:{normalized_user_id}",),
            )
            cursor.fetchone()
        yield

    def create_auth_token_family(
        self,
        family: Dict[str, Any],
        session: Dict[str, Any],
        event: Dict[str, Any],
    ) -> Dict[str, Any]:
        if self._current_uow.get() is None:
            with self.request_unit_of_work(
                correlation_id=f"auth-issue-{uuid.uuid4().hex}",
                command_id="createAuthTokenFamily",
            ):
                return self.create_auth_token_family(family, session, event)
        active = self._current_uow.get()
        if active is None:  # pragma: no cover - guarded above
            raise RuntimeError("auth token family creation requires a unit of work")
        family_item = deepcopy(family)
        session_item = deepcopy(session)
        with active.connection.cursor(row_factory=self._dict_row_factory()) as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s)) AS locked",
                (f"auth-user:{family_item['userId']}",),
            )
            cursor.fetchone()
            cursor.execute(
                """
                INSERT INTO token_families (
                    id, user_id, status, current_session_version, contract_version,
                    created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    family_item["tokenFamilyId"],
                    family_item["userId"],
                    family_item["status"],
                    family_item["currentSessionVersion"],
                    family_item.get("contractVersion", 1),
                    family_item["createdAt"],
                    family_item["updatedAt"],
                ),
            )
            self._insert_auth_session_cursor(cursor, session_item)
            self._insert_auth_session_event_cursor(cursor, event)
        return deepcopy(session_item)

    def get_auth_session_by_access_token_hash(self, token_hash: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone(
            """
            SELECT a.payload, a.status, a.access_expires_at,
                   a.family_id, a.session_version,
                   COALESCE(f.status, 'legacy') AS family_status
            FROM auth_sessions AS a
            LEFT JOIN token_families AS f ON f.id = a.family_id
            WHERE a.access_token_hash = %s
            """,
            (token_hash,),
        )
        if row is None:
            return None
        item = deepcopy(row["payload"])
        item["status"] = str(row.get("status") or item.get("status") or "invalid")
        item["accessExpiresAt"] = self._iso_value(row.get("access_expires_at"))
        if row.get("family_id") is not None:
            item["tokenFamilyId"] = str(row["family_id"])
            item["sessionVersion"] = int(row.get("session_version") or 0)
            item["familyStatus"] = str(row.get("family_status") or "missing")
        return item

    def rotate_auth_session_refresh(
        self,
        refresh_token_hash: str,
        *,
        successor: Dict[str, Any],
        rotated_at_iso: str,
        rotation_receipt_id: str,
        reuse_receipt_id: str,
    ) -> Dict[str, Any]:
        if self._current_uow.get() is None:
            with self.request_unit_of_work(
                correlation_id=f"auth-rotate-{uuid.uuid4().hex}",
                command_id="rotateAuthSessionRefresh",
            ):
                return self.rotate_auth_session_refresh(
                    refresh_token_hash,
                    successor=successor,
                    rotated_at_iso=rotated_at_iso,
                    rotation_receipt_id=rotation_receipt_id,
                    reuse_receipt_id=reuse_receipt_id,
                )
        active = self._current_uow.get()
        if active is None:  # pragma: no cover - guarded above
            raise RuntimeError("auth refresh rotation requires a unit of work")
        with active.connection.cursor(row_factory=self._dict_row_factory()) as cursor:
            cursor.execute(
                """
                SELECT user_id, family_id, payload
                FROM auth_sessions
                WHERE refresh_token_hash = %s
                """,
                (refresh_token_hash,),
            )
            candidate = cursor.fetchone()
            if candidate is None:
                return {"outcome": "invalid"}
            family_id = str(candidate.get("family_id") or "")
            payload = deepcopy(candidate.get("payload") or {})
            if not family_id or int(payload.get("contractVersion") or 1) < 2:
                return {"outcome": "legacyReauthRequired"}
            user_id = str(candidate["user_id"])

            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s)) AS locked",
                (f"auth-user:{user_id}",),
            )
            cursor.fetchone()

            cursor.execute(
                """
                SELECT id, user_id, status, current_session_version
                FROM token_families
                WHERE id = %s
                FOR UPDATE
                """,
                (family_id,),
            )
            family = cursor.fetchone()
            if family is None:
                return {"outcome": "invalid"}
            cursor.execute(
                """
                SELECT id, user_id, family_id, session_version, status,
                       refresh_expires_at, payload
                FROM auth_sessions
                WHERE refresh_token_hash = %s
                FOR UPDATE
                """,
                (refresh_token_hash,),
            )
            row = cursor.fetchone()
            if row is None or str(row.get("family_id") or "") != family_id:
                return {"outcome": "invalid"}
            if str(family.get("user_id") or "") != str(row.get("user_id") or ""):
                return {"outcome": "invalid"}
            if str(row.get("status") or "") == "rotated":
                self._revoke_auth_family_cursor(
                    cursor,
                    family_id=family_id,
                    user_id=str(row["user_id"]),
                    revoked_at_iso=rotated_at_iso,
                    reason="refreshTokenReuse",
                    receipt_id=reuse_receipt_id,
                    event_type="refreshReuseDetected",
                    source_session_id=str(row["id"]),
                    source_session_version=int(row.get("session_version") or 0),
                )
                cursor.execute(
                    """
                    UPDATE auth_sessions
                    SET reuse_detected_at = %s, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (rotated_at_iso, row["id"]),
                )
                return {"outcome": "reuseDetected"}
            if (
                str(row.get("status") or "") != "active"
                or str(family.get("status") or "") != "active"
            ):
                return {"outcome": "invalid"}
            current_version = int(row.get("session_version") or 0)
            if current_version < 1 or int(
                family.get("current_session_version") or 0
            ) != current_version:
                return {"outcome": "invalid"}
            try:
                refresh_expires_at = self._parse_iso_datetime(
                    self._iso_value(row.get("refresh_expires_at"))
                )
                rotated_at = self._parse_iso_datetime(rotated_at_iso)
            except (TypeError, ValueError):
                return {"outcome": "invalid"}
            if refresh_expires_at <= rotated_at:
                cursor.execute(
                    """
                    UPDATE auth_sessions
                    SET status = 'expired',
                        payload = payload || %s,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    self._adapt_params(
                        ({"status": "expired", "expiredAt": rotated_at_iso}, row["id"])
                    ),
                )
                return {"outcome": "expired"}

            version = current_version + 1
            successor_item = deepcopy(successor)
            successor_item.update(
                {
                    "userId": str(row["user_id"]),
                    "tokenFamilyId": family_id,
                    "parentSessionId": str(row["id"]),
                    "sessionVersion": version,
                }
            )
            consumed_patch = {
                "status": "rotated",
                "rotatedAt": rotated_at_iso,
                "successorSessionId": successor_item["sessionId"],
            }
            cursor.execute(
                """
                UPDATE auth_sessions
                SET status = 'rotated', payload = payload || %s,
                    successor_session_id = %s, rotated_at = %s,
                    revoked_at = NULL, updated_at = NOW()
                WHERE id = %s AND status = 'active'
                  AND user_id = %s AND family_id = %s AND session_version = %s
                """,
                self._adapt_params(
                    (
                        consumed_patch,
                        successor_item["sessionId"],
                        rotated_at_iso,
                        row["id"],
                        row["user_id"],
                        family_id,
                        current_version,
                    )
                ),
            )
            if int(cursor.rowcount or 0) != 1:
                raise RuntimeError("auth refresh rotation lost its session lock")
            self._insert_auth_session_cursor(cursor, successor_item)
            cursor.execute(
                """
                UPDATE token_families
                SET current_session_version = %s, updated_at = %s
                WHERE id = %s AND status = 'active'
                  AND user_id = %s AND current_session_version = %s
                """,
                (
                    version,
                    rotated_at_iso,
                    family_id,
                    row["user_id"],
                    current_version,
                ),
            )
            if int(cursor.rowcount or 0) != 1:
                raise RuntimeError("auth token family changed during rotation")
            self._insert_auth_session_event_cursor(
                cursor,
                {
                    "eventId": rotation_receipt_id,
                    "tokenFamilyId": family_id,
                    "sessionId": successor_item["sessionId"],
                    "userId": successor_item["userId"],
                    "eventType": "sessionRotated",
                    "reason": "refreshConsumed",
                    "sessionVersion": version,
                    "occurredAt": rotated_at_iso,
                    "contractVersion": 1,
                },
            )
            return {"outcome": "rotated", "session": deepcopy(successor_item)}

    def revoke_auth_session_by_access_token_hash(
        self,
        access_token_hash: str,
        revoked_at_iso: str,
        reason: str,
        *,
        receipt_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        patch = {
            "status": "revoked",
            "revokedAt": revoked_at_iso,
            "revokeReason": reason,
        }
        if self._current_uow.get() is None:
            with self.request_unit_of_work(
                correlation_id=f"auth-revoke-{uuid.uuid4().hex}",
                command_id="revokeAuthSession",
            ):
                return self.revoke_auth_session_by_access_token_hash(
                    access_token_hash,
                    revoked_at_iso,
                    reason,
                    receipt_id=receipt_id,
                )
        active = self._current_uow.get()
        if active is None:  # pragma: no cover - guarded above
            raise RuntimeError("auth session revoke requires a unit of work")
        with active.connection.cursor(row_factory=self._dict_row_factory()) as cursor:
            cursor.execute(
                """
                SELECT id, user_id, family_id, session_version
                FROM auth_sessions
                WHERE access_token_hash = %s
                """,
                (access_token_hash,),
            )
            candidate = cursor.fetchone()
            if candidate is None:
                return None
            user_id = str(candidate["user_id"])
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s)) AS locked",
                (f"auth-user:{user_id}",),
            )
            cursor.fetchone()
            family_id = str(candidate.get("family_id") or "")
            if family_id:
                cursor.execute(
                    "SELECT id FROM token_families WHERE id = %s FOR UPDATE",
                    (family_id,),
                )
                if cursor.fetchone() is None:
                    return None
            cursor.execute(
                """
                UPDATE auth_sessions
                SET status = 'revoked', payload = payload || %s,
                    revoked_at = %s, revoke_reason = %s, updated_at = NOW()
                WHERE access_token_hash = %s AND user_id = %s AND status = 'active'
                RETURNING id, user_id, family_id, session_version, payload
                """,
                self._adapt_params(
                    (patch, revoked_at_iso, reason, access_token_hash, user_id)
                ),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            item = deepcopy(row["payload"])
            if receipt_id:
                self._insert_auth_session_event_cursor(
                    cursor,
                    {
                        "eventId": receipt_id,
                        "tokenFamilyId": row.get("family_id"),
                        "sessionId": row["id"],
                        "userId": row["user_id"],
                        "eventType": "sessionRevoked",
                        "reason": reason,
                        "sessionVersion": int(row.get("session_version") or 0),
                        "occurredAt": revoked_at_iso,
                        "contractVersion": 1,
                    },
                )
            return {
                **item,
                "scope": "session",
                "revocationReceiptId": receipt_id,
                "revokedSessionCount": 1,
                "revokedFamilyCount": 0,
            }

    def revoke_auth_token_family_by_access_token_hash(
        self,
        access_token_hash: str,
        revoked_at_iso: str,
        reason: str,
        *,
        receipt_id: str,
    ) -> Optional[Dict[str, Any]]:
        if self._current_uow.get() is None:
            with self.request_unit_of_work(
                correlation_id=f"auth-family-revoke-{uuid.uuid4().hex}",
                command_id="revokeAuthTokenFamily",
            ):
                return self.revoke_auth_token_family_by_access_token_hash(
                    access_token_hash,
                    revoked_at_iso,
                    reason,
                    receipt_id=receipt_id,
                )
        active = self._current_uow.get()
        if active is None:  # pragma: no cover - guarded above
            raise RuntimeError("auth family revoke requires a unit of work")
        with active.connection.cursor(row_factory=self._dict_row_factory()) as cursor:
            cursor.execute(
                """
                SELECT id, user_id, family_id, session_version
                FROM auth_sessions
                WHERE access_token_hash = %s
                """,
                (access_token_hash,),
            )
            session = cursor.fetchone()
            if session is None:
                return None
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s)) AS locked",
                (f"auth-user:{session['user_id']}",),
            )
            cursor.fetchone()
            family_id = str(session.get("family_id") or "")
            if not family_id:
                return self.revoke_auth_session_by_access_token_hash(
                    access_token_hash,
                    revoked_at_iso,
                    reason,
                    receipt_id=receipt_id,
                )
            cursor.execute(
                "SELECT id FROM token_families WHERE id = %s FOR UPDATE",
                (family_id,),
            )
            if cursor.fetchone() is None:
                return None
            return self._revoke_auth_family_cursor(
                cursor,
                family_id=family_id,
                user_id=str(session["user_id"]),
                revoked_at_iso=revoked_at_iso,
                reason=reason,
                receipt_id=receipt_id,
                event_type="familyRevoked",
                source_session_id=str(session["id"]),
                source_session_version=int(session.get("session_version") or 0),
            )

    def revoke_all_auth_token_families(
        self,
        user_id: str,
        revoked_at_iso: str,
        reason: str,
        *,
        receipt_id: str,
    ) -> Dict[str, Any]:
        if self._current_uow.get() is None:
            with self.request_unit_of_work(
                correlation_id=f"auth-all-revoke-{uuid.uuid4().hex}",
                command_id="revokeAllAuthTokenFamilies",
            ):
                return self.revoke_all_auth_token_families(
                    user_id,
                    revoked_at_iso,
                    reason,
                    receipt_id=receipt_id,
                )
        active = self._current_uow.get()
        if active is None:  # pragma: no cover - guarded above
            raise RuntimeError("all-device auth revoke requires a unit of work")
        revoked_family_count = 0
        revoked_session_count = 0
        with active.connection.cursor(row_factory=self._dict_row_factory()) as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s)) AS locked",
                (f"auth-user:{user_id}",),
            )
            cursor.fetchone()
            cursor.execute(
                """
                SELECT id, current_session_version
                FROM token_families
                WHERE user_id = %s AND status = 'active'
                ORDER BY id
                FOR UPDATE
                """,
                (user_id,),
            )
            families = cursor.fetchall()
            for index, family in enumerate(families):
                result = self._revoke_auth_family_cursor(
                    cursor,
                    family_id=str(family["id"]),
                    user_id=user_id,
                    revoked_at_iso=revoked_at_iso,
                    reason=reason,
                    receipt_id=f"{receipt_id}_{index + 1}",
                    event_type="allDevicesRevoked",
                    source_session_id=None,
                    source_session_version=int(family.get("current_session_version") or 0),
                )
                revoked_family_count += int(result["revokedFamilyCount"])
                revoked_session_count += int(result["revokedSessionCount"])
            legacy_patch = {
                "status": "revoked",
                "revokedAt": revoked_at_iso,
                "revokeReason": reason,
            }
            cursor.execute(
                """
                UPDATE auth_sessions
                SET status = 'revoked', payload = payload || %s,
                    revoked_at = %s, revoke_reason = %s, updated_at = NOW()
                WHERE user_id = %s AND family_id IS NULL AND status = 'active'
                """,
                self._adapt_params((legacy_patch, revoked_at_iso, reason, user_id)),
            )
            revoked_session_count += int(cursor.rowcount or 0)
            self._insert_auth_session_event_cursor(
                cursor,
                {
                    "eventId": receipt_id,
                    "tokenFamilyId": None,
                    "sessionId": None,
                    "userId": user_id,
                    "eventType": "allDevicesRevoked",
                    "reason": reason,
                    "sessionVersion": 0,
                    "occurredAt": revoked_at_iso,
                    "contractVersion": 1,
                },
            )
        return {
            "scope": "allDevices",
            "userId": user_id,
            "revocationReceiptId": receipt_id,
            "revokedFamilyCount": revoked_family_count,
            "revokedSessionCount": revoked_session_count,
            "revokedAt": revoked_at_iso,
            "reason": reason,
            "contractVersion": 1,
        }

    def list_auth_session_events(self, token_family_id: str) -> List[Dict[str, Any]]:
        rows = self._fetchall(
            """
            SELECT id, family_id, session_id, user_id, event_type, reason,
                   session_version, contract_version, occurred_at
            FROM session_events
            WHERE family_id = %s
            ORDER BY occurred_at, id
            """,
            (token_family_id,),
        )
        return [
            {
                "eventId": str(row["id"]),
                "tokenFamilyId": str(row.get("family_id") or ""),
                "sessionId": row.get("session_id"),
                "userId": str(row["user_id"]),
                "eventType": str(row["event_type"]),
                "reason": str(row["reason"]),
                "sessionVersion": int(row.get("session_version") or 0),
                "contractVersion": int(row.get("contract_version") or 1),
                "occurredAt": self._iso_value(row.get("occurred_at")),
            }
            for row in rows
        ]

    def _insert_auth_session_cursor(self, cursor: Any, item: Dict[str, Any]) -> None:
        cursor.execute(
            """
            INSERT INTO auth_sessions (
                id, user_id, access_token_hash, refresh_token_hash, status, payload,
                access_expires_at, refresh_expires_at, family_id, parent_session_id,
                session_version, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            """,
            self._adapt_params(
                (
                    item["sessionId"],
                    item["userId"],
                    item["accessTokenHash"],
                    item["refreshTokenHash"],
                    item["status"],
                    item,
                    item["accessExpiresAt"],
                    item["refreshExpiresAt"],
                    item["tokenFamilyId"],
                    item.get("parentSessionId"),
                    item["sessionVersion"],
                    item["createdAt"],
                )
            ),
        )

    def _insert_auth_session_event_cursor(self, cursor: Any, event: Dict[str, Any]) -> None:
        cursor.execute(
            """
            INSERT INTO session_events (
                id, family_id, session_id, user_id, event_type, reason,
                session_version, contract_version, occurred_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                event["eventId"],
                event.get("tokenFamilyId"),
                event.get("sessionId"),
                event["userId"],
                event["eventType"],
                event["reason"],
                int(event.get("sessionVersion") or 0),
                int(event.get("contractVersion") or 1),
                event["occurredAt"],
            ),
        )

    def _revoke_auth_family_cursor(
        self,
        cursor: Any,
        *,
        family_id: str,
        user_id: str,
        revoked_at_iso: str,
        reason: str,
        receipt_id: str,
        event_type: str,
        source_session_id: Optional[str],
        source_session_version: int,
    ) -> Dict[str, Any]:
        cursor.execute(
            """
            UPDATE token_families
            SET status = 'revoked', revoked_at = %s, revoke_reason = %s,
                updated_at = %s
            WHERE id = %s AND status = 'active'
            """,
            (revoked_at_iso, reason, revoked_at_iso, family_id),
        )
        revoked_family_count = int(cursor.rowcount or 0)
        patch = {
            "status": "revoked",
            "revokedAt": revoked_at_iso,
            "revokeReason": reason,
        }
        cursor.execute(
            """
            UPDATE auth_sessions
            SET status = 'revoked', payload = payload || %s,
                revoked_at = %s, revoke_reason = %s, updated_at = NOW()
            WHERE family_id = %s AND status = 'active'
            """,
            self._adapt_params((patch, revoked_at_iso, reason, family_id)),
        )
        revoked_session_count = int(cursor.rowcount or 0)
        if revoked_family_count:
            self._insert_auth_session_event_cursor(
                cursor,
                {
                    "eventId": receipt_id,
                    "tokenFamilyId": family_id,
                    "sessionId": source_session_id,
                    "userId": user_id,
                    "eventType": event_type,
                    "reason": reason,
                    "sessionVersion": source_session_version,
                    "occurredAt": revoked_at_iso,
                    "contractVersion": 1,
                },
            )
        return {
            "scope": "family",
            "tokenFamilyId": family_id,
            "userId": user_id,
            "revocationReceiptId": receipt_id,
            "revokedFamilyCount": revoked_family_count,
            "revokedSessionCount": revoked_session_count,
            "revokedAt": revoked_at_iso,
            "reason": reason,
            "contractVersion": 1,
        }

    def ensure_identity_hash_key_version(
        self,
        version: str,
        fingerprint: str,
    ) -> Dict[str, Any]:
        if self._current_uow.get() is None:
            with self.request_unit_of_work(
                correlation_id=f"identity-key-{uuid.uuid4().hex}",
                command_id="ensureIdentityHashKeyVersion",
            ):
                return self.ensure_identity_hash_key_version(version, fingerprint)
        active = self._current_uow.get()
        if active is None:  # pragma: no cover - guarded above
            raise RuntimeError("identity key registration requires a unit of work")
        with active.connection.cursor(row_factory=self._dict_row_factory()) as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s)) AS locked",
                ("dreamjourney-identity-hash-key-version:v1",),
            )
            cursor.fetchone()
            cursor.execute(
                """
                SELECT version, key_fingerprint, status
                FROM identity_hash_key_versions
                ORDER BY created_at ASC
                FOR UPDATE
                """
            )
            rows = cursor.fetchall()
            if rows:
                matching = next(
                    (
                        row
                        for row in rows
                        if str(row.get("version") or "") == version
                    ),
                    None,
                )
                if (
                    matching is not None
                    and matching.get("status") == "active"
                    and secrets.compare_digest(
                        str(matching.get("key_fingerprint") or ""),
                        fingerprint,
                    )
                    and len(rows) == 1
                ):
                    return {"outcome": "ready", "version": version}
                return {"outcome": "conflict", "version": version}
            cursor.execute(
                """
                INSERT INTO identity_hash_key_versions (
                    version, key_fingerprint, status, created_at, updated_at
                )
                VALUES (%s, %s, 'active', NOW(), NOW())
                """,
                (version, fingerprint),
            )
        return {"outcome": "ready", "version": version}

    def save_auth_challenge(self, challenge: Dict[str, Any]) -> Dict[str, Any]:
        row = self._fetchone(
            """
            INSERT INTO auth_challenges (
                id, identity_type, target_hash_key_version, target_hash,
                code_hash, provider_mode,
                purpose, status, attempts, max_attempts,
                internal_verification_enabled, expires_at, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, identity_type, target_hash_key_version, target_hash,
                      code_hash, provider_mode,
                      purpose, status, attempts, max_attempts,
                      internal_verification_enabled, expires_at, created_at,
                      consumed_at, updated_at
            """,
            (
                challenge["challengeId"],
                challenge["identityType"],
                challenge["targetHashKeyVersion"],
                challenge["targetHash"],
                challenge["codeHash"],
                challenge["providerMode"],
                challenge["purpose"],
                challenge["status"],
                challenge["attempts"],
                challenge["maxAttempts"],
                challenge["internalVerificationEnabled"],
                challenge["expiresAt"],
                challenge["createdAt"],
                challenge["createdAt"],
            ),
            commit=True,
        )
        if row is None:
            raise RuntimeError("identity challenge insert returned no row")
        return self._auth_challenge_record(row)

    def get_auth_challenge(self, challenge_id: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone(
            """
            SELECT id, identity_type, target_hash_key_version, target_hash,
                   code_hash, provider_mode,
                   purpose, status, attempts, max_attempts,
                   internal_verification_enabled, expires_at, created_at,
                   consumed_at, updated_at
            FROM auth_challenges
            WHERE id = %s
            """,
            (challenge_id,),
        )
        return None if row is None else self._auth_challenge_record(row)

    def get_latest_auth_challenge(
        self,
        *,
        identity_type: str,
        target_hash_key_version: str,
        target_hash: str,
        purpose: str,
    ) -> Optional[Dict[str, Any]]:
        query = """
            SELECT id, identity_type, target_hash_key_version, target_hash,
                   code_hash, provider_mode,
                   purpose, status, attempts, max_attempts,
                   internal_verification_enabled, expires_at, created_at,
                   consumed_at, updated_at
            FROM auth_challenges
            WHERE identity_type = %s
              AND target_hash_key_version = %s
              AND target_hash = %s
              AND purpose = %s
            ORDER BY created_at DESC
            LIMIT 1
            """
        params = (identity_type, target_hash_key_version, target_hash, purpose)
        active = self._current_uow.get()
        if active is None:
            row = self._fetchone(query, params)
        else:
            with active.connection.cursor(row_factory=self._dict_row_factory()) as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s)) AS locked",
                    (
                        f"identity-challenge:{identity_type}:"
                        f"{target_hash_key_version}:{target_hash}:{purpose}",
                    ),
                )
                cursor.fetchone()
                cursor.execute(query, params)
                row = cursor.fetchone()
        return None if row is None else self._auth_challenge_record(row)

    def verify_auth_challenge(
        self,
        challenge_id: str,
        *,
        code_hash: str,
        attempted_at_iso: str,
        subject_id: str,
        binding_id: str,
        proof_id: str,
    ) -> Dict[str, Any]:
        if self._current_uow.get() is None:
            with self.request_unit_of_work(
                correlation_id=f"identity-verify-{uuid.uuid4().hex}",
                command_id="verifyIdentityChallenge",
            ):
                return self.verify_auth_challenge(
                    challenge_id,
                    code_hash=code_hash,
                    attempted_at_iso=attempted_at_iso,
                    subject_id=subject_id,
                    binding_id=binding_id,
                    proof_id=proof_id,
                )

        attempted_at = self._parse_iso_datetime(attempted_at_iso)
        active = self._current_uow.get()
        if active is None:  # pragma: no cover - guarded by the unit of work above
            raise RuntimeError("identity verification requires a unit of work")
        with active.connection.cursor(row_factory=self._dict_row_factory()) as cursor:
            cursor.execute(
                """
                SELECT id, identity_type, target_hash_key_version, target_hash,
                       code_hash, provider_mode,
                       purpose, status, attempts, max_attempts,
                       internal_verification_enabled, expires_at
                FROM auth_challenges
                WHERE id = %s
                FOR UPDATE
                """,
                (challenge_id,),
            )
            challenge = cursor.fetchone()
            if challenge is None:
                return {"outcome": "missing"}
            if challenge.get("status") != "active":
                return {"outcome": "inactive"}

            expires_at = self._parse_iso_datetime(
                self._iso_value(challenge.get("expires_at"))
            )
            if expires_at <= attempted_at:
                cursor.execute(
                    """
                    UPDATE auth_challenges
                    SET status = 'expired', updated_at = %s
                    WHERE id = %s
                    """,
                    (attempted_at_iso, challenge_id),
                )
                return {"outcome": "expired"}

            attempts = int(challenge.get("attempts") or 0) + 1
            code_matches = bool(
                challenge.get("internal_verification_enabled")
                and secrets.compare_digest(
                    str(challenge.get("code_hash") or ""),
                    code_hash,
                )
            )
            if not code_matches:
                status = (
                    "locked"
                    if attempts >= int(challenge.get("max_attempts") or 1)
                    else "active"
                )
                cursor.execute(
                    """
                    UPDATE auth_challenges
                    SET attempts = %s, status = %s, updated_at = %s
                    WHERE id = %s
                    """,
                    (attempts, status, attempted_at_iso, challenge_id),
                )
                return {"outcome": "invalid"}

            identity_type = str(challenge["identity_type"])
            target_hash_key_version = str(challenge["target_hash_key_version"])
            target_hash = str(challenge["target_hash"])
            provider_mode = str(challenge["provider_mode"])
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s)) AS locked",
                (f"identity:{identity_type}:{target_hash_key_version}:{target_hash}",),
            )
            cursor.fetchone()
            cursor.execute(
                """
                SELECT b.id, b.subject_id, b.status AS binding_status,
                       s.status AS subject_status
                FROM identity_bindings AS b
                JOIN subjects AS s ON s.id = b.subject_id
                WHERE b.identity_type = %s
                  AND b.target_hash_key_version = %s
                  AND b.target_hash = %s
                FOR UPDATE OF b, s
                """,
                (identity_type, target_hash_key_version, target_hash),
            )
            binding = cursor.fetchone()
            if binding is None:
                cursor.execute(
                    """
                    INSERT INTO subjects (id, status, created_at, updated_at)
                    VALUES (%s, 'active', %s, %s)
                    """,
                    (subject_id, attempted_at_iso, attempted_at_iso),
                )
                cursor.execute(
                    """
                    INSERT INTO identity_bindings (
                        id, subject_id, identity_type, target_hash_key_version,
                        target_hash,
                        provider_mode, status, verified_at, created_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, 'active', %s, %s, %s)
                    """,
                    (
                        binding_id,
                        subject_id,
                        identity_type,
                        target_hash_key_version,
                        target_hash,
                        provider_mode,
                        attempted_at_iso,
                        attempted_at_iso,
                        attempted_at_iso,
                    ),
                )
            else:
                if (
                    binding.get("binding_status") != "active"
                    or binding.get("subject_status") != "active"
                ):
                    return {"outcome": "identityDisabled"}
                binding_id = str(binding["id"])
                subject_id = str(binding["subject_id"])
                cursor.execute(
                    """
                    UPDATE identity_bindings
                    SET provider_mode = %s, verified_at = %s, updated_at = %s
                    WHERE id = %s
                    """,
                    (provider_mode, attempted_at_iso, attempted_at_iso, binding_id),
                )

            cursor.execute(
                """
                INSERT INTO identity_proofs (
                    id, challenge_id, binding_id, subject_id, provider_mode,
                    verified_at, contract_version, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, 1, %s)
                """,
                (
                    proof_id,
                    challenge_id,
                    binding_id,
                    subject_id,
                    provider_mode,
                    attempted_at_iso,
                    attempted_at_iso,
                ),
            )
            cursor.execute(
                """
                UPDATE auth_challenges
                SET attempts = %s, status = 'consumed', consumed_at = %s,
                    updated_at = %s
                WHERE id = %s
                """,
                (attempts, attempted_at_iso, attempted_at_iso, challenge_id),
            )
        return {
            "outcome": "verified",
            "subjectId": subject_id,
            "bindingId": binding_id,
            "proofReceiptId": proof_id,
            "verifiedAt": attempted_at_iso,
        }

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
        if user.get("deletionState") == "purged":
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
              AND COALESCE(payload->>'deletionState', 'active') <> 'purged'
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
        if user.get("deletionState") != "softDeleted":
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
            WHERE id = %s AND payload->>'deletionState' = 'softDeleted'
            RETURNING payload
            """,
            (phone, item["nickname"], item, user_id),
            commit=True,
        )
        return None if row is None else deepcopy(row["payload"])

    def purge_expired_deleted_users(self, cutoff_iso: str) -> List[Dict[str, Any]]:
        if self._current_uow.get() is None:
            with self.request_unit_of_work(
                correlation_id=f"account-purge-{uuid.uuid4().hex}",
                command_id="purgeExpiredDeletedUsers",
            ):
                return self.purge_expired_deleted_users(cutoff_iso)
        cutoff = self._parse_iso_datetime(cutoff_iso)
        rows = self._fetchall(
            """
            SELECT id, payload FROM users
            WHERE payload->>'deletionState' = 'softDeleted'
            ORDER BY id
            """,
        )
        purged: List[Dict[str, Any]] = []
        for row in rows:
            user_id = str(row.get("id") or row["payload"].get("id") or "")
            user = deepcopy(row["payload"])
            deadline = self._parse_iso_datetime(str(user.get("restoreDeadline") or user.get("purgeAfter") or ""))
            if not user_id or deadline > cutoff:
                continue
            self._fetchone(
                "SELECT pg_advisory_xact_lock(hashtext(%s)) AS locked",
                (f"auth-user:{user_id}",),
            )
            self._fetchone(
                "SELECT pg_advisory_xact_lock(hashtext(%s)) AS locked",
                (f"knowledge:{user_id}",),
            )
            locked_user = self._fetchone(
                "SELECT id, payload FROM users WHERE id = %s FOR UPDATE",
                (user_id,),
            )
            if locked_user is None:
                continue
            user = deepcopy(locked_user["payload"])
            deadline = self._parse_iso_datetime(
                str(user.get("restoreDeadline") or user.get("purgeAfter") or "")
            )
            if user.get("deletionState") != "softDeleted" or deadline > cutoff:
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
            self._fetchall(
                "DELETE FROM kb_change_feed_state WHERE user_id = %s RETURNING user_id",
                (user_id,),
            )
            self._fetchall(
                "DELETE FROM session_events WHERE user_id = %s RETURNING id",
                (user_id,),
            )
            self._fetchall(
                "DELETE FROM token_families WHERE user_id = %s RETURNING id",
                (user_id,),
            )
            self._fetchall(
                "DELETE FROM auth_sessions WHERE user_id = %s RETURNING payload",
                (user_id,),
            )
            self._fetchone(
                """
                SELECT deleted_grant_events, deleted_access_grants, deleted_relationships
                FROM purge_delegated_access_for_subject(%s)
                """,
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
            ):
                self._fetchall(
                    f"DELETE FROM {table} WHERE user_id = %s RETURNING user_id",
                    (user_id,),
                )
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
        receipt_governance_summary: Optional[Dict[str, Any]] = None,
        record_compatibility_noop_receipt: bool = True,
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
                cursor.execute(
                    """
                    INSERT INTO kb_change_feed_state (user_id, minimum_since_revision, updated_at)
                    VALUES (%s, 0, NOW())
                    ON CONFLICT (user_id) DO NOTHING
                    """,
                    (user_id,),
                )
                receipt_result = self._kb_operation_receipt_replay_cursor(
                    cursor,
                    user_id,
                    operation_id,
                    operation_kind=operation_kind,
                    payload_hash=payload_hash,
                )
                if receipt_result is not None:
                    self._commit(connection)
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
                    self._commit(connection)
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
                        if record_compatibility_noop_receipt:
                            self._insert_kb_operation_receipt_cursor(
                                cursor,
                                user_id,
                                operation_id,
                                operation_kind=operation_kind,
                                schema_version=schema_version,
                                payload_hash=payload_hash,
                                result=result,
                                governance_summary=receipt_governance_summary,
                            )
                        self._commit(connection)
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
                    governance_summary=receipt_governance_summary,
                )
            self._commit(connection)
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
            receipt_result = receipt["result"]
            if is_compact_knowledge_operation_receipt_result(receipt_result):
                existing = self._fetchone(
                    """
                    SELECT revision, graph, mutation, created_at
                    FROM kb_changes
                    WHERE user_id = %s AND operation_id = %s
                    """,
                    (user_id, operation_id),
                )
                snapshot = None
                if existing is None:
                    snapshot_row = self._fetchone(
                        """
                        SELECT graph, revision, updated_at
                        FROM kb_snapshots
                        WHERE user_id = %s
                        """,
                        (user_id,),
                    )
                    if snapshot_row is not None:
                        snapshot = {
                            "graph": deepcopy(snapshot_row.get("graph") or {}),
                            "revision": int(snapshot_row.get("revision") or 0),
                            "updatedAt": self._iso_value(snapshot_row.get("updated_at")),
                        }
                result = rebuild_compact_knowledge_operation_result(
                    receipt_result,
                    user_id=user_id,
                    operation_id=operation_id,
                    change=self._kb_change_replay_row(existing),
                    snapshot=snapshot,
                )
            else:
                result = deepcopy(receipt_result)
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

    def get_kb_change_page(
        self,
        user_id: str,
        since_revision: int,
        through_revision: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        connection = self._open_connection()
        try:
            with connection.cursor(row_factory=self._dict_row_factory()) as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (f"knowledge:{user_id}",),
                )
                cursor.execute(
                    """
                    SELECT minimum_since_revision
                    FROM kb_change_feed_state
                    WHERE user_id = %s
                    """,
                    (user_id,),
                )
                state = cursor.fetchone()
                cursor.execute(
                    """
                    SELECT graph, revision, updated_at
                    FROM kb_snapshots
                    WHERE user_id = %s
                    """,
                    (user_id,),
                )
                snapshot = cursor.fetchone()
                rows = self._list_kb_change_rows_cursor(
                    cursor,
                    user_id,
                    since_revision,
                    through_revision=through_revision,
                    limit=limit,
                )
            self._commit(connection)
            return {
                "currentRevision": int((snapshot or {}).get("revision") or 0),
                "minimumSinceRevision": int(
                    (state or {}).get("minimum_since_revision") or 0
                ),
                "changes": self._kb_change_rows_payload(rows),
            }
        except Exception:
            self._rollback(connection)
            raise
        finally:
            self._close(connection)

    def maintain_kb_change_feed_compaction(
        self,
        *,
        keep_recent_revisions: int = 1000,
        keep_days: int = 30,
        apply: bool = False,
        now: Optional[datetime] = None,
        lock_timeout_ms: int = 5000,
        statement_timeout_ms: int = 30000,
    ) -> Dict[str, Any]:
        if (
            isinstance(keep_recent_revisions, bool)
            or not isinstance(keep_recent_revisions, int)
            or keep_recent_revisions < 1
        ):
            raise ValueError("keep_recent_revisions must be a positive integer")
        if isinstance(keep_days, bool) or not isinstance(keep_days, int) or keep_days < 1:
            raise ValueError("keep_days must be a positive integer")
        if (
            isinstance(lock_timeout_ms, bool)
            or not isinstance(lock_timeout_ms, int)
            or lock_timeout_ms < 1
        ):
            raise ValueError("lock_timeout_ms must be a positive integer")
        if (
            isinstance(statement_timeout_ms, bool)
            or not isinstance(statement_timeout_ms, int)
            or statement_timeout_ms < 1
        ):
            raise ValueError("statement_timeout_ms must be a positive integer")

        now_value = now or datetime.now(timezone.utc)
        if now_value.tzinfo is None or now_value.utcoffset() is None:
            raise ValueError("now must include a timezone")
        now_value = now_value.astimezone(timezone.utc)
        created_at_cutoff = now_value - timedelta(days=keep_days)
        report = {
            "schemaVersion": 1,
            "mode": "apply" if apply else "dryRun",
            "status": "ok",
            "retention": {
                "keepRecentRevisions": keep_recent_revisions,
                "keepDays": keep_days,
                "createdAtCutoff": created_at_cutoff.isoformat(),
                "policy": "union",
            },
            "timeouts": {
                "lockTimeoutMilliseconds": lock_timeout_ms,
                "statementTimeoutMilliseconds": statement_timeout_ms,
            },
            "compactorAlreadyRunning": False,
            "scannedUsers": 0,
            "processedUsers": 0,
            "skippedUsers": 0,
            "skipReasons": {
                "lockTimeout": 0,
                "statementTimeout": 0,
            },
            "scannedChanges": 0,
            "plannedChanges": 0,
            "deletedChanges": 0,
            "plannedFloorAdvances": 0,
            "advancedUsers": 0,
            "legacyBarriers": 0,
            "retentionBarriers": 0,
            "revisionGapBarriers": 0,
        }

        coordinator = self._open_connection()
        session_lock_acquired = False
        try:
            with coordinator.cursor(row_factory=self._dict_row_factory()) as cursor:
                cursor.execute(
                    "SELECT pg_try_advisory_lock(hashtext(%s)) AS locked",
                    ("knowledge-change-feed-compaction:v1",),
                )
                lock_row = cursor.fetchone()
                session_lock_acquired = bool((lock_row or {}).get("locked"))
                if not session_lock_acquired:
                    report["status"] = "skipped"
                    report["compactorAlreadyRunning"] = True
                    self._rollback(coordinator)
                    return report
                cursor.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (f"{statement_timeout_ms}ms",),
                )
                cursor.execute(
                    """
                    SELECT user_id FROM (
                        SELECT user_id FROM kb_snapshots
                        UNION
                        SELECT user_id FROM kb_changes
                        UNION
                        SELECT user_id FROM kb_change_feed_state
                    ) AS knowledge_users
                    ORDER BY user_id
                    """
                )
                users = cursor.fetchall()
                report["scannedUsers"] = len(users)
            self._commit(coordinator)

            for user in users:
                user_id = str(user["user_id"])
                connection = self._open_connection()
                try:
                    with connection.cursor(row_factory=self._dict_row_factory()) as cursor:
                        cursor.execute(
                            "SELECT set_config('lock_timeout', %s, true)",
                            (f"{lock_timeout_ms}ms",),
                        )
                        cursor.execute(
                            "SELECT set_config('statement_timeout', %s, true)",
                            (f"{statement_timeout_ms}ms",),
                        )
                        cursor.execute(
                            "SELECT pg_advisory_xact_lock(hashtext(%s))",
                            (f"knowledge:{user_id}",),
                        )
                        user_report = self._compact_kb_change_feed_user_cursor(
                            cursor,
                            user_id=user_id,
                            keep_recent_revisions=keep_recent_revisions,
                            created_at_cutoff=created_at_cutoff,
                            apply=apply,
                        )
                    if apply:
                        self._commit(connection)
                    else:
                        self._rollback(connection)
                except Exception as exc:
                    self._rollback(connection)
                    timeout_reason = self._postgres_timeout_reason(exc)
                    if timeout_reason is None:
                        raise
                    report["skippedUsers"] += 1
                    report["skipReasons"][timeout_reason] += 1
                    continue
                finally:
                    self._close(connection)

                report["processedUsers"] += 1
                for key in (
                    "scannedChanges",
                    "plannedChanges",
                    "deletedChanges",
                    "plannedFloorAdvances",
                    "advancedUsers",
                    "legacyBarriers",
                    "retentionBarriers",
                    "revisionGapBarriers",
                ):
                    report[key] += user_report[key]
            if report["skippedUsers"]:
                report["status"] = "partial"
            return report
        except Exception:
            self._rollback(coordinator)
            raise
        finally:
            if session_lock_acquired:
                try:
                    with coordinator.cursor(row_factory=self._dict_row_factory()) as cursor:
                        cursor.execute(
                            "SELECT pg_advisory_unlock(hashtext(%s)) AS unlocked",
                            ("knowledge-change-feed-compaction:v1",),
                        )
                        cursor.fetchone()
                    self._commit(coordinator)
                except Exception:
                    self._rollback(coordinator)
            self._close(coordinator)

    def maintain_knowledge_operation_receipts(
        self,
        keep_days: int = 30,
        batch_size: int = 100,
        apply: bool = False,
        lock_timeout_ms: int = 5000,
        statement_timeout_ms: int = 30000,
    ) -> Dict[str, Any]:
        if (
            isinstance(keep_days, bool)
            or not isinstance(keep_days, int)
            or keep_days < 0
        ):
            raise ValueError("keep_days must be a non-negative integer")
        for name, value in (
            ("batch_size", batch_size),
            ("lock_timeout_ms", lock_timeout_ms),
            ("statement_timeout_ms", statement_timeout_ms),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if not isinstance(apply, bool):
            raise ValueError("apply must be a boolean")

        created_at_cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
        report: Dict[str, Any] = {
            "schemaVersion": 1,
            "mode": "apply" if apply else "dryRun",
            "status": "ok",
            "retention": {
                "keepDays": keep_days,
                "createdAtCutoff": created_at_cutoff.isoformat(),
            },
            "batchSize": batch_size,
            "timeouts": {
                "lockTimeoutMilliseconds": lock_timeout_ms,
                "statementTimeoutMilliseconds": statement_timeout_ms,
            },
            "scannedUsers": 0,
            "processedUsers": 0,
            "failedUsers": 0,
            "failureReasons": {
                "lockTimeout": 0,
                "statementTimeout": 0,
                "error": 0,
            },
            **self._new_knowledge_receipt_maintenance_counts(),
        }

        last_user_id: Optional[str] = None
        while True:
            users = self._knowledge_receipt_maintenance_user_page(
                created_at_cutoff=created_at_cutoff,
                last_user_id=last_user_id,
                limit=batch_size,
                statement_timeout_ms=statement_timeout_ms,
            )
            if not users:
                break
            report["scannedUsers"] += len(users)

            for user in users:
                user_id = str(user["user_id"])
                user_report = self._new_knowledge_receipt_maintenance_counts()
                connection = self._open_connection()
                try:
                    with connection.cursor(row_factory=self._dict_row_factory()) as cursor:
                        cursor.execute(
                            "SELECT set_config('statement_timeout', %s, true)",
                            (f"{statement_timeout_ms}ms",),
                        )
                        cursor.execute(
                            "SELECT set_config('lock_timeout', %s, true)",
                            (f"{lock_timeout_ms}ms",),
                        )
                        cursor.execute(
                            "SELECT pg_advisory_xact_lock(hashtext(%s))",
                            (f"knowledge:{user_id}",),
                        )
                        self._maintain_knowledge_operation_receipts_user_cursor(
                            cursor,
                            user_id=user_id,
                            created_at_cutoff=created_at_cutoff,
                            batch_size=batch_size,
                            apply=apply,
                            report=user_report,
                        )
                    if apply:
                        self._commit(connection)
                    else:
                        self._rollback(connection)
                except Exception as exc:
                    self._rollback(connection)
                    self._mark_knowledge_receipt_user_report_failed(user_report)
                    self._merge_knowledge_receipt_maintenance_counts(report, user_report)
                    report["failedUsers"] += 1
                    reason = self._postgres_timeout_reason(exc) or "error"
                    report["failureReasons"][reason] += 1
                    report["status"] = "partial"
                    continue
                finally:
                    self._close(connection)

                report["processedUsers"] += 1
                self._merge_knowledge_receipt_maintenance_counts(report, user_report)

            last_user_id = str(users[-1]["user_id"])
            if len(users) < batch_size:
                break

        report["byKind"] = dict(sorted(report["byKind"].items()))
        return report

    def _knowledge_receipt_maintenance_user_page(
        self,
        *,
        created_at_cutoff: datetime,
        last_user_id: Optional[str],
        limit: int,
        statement_timeout_ms: int,
    ) -> List[Dict[str, Any]]:
        connection = self._open_connection()
        try:
            with connection.cursor(row_factory=self._dict_row_factory()) as cursor:
                cursor.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (f"{statement_timeout_ms}ms",),
                )
                if last_user_id is None:
                    cursor.execute(
                        """
                        SELECT DISTINCT user_id
                        FROM kb_operation_receipts
                        WHERE created_at < %s
                        ORDER BY user_id
                        LIMIT %s
                        """,
                        (created_at_cutoff, limit),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT DISTINCT user_id
                        FROM kb_operation_receipts
                        WHERE created_at < %s AND user_id > %s
                        ORDER BY user_id
                        LIMIT %s
                        """,
                        (created_at_cutoff, last_user_id, limit),
                    )
                users = cursor.fetchall()
            self._commit(connection)
            return users
        except Exception:
            self._rollback(connection)
            raise
        finally:
            self._close(connection)

    def _maintain_knowledge_operation_receipts_user_cursor(
        self,
        cursor: Any,
        *,
        user_id: str,
        created_at_cutoff: datetime,
        batch_size: int,
        apply: bool,
        report: Dict[str, Any],
    ) -> None:
        last_operation_id: Optional[str] = None
        lock_clause = "FOR UPDATE" if apply else ""
        while True:
            if last_operation_id is None:
                cursor.execute(
                    f"""
                    SELECT operation_id, operation_kind, result
                    FROM kb_operation_receipts
                    WHERE user_id = %s AND created_at < %s
                    ORDER BY operation_id
                    LIMIT %s
                    {lock_clause}
                    """,
                    (user_id, created_at_cutoff, batch_size),
                )
            else:
                cursor.execute(
                    f"""
                    SELECT operation_id, operation_kind, result
                    FROM kb_operation_receipts
                    WHERE user_id = %s
                      AND created_at < %s
                      AND operation_id > %s
                    ORDER BY operation_id
                    LIMIT %s
                    {lock_clause}
                    """,
                    (
                        user_id,
                        created_at_cutoff,
                        last_operation_id,
                        batch_size,
                    ),
                )
            rows = cursor.fetchall()
            if not rows:
                return

            updates = []
            update_operation_kinds = []
            for row in rows:
                operation_id = str(row["operation_id"])
                operation_kind = str(row["operation_kind"])
                result = row["result"]
                before_bytes = self._estimated_json_bytes(result)
                kind_report = self._knowledge_receipt_kind_counts(
                    report,
                    operation_kind,
                )
                self._increment_knowledge_receipt_count(
                    report,
                    kind_report,
                    "scanned",
                )
                self._increment_knowledge_receipt_bytes(
                    report,
                    kind_report,
                    before=before_bytes,
                    after=before_bytes,
                )
                last_operation_id = operation_id

                try:
                    compact_result = compact_persisted_knowledge_receipt_result(
                        result,
                        operation_id=operation_id,
                        operation_kind=operation_kind,
                    )
                except Exception:
                    self._increment_knowledge_receipt_count(
                        report,
                        kind_report,
                        "candidate",
                    )
                    self._increment_knowledge_receipt_count(
                        report,
                        kind_report,
                        "failed",
                    )
                    raise

                if compact_result == result:
                    self._increment_knowledge_receipt_count(
                        report,
                        kind_report,
                        "alreadyCompact",
                    )
                    self._increment_knowledge_receipt_count(
                        report,
                        kind_report,
                        "skipped",
                    )
                    continue

                self._increment_knowledge_receipt_count(
                    report,
                    kind_report,
                    "candidate",
                )

                after_bytes = self._estimated_json_bytes(compact_result)
                self._replace_knowledge_receipt_after_bytes(
                    report,
                    kind_report,
                    previous_after=before_bytes,
                    after=after_bytes,
                )
                if apply:
                    updates.append(
                        self._adapt_params((compact_result, user_id, operation_id))
                    )
                    update_operation_kinds.append(operation_kind)

            if apply and updates:
                cursor.executemany(
                    """
                    UPDATE kb_operation_receipts
                    SET result = %s
                    WHERE user_id = %s AND operation_id = %s
                    """,
                    updates,
                )
                report["updated"] += len(updates)
                for operation_kind in update_operation_kinds:
                    kind_report = self._knowledge_receipt_kind_counts(
                        report,
                        operation_kind,
                    )
                    kind_report["updated"] += 1

            if len(rows) < batch_size:
                return

    @staticmethod
    def _new_knowledge_receipt_maintenance_counts() -> Dict[str, Any]:
        return {
            "scanned": 0,
            "candidate": 0,
            "updated": 0,
            "skipped": 0,
            "alreadyCompact": 0,
            "failed": 0,
            "byKind": {},
            "estimatedBytes": {
                "before": 0,
                "after": 0,
                "saved": 0,
            },
        }

    @staticmethod
    def _knowledge_receipt_kind_counts(
        report: Dict[str, Any],
        operation_kind: str,
    ) -> Dict[str, Any]:
        return report["byKind"].setdefault(
            operation_kind,
            {
                "scanned": 0,
                "candidate": 0,
                "updated": 0,
                "skipped": 0,
                "alreadyCompact": 0,
                "failed": 0,
                "estimatedBytes": {
                    "before": 0,
                    "after": 0,
                    "saved": 0,
                },
            },
        )

    @staticmethod
    def _increment_knowledge_receipt_count(
        report: Dict[str, Any],
        kind_report: Dict[str, Any],
        key: str,
    ) -> None:
        report[key] += 1
        kind_report[key] += 1

    @staticmethod
    def _increment_knowledge_receipt_bytes(
        report: Dict[str, Any],
        kind_report: Dict[str, Any],
        *,
        before: int,
        after: int,
    ) -> None:
        for target in (report["estimatedBytes"], kind_report["estimatedBytes"]):
            target["before"] += before
            target["after"] += after
            target["saved"] = target["before"] - target["after"]

    @staticmethod
    def _replace_knowledge_receipt_after_bytes(
        report: Dict[str, Any],
        kind_report: Dict[str, Any],
        *,
        previous_after: int,
        after: int,
    ) -> None:
        for target in (report["estimatedBytes"], kind_report["estimatedBytes"]):
            target["after"] += after - previous_after
            target["saved"] = target["before"] - target["after"]

    @classmethod
    def _mark_knowledge_receipt_user_report_failed(
        cls,
        report: Dict[str, Any],
    ) -> None:
        report["updated"] = 0
        report["failed"] = report["candidate"]
        report["estimatedBytes"]["after"] = report["estimatedBytes"]["before"]
        report["estimatedBytes"]["saved"] = 0
        for kind_report in report["byKind"].values():
            kind_report["updated"] = 0
            kind_report["failed"] = kind_report["candidate"]
            kind_report["estimatedBytes"]["after"] = kind_report[
                "estimatedBytes"
            ]["before"]
            kind_report["estimatedBytes"]["saved"] = 0

    @classmethod
    def _merge_knowledge_receipt_maintenance_counts(
        cls,
        target: Dict[str, Any],
        source: Dict[str, Any],
    ) -> None:
        for key in (
            "scanned",
            "candidate",
            "updated",
            "skipped",
            "alreadyCompact",
            "failed",
        ):
            target[key] += source[key]
        for key in ("before", "after"):
            target["estimatedBytes"][key] += source["estimatedBytes"][key]
        target["estimatedBytes"]["saved"] = (
            target["estimatedBytes"]["before"]
            - target["estimatedBytes"]["after"]
        )
        for operation_kind, source_kind in source["byKind"].items():
            target_kind = cls._knowledge_receipt_kind_counts(target, operation_kind)
            for key in (
                "scanned",
                "candidate",
                "updated",
                "skipped",
                "alreadyCompact",
                "failed",
            ):
                target_kind[key] += source_kind[key]
            for key in ("before", "after"):
                target_kind["estimatedBytes"][key] += source_kind[
                    "estimatedBytes"
                ][key]
            target_kind["estimatedBytes"]["saved"] = (
                target_kind["estimatedBytes"]["before"]
                - target_kind["estimatedBytes"]["after"]
            )

    @staticmethod
    def _estimated_json_bytes(value: Any) -> int:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return len(serialized.encode("utf-8"))

    def _compact_kb_change_feed_user_cursor(
        self,
        cursor: Any,
        *,
        user_id: str,
        keep_recent_revisions: int,
        created_at_cutoff: datetime,
        apply: bool,
    ) -> Dict[str, int]:
        report = {
            "scannedChanges": 0,
            "plannedChanges": 0,
            "deletedChanges": 0,
            "plannedFloorAdvances": 0,
            "advancedUsers": 0,
            "legacyBarriers": 0,
            "retentionBarriers": 0,
            "revisionGapBarriers": 0,
        }
        cursor.execute(
            "SELECT revision FROM kb_snapshots WHERE user_id = %s",
            (user_id,),
        )
        snapshot = cursor.fetchone()
        current_revision = int((snapshot or {}).get("revision") or 0)
        cursor.execute(
            """
            SELECT minimum_since_revision
            FROM kb_change_feed_state
            WHERE user_id = %s
            """,
            (user_id,),
        )
        state = cursor.fetchone()
        minimum_since_revision = int(
            (state or {}).get("minimum_since_revision") or 0
        )
        cursor.execute(
            """
            SELECT c.revision, c.created_at,
                   EXISTS (
                       SELECT 1
                       FROM kb_operation_receipts r
                       WHERE r.user_id = c.user_id
                         AND r.operation_id = c.operation_id
                   ) AS has_receipt
            FROM kb_changes c
            WHERE c.user_id = %s
              AND c.revision > %s
            ORDER BY c.revision ASC
            """,
            (user_id, minimum_since_revision),
        )
        rows = cursor.fetchall()
        report["scannedChanges"] = len(rows)
        revision_cutoff = current_revision - keep_recent_revisions
        planned_revisions = []
        expected_revision = minimum_since_revision + 1
        for row in rows:
            revision = int(row["revision"])
            if revision != expected_revision:
                report["revisionGapBarriers"] += 1
                break
            created_at = self._datetime_value(row.get("created_at"))
            if (
                revision > revision_cutoff
                or created_at is None
                or created_at >= created_at_cutoff
            ):
                report["retentionBarriers"] += 1
                break
            if not bool(row.get("has_receipt")):
                report["legacyBarriers"] += 1
                break
            planned_revisions.append(revision)

            expected_revision += 1

        if not planned_revisions:
            return report
        report["plannedChanges"] = len(planned_revisions)
        report["plannedFloorAdvances"] = 1
        if not apply:
            return report

        cursor.execute(
            """
            DELETE FROM kb_changes c
            WHERE c.user_id = %s
              AND c.revision = ANY(%s)
              AND EXISTS (
                  SELECT 1
                  FROM kb_operation_receipts r
                  WHERE r.user_id = c.user_id
                    AND r.operation_id = c.operation_id
              )
            RETURNING c.revision
            """,
            (user_id, planned_revisions),
        )
        deleted_revisions = sorted(
            int(row["revision"]) for row in cursor.fetchall()
        )
        if deleted_revisions != planned_revisions:
            raise RuntimeError(
                "knowledge change feed compaction delete set changed during transaction"
            )
        new_minimum = planned_revisions[-1]
        cursor.execute(
            """
            INSERT INTO kb_change_feed_state (
                user_id, minimum_since_revision, updated_at
            )
            VALUES (%s, %s, NOW())
            ON CONFLICT (user_id) DO UPDATE
            SET minimum_since_revision = EXCLUDED.minimum_since_revision,
                updated_at = NOW()
            RETURNING minimum_since_revision
            """,
            (user_id, new_minimum),
        )
        saved_state = cursor.fetchone()
        if int(saved_state["minimum_since_revision"]) != new_minimum:
            raise RuntimeError("knowledge change feed compaction floor update failed")
        report["deletedChanges"] = len(deleted_revisions)
        report["advancedUsers"] = 1
        return report

    @staticmethod
    def _postgres_timeout_reason(exc: Exception) -> Optional[str]:
        sqlstate = getattr(exc, "sqlstate", None)
        if sqlstate is None:
            sqlstate = getattr(getattr(exc, "diag", None), "sqlstate", None)
        if sqlstate == "55P03":
            return "lockTimeout"
        if sqlstate == "57014":
            return "statementTimeout"
        return None

    def _list_kb_change_rows_cursor(
        self,
        cursor: Any,
        user_id: str,
        since_revision: int,
        *,
        through_revision: Optional[int],
        limit: Optional[int],
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
        cursor.execute(
            f"""
            SELECT revision, operation_id, graph, mutation, created_at
            FROM kb_changes
            WHERE {' AND '.join(where_clauses)}
            ORDER BY revision ASC
            {limit_clause}
            """,
            tuple(params),
        )
        return cursor.fetchall()

    @classmethod
    def _kb_change_rows_payload(cls, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [
            {
                "revision": int(row["revision"]),
                "operationId": str(row["operation_id"]),
                "graph": deepcopy(row["graph"]),
                "createdAt": cls._iso_value(row.get("created_at")),
                "mutationSchemaVersion": 2 if row.get("mutation") is not None else 1,
                "mutation": deepcopy(row.get("mutation")),
            }
            for row in rows
        ]

    @staticmethod
    def _datetime_value(value: Any) -> Optional[datetime]:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        else:
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(timezone.utc)

    def maintain_knowledge_privacy_metadata(self, *, apply: bool = False) -> Dict[str, Any]:
        report = {
            "schemaVersion": 1,
            "mode": "apply" if apply else "dryRun",
            "status": "ok",
            "scanned": {
                "users": 0,
                "snapshots": 0,
                "changes": 0,
                "receipts": 0,
            },
            "changed": {
                "snapshotGraphs": 0,
                "changeGraphs": 0,
                "changeMutations": 0,
                "receiptResults": 0,
                "receiptPayloadHashes": 0,
            },
            "invalidRecordCount": 0,
        }
        connection = self._open_connection()
        snapshot_updates = []
        change_graph_updates = []
        change_mutation_updates = []
        receipt_result_updates = []
        receipt_hash_updates = []
        try:
            with connection.cursor(row_factory=self._dict_row_factory()) as cursor:
                cursor.execute("SET LOCAL lock_timeout = '5s'")
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    ("knowledge-privacy-metadata-maintenance:v1",),
                )
                cursor.execute(
                    """
                    SELECT user_id FROM (
                        SELECT user_id FROM kb_snapshots
                        UNION
                        SELECT user_id FROM kb_changes
                        UNION
                        SELECT user_id FROM kb_operation_receipts
                    ) AS knowledge_users
                    ORDER BY user_id
                    """
                )
                user_rows = cursor.fetchall()
                user_ids = sorted(
                    {
                        str(row.get("user_id") or "")
                        for row in user_rows
                        if str(row.get("user_id") or "")
                    }
                )
                report["scanned"]["users"] = len(user_ids)
                for user_id in user_ids:
                    cursor.execute(
                        "SELECT pg_advisory_xact_lock(hashtext(%s))",
                        (f"knowledge:{user_id}",),
                    )
                cursor.execute(
                    """
                    LOCK TABLE kb_snapshots, kb_changes, kb_operation_receipts
                    IN SHARE ROW EXCLUSIVE MODE
                    """
                )

                cursor.execute(
                    "SELECT user_id, graph FROM kb_snapshots ORDER BY user_id"
                )
                snapshot_rows = cursor.fetchall()
                report["scanned"]["snapshots"] = len(snapshot_rows)
                for row in snapshot_rows:
                    try:
                        canonical_graph = canonicalize_persisted_knowledge_graph(
                            row.get("graph")
                        )
                    except KnowledgePrivacyMetadataError:
                        report["invalidRecordCount"] += 1
                        continue
                    if canonical_graph != row.get("graph"):
                        report["changed"]["snapshotGraphs"] += 1
                        snapshot_updates.append((canonical_graph, row["user_id"]))

                cursor.execute(
                    """
                    SELECT user_id, revision, graph, mutation
                    FROM kb_changes
                    ORDER BY user_id, revision
                    """
                )
                change_rows = cursor.fetchall()
                report["scanned"]["changes"] = len(change_rows)
                for row in change_rows:
                    try:
                        canonical_graph = canonicalize_persisted_knowledge_graph(
                            row.get("graph")
                        )
                        canonical_mutation = (
                            None
                            if row.get("mutation") is None
                            else canonicalize_persisted_knowledge_mutation(
                                row.get("mutation")
                            )
                        )
                    except KnowledgePrivacyMetadataError:
                        report["invalidRecordCount"] += 1
                        continue
                    if canonical_graph != row.get("graph"):
                        report["changed"]["changeGraphs"] += 1
                        change_graph_updates.append(
                            (canonical_graph, row["user_id"], row["revision"])
                        )
                    if canonical_mutation != row.get("mutation"):
                        report["changed"]["changeMutations"] += 1
                        change_mutation_updates.append(
                            (canonical_mutation, row["user_id"], row["revision"])
                        )

                cursor.execute(
                    """
                    SELECT user_id, operation_id, operation_kind, schema_version,
                           payload_hash, result
                    FROM kb_operation_receipts
                    ORDER BY user_id, operation_id
                    """
                )
                receipt_rows = cursor.fetchall()
                report["scanned"]["receipts"] = len(receipt_rows)
                for row in receipt_rows:
                    operation_kind = str(row.get("operation_kind") or "")
                    schema_version = int(row.get("schema_version") or 0)
                    try:
                        canonical_result = canonicalize_persisted_receipt_result(
                            row.get("result"),
                            require_v2_mutation=(
                                operation_kind == KB_OPERATION_MUTATION
                                and schema_version == 2
                            ),
                        )
                        canonical_hash = canonical_receipt_payload_hash(
                            operation_kind=operation_kind,
                            schema_version=schema_version,
                            canonical_result=canonical_result,
                            current_payload_hash=str(row.get("payload_hash") or ""),
                        )
                    except (KnowledgePrivacyMetadataError, TypeError, ValueError):
                        report["invalidRecordCount"] += 1
                        continue
                    if canonical_result != row.get("result"):
                        report["changed"]["receiptResults"] += 1
                        receipt_result_updates.append(
                            (canonical_result, row["user_id"], row["operation_id"])
                        )
                    if canonical_hash != str(row.get("payload_hash") or ""):
                        report["changed"]["receiptPayloadHashes"] += 1
                        receipt_hash_updates.append(
                            (canonical_hash, row["user_id"], row["operation_id"])
                        )

                if report["invalidRecordCount"]:
                    report["status"] = "invalidRecords"
                    if apply:
                        raise KnowledgePrivacyMetadataError(
                            "knowledge privacy maintenance refused invalid historical records"
                        )

                if apply:
                    for params in snapshot_updates:
                        cursor.execute(
                            "UPDATE kb_snapshots SET graph = %s WHERE user_id = %s",
                            self._adapt_params(params),
                        )
                    for params in change_graph_updates:
                        cursor.execute(
                            """
                            UPDATE kb_changes SET graph = %s
                            WHERE user_id = %s AND revision = %s
                            """,
                            self._adapt_params(params),
                        )
                    for params in change_mutation_updates:
                        cursor.execute(
                            """
                            UPDATE kb_changes SET mutation = %s
                            WHERE user_id = %s AND revision = %s
                            """,
                            self._adapt_params(params),
                        )
                    for params in receipt_result_updates:
                        cursor.execute(
                            """
                            UPDATE kb_operation_receipts SET result = %s
                            WHERE user_id = %s AND operation_id = %s
                            """,
                            self._adapt_params(params),
                        )
                    for params in receipt_hash_updates:
                        cursor.execute(
                            """
                            UPDATE kb_operation_receipts SET payload_hash = %s
                            WHERE user_id = %s AND operation_id = %s
                            """,
                            params,
                        )
            self._commit(connection)
            return report
        except Exception:
            self._rollback(connection)
            raise
        finally:
            self._close(connection)

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
        connection = self._open_connection()
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
                    self._commit(connection)
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
                    self._commit(connection)
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
            self._commit(connection)
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
        finally:
            self._close(connection)

    def drain_expired_digital_human_session_leases(self, *, now_iso: str) -> Dict[str, int]:
        connection = self._open_connection()
        try:
            with connection.cursor(row_factory=self._dict_row_factory()) as cursor:
                cursor.execute(
                    """
                    SELECT id, payload FROM digital_human_sessions
                    WHERE status = 'active' AND expires_at <= %s
                    FOR UPDATE
                    """,
                    (now_iso,),
                )
                rows = cursor.fetchall()
                for row in rows:
                    lease = deepcopy(row["payload"])
                    lease["status"] = "expired"
                    lease["expiredAt"] = now_iso
                    lease["updatedAt"] = now_iso
                    self._update_digital_human_session_cursor(cursor, lease)
                cursor.execute(
                    "SELECT COUNT(*) AS count FROM digital_human_sessions WHERE status = 'active'"
                )
                active_row = cursor.fetchone()
            self._commit(connection)
            return {
                "expiredLeaseCount": len(rows),
                "activeLeaseCount": int((active_row or {}).get("count") or 0),
            }
        except Exception:
            self._rollback(connection)
            raise
        finally:
            self._close(connection)

    def heartbeat_digital_human_session_lease(
        self,
        session_id: str,
        *,
        user_id: str,
        device_id: str,
        heartbeat_at_iso: str,
        expires_at_iso: str,
    ) -> Optional[Dict[str, Any]]:
        connection = self._open_connection()
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
                    self._commit(connection)
                    return None
                lease = deepcopy(row["payload"])
                if lease.get("userId") != user_id or lease.get("deviceId") != device_id:
                    self._commit(connection)
                    return None
                if lease.get("status") != "active":
                    self._commit(connection)
                    return {"outcome": self._inactive_digital_human_outcome(lease), "lease": lease}
                if self._parse_iso_datetime(str(lease.get("expiresAt") or "")) <= self._parse_iso_datetime(heartbeat_at_iso):
                    lease["status"] = "expired"
                    lease["expiredAt"] = heartbeat_at_iso
                    lease["updatedAt"] = heartbeat_at_iso
                    self._update_digital_human_session_cursor(cursor, lease)
                    self._commit(connection)
                    return {"outcome": "expired", "lease": lease}
                lease["heartbeatAt"] = heartbeat_at_iso
                lease["expiresAt"] = expires_at_iso
                lease["updatedAt"] = heartbeat_at_iso
                self._update_digital_human_session_cursor(cursor, lease)
            self._commit(connection)
            return {"outcome": "active", "lease": deepcopy(lease)}
        except Exception:
            self._rollback(connection)
            raise
        finally:
            self._close(connection)

    def release_digital_human_session_lease(
        self,
        session_id: str,
        *,
        user_id: str,
        device_id: str,
        released_at_iso: str,
        reason: str,
    ) -> Optional[Dict[str, Any]]:
        connection = self._open_connection()
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
                    self._commit(connection)
                    return None
                lease = deepcopy(row["payload"])
                if lease.get("userId") != user_id or lease.get("deviceId") != device_id:
                    self._commit(connection)
                    return None
                if lease.get("status") == "released":
                    self._commit(connection)
                    return {"outcome": "alreadyReleased", "lease": lease}
                if lease.get("status") == "expired":
                    self._commit(connection)
                    return {"outcome": "alreadyExpired", "lease": lease}
                lease["status"] = "released"
                lease["releasedAt"] = released_at_iso
                lease["releaseReason"] = reason
                lease["updatedAt"] = released_at_iso
                self._update_digital_human_session_cursor(cursor, lease)
            self._commit(connection)
            return {"outcome": "released", "lease": deepcopy(lease)}
        except Exception:
            self._rollback(connection)
            raise
        finally:
            self._close(connection)

    def get_digital_human_session_lease(self, session_id: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone(
            "SELECT payload FROM digital_human_sessions WHERE id = %s",
            (session_id,),
        )
        return None if row is None else deepcopy(row["payload"])

    def add_memory(self, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        item = self._with_identity(payload, "memory", user_id)
        return self._insert_payload("memories", user_id, item)

    def resolve_resource_authority(
        self,
        resource_type: str,
        resource_id: str,
    ) -> Optional[Dict[str, Any]]:
        table = self._RESOURCE_AUTHORITY_TABLES.get(resource_type)
        if table is None:
            raise ValueError(f"unsupported resource type: {resource_type}")
        row = self._fetchone(
            f"""
            SELECT id, vault_id, owner_subject_id, row_version, authority_state
            FROM {table}
            WHERE id = %s
            """,
            (resource_id,),
        )
        if row is None:
            return None
        return {
            "resourceType": resource_type,
            "resourceId": str(row["id"]),
            "vaultId": str(row["vault_id"]),
            "ownerSubjectId": str(row["owner_subject_id"]),
            "rowVersion": int(row["row_version"]),
            "authorityState": str(row["authority_state"]),
        }

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
        governance_summary: Optional[Dict[str, Any]] = None,
        expected_version: Optional[int] = None,
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
                cursor.execute(
                    """
                    INSERT INTO kb_change_feed_state (user_id, minimum_since_revision, updated_at)
                    VALUES (%s, 0, NOW())
                    ON CONFLICT (user_id) DO NOTHING
                    """,
                    (user_id,),
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
                    self._commit(connection)
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
                    self._commit(connection)
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
                    SELECT payload, row_version
                    FROM archive_items
                    WHERE user_id = %s AND id = %s
                    FOR UPDATE
                    """,
                    (user_id, item_id),
                )
                archive_row = cursor.fetchone()
                if archive_row is None:
                    raise ArchiveItemNotFound("archive item not found")
                current_resource_version = int(archive_row.get("row_version") or 1)
                if expected_version is not None and expected_version != current_resource_version:
                    raise ResourceVersionConflict(
                        expected_version=expected_version,
                        current_version=current_resource_version,
                    )
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
                    governance_summary=governance_summary,
                )
            self._commit(connection)
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
            item["ownerUserId"] = row["user_id"]
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
            metadata["ownerUserId"] = row["user_id"]
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
                payload = EXCLUDED.payload,
                created_at = NOW()
            WHERE mailbox_letters.user_id = EXCLUDED.user_id
            RETURNING payload
            """,
            (user_id, item["id"], item),
            commit=True,
        )
        if row is None:
            raise ResourceOwnershipConflict("resource id belongs to another owner")
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
                payload = EXCLUDED.payload,
                created_at = NOW()
            WHERE echo_delayed_replies.user_id = EXCLUDED.user_id
            RETURNING payload
            """,
            (user_id, item["id"], item),
            commit=True,
        )
        if row is None:
            raise ResourceOwnershipConflict("resource id belongs to another owner")
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
                payload = EXCLUDED.payload,
                updated_at = NOW()
            WHERE push_device_tokens.user_id = EXCLUDED.user_id
            RETURNING payload
            """,
            (user_id, item["id"], item),
            commit=True,
        )
        if row is None:
            raise ResourceOwnershipConflict("resource id belongs to another owner")
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

    @contextmanager
    def delegated_access_relationship_scope(
        self,
        *,
        owner_subject_id: str,
        relationship_id: str,
    ):
        lock_key = f"delegated-access:{owner_subject_id}:{relationship_id}"
        with self.request_unit_of_work(
            correlation_id=f"delegated-access-{uuid.uuid4().hex}",
            command_id="delegatedAccessRelationshipScope",
        ):
            self._fetchone(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS acquired",
                (lock_key,),
            )
            yield

    def upsert_family_relationship(self, relationship: Dict[str, Any]) -> Dict[str, Any]:
        row = self._fetchone(
            """
            INSERT INTO family_relationships (
                id, vault_id, owner_subject_id, family_member_id,
                member_subject_id, status, relationship_epoch, grant_epoch,
                created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, 1, 0, NOW(), NOW())
            ON CONFLICT (vault_id, family_member_id) DO UPDATE SET
                member_subject_id = CASE
                    WHEN EXCLUDED.status = 'accepted'
                     AND (
                        family_relationships.status = 'pending'
                        OR family_relationships.member_subject_id LIKE 'legacy-unverified:%%'
                     )
                    THEN EXCLUDED.member_subject_id
                    ELSE family_relationships.member_subject_id
                END,
                status = CASE
                    WHEN family_relationships.status IN ('paused', 'revoked')
                     AND EXCLUDED.status = 'accepted'
                    THEN family_relationships.status
                    WHEN family_relationships.status = 'accepted'
                     AND EXCLUDED.status = 'pending'
                    THEN family_relationships.status
                    ELSE EXCLUDED.status
                END,
                relationship_epoch = family_relationships.relationship_epoch + CASE
                    WHEN family_relationships.status IS DISTINCT FROM CASE
                        WHEN family_relationships.status IN ('paused', 'revoked')
                         AND EXCLUDED.status = 'accepted'
                        THEN family_relationships.status
                        WHEN family_relationships.status = 'accepted'
                         AND EXCLUDED.status = 'pending'
                        THEN family_relationships.status
                        ELSE EXCLUDED.status
                    END
                    OR family_relationships.member_subject_id IS DISTINCT FROM CASE
                        WHEN EXCLUDED.status = 'accepted'
                         AND (
                            family_relationships.status = 'pending'
                            OR family_relationships.member_subject_id LIKE 'legacy-unverified:%%'
                         )
                        THEN EXCLUDED.member_subject_id
                        ELSE family_relationships.member_subject_id
                    END
                    THEN 1 ELSE 0
                END,
                updated_at = NOW()
            RETURNING id, vault_id, owner_subject_id, family_member_id,
                member_subject_id, status, relationship_epoch, grant_epoch,
                created_at, updated_at
            """,
            (
                relationship["id"],
                relationship.get("vaultId") or relationship["ownerSubjectId"],
                relationship["ownerSubjectId"],
                relationship["familyMemberId"],
                relationship["memberSubjectId"],
                relationship["status"],
            ),
            commit=True,
        )
        if row is None:
            raise ValueError("family relationship upsert failed")
        return self._family_relationship_payload(row)

    def get_family_relationship(
        self,
        owner_subject_id: str,
        relationship_id: str,
    ) -> Optional[Dict[str, Any]]:
        row = self._fetchone(
            """
            SELECT id, vault_id, owner_subject_id, family_member_id,
                member_subject_id, status, relationship_epoch, grant_epoch,
                created_at, updated_at
            FROM family_relationships
            WHERE vault_id = %s AND id = %s
            """,
            (owner_subject_id, relationship_id),
        )
        return None if row is None else self._family_relationship_payload(row)

    def get_family_relationship_by_member(
        self,
        owner_subject_id: str,
        family_member_id: str,
    ) -> Optional[Dict[str, Any]]:
        row = self._fetchone(
            """
            SELECT id, vault_id, owner_subject_id, family_member_id,
                member_subject_id, status, relationship_epoch, grant_epoch,
                created_at, updated_at
            FROM family_relationships
            WHERE vault_id = %s AND family_member_id = %s
            """,
            (owner_subject_id, family_member_id),
        )
        return None if row is None else self._family_relationship_payload(row)

    def list_family_relationships(self, owner_subject_id: str) -> List[Dict[str, Any]]:
        rows = self._fetchall(
            """
            SELECT id, vault_id, owner_subject_id, family_member_id,
                member_subject_id, status, relationship_epoch, grant_epoch,
                created_at, updated_at
            FROM family_relationships
            WHERE vault_id = %s
            ORDER BY created_at ASC, id ASC
            """,
            (owner_subject_id,),
        )
        return [self._family_relationship_payload(row) for row in rows]

    def update_family_relationship_status(
        self,
        owner_subject_id: str,
        relationship_id: str,
        *,
        status: str,
        expected_epoch: int,
    ) -> Optional[Dict[str, Any]]:
        row = self._fetchone(
            """
            UPDATE family_relationships
            SET status = %s,
                relationship_epoch = relationship_epoch + 1,
                updated_at = NOW()
            WHERE vault_id = %s AND id = %s AND relationship_epoch = %s
            RETURNING id, vault_id, owner_subject_id, family_member_id,
                member_subject_id, status, relationship_epoch, grant_epoch,
                created_at, updated_at
            """,
            (status, owner_subject_id, relationship_id, expected_epoch),
            commit=True,
        )
        return None if row is None else self._family_relationship_payload(row)

    def create_access_grant(self, grant: Dict[str, Any]) -> Dict[str, Any]:
        row = self._fetchone(
            """
            WITH inserted AS (
                INSERT INTO access_grants (
                    id, vault_id, grantor_subject_id, grantee_subject_id,
                    relationship_id, purpose, resource_type, resource_id,
                    operations, status, expires_at, revoked_at, row_version,
                    created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'active', %s, NULL, 1, %s, %s)
                RETURNING *
            ), event AS (
                INSERT INTO grant_events (
                    id, grant_id, relationship_id, event_type,
                    actor_subject_id, grant_version, reason, occurred_at
                )
                SELECT %s, id, relationship_id, 'granted', grantor_subject_id,
                    row_version, 'ownerGranted', created_at
                FROM inserted
            ), bumped AS (
                UPDATE family_relationships
                SET grant_epoch = grant_epoch + 1, updated_at = NOW()
                WHERE id = (SELECT relationship_id FROM inserted)
                RETURNING grant_epoch
            )
            SELECT inserted.*, bumped.grant_epoch
            FROM inserted CROSS JOIN bumped
            """,
            (
                grant["id"],
                grant.get("vaultId") or grant["grantorSubjectId"],
                grant["grantorSubjectId"],
                grant["granteeSubjectId"],
                grant["relationshipId"],
                grant["purpose"],
                grant["resourceType"],
                grant.get("resourceId"),
                Jsonb(grant.get("operations") or []),
                grant.get("expiresAt"),
                grant["createdAt"],
                grant["updatedAt"],
                f"grant_event_{uuid.uuid4().hex}",
            ),
            commit=True,
        )
        if row is None:
            raise ValueError("access grant creation failed")
        return self._access_grant_payload(row)

    def get_access_grant(self, grant_id: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone(
            """
            SELECT id, vault_id, grantor_subject_id, grantee_subject_id,
                relationship_id, purpose, resource_type, resource_id,
                operations, status, expires_at, revoked_at, row_version,
                created_at, updated_at
            FROM access_grants
            WHERE id = %s
            """,
            (grant_id,),
        )
        return None if row is None else self._access_grant_payload(row)

    def list_access_grants(
        self,
        *,
        owner_subject_id: str,
        relationship_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        clauses = ["vault_id = %s"]
        params: List[Any] = [owner_subject_id]
        if relationship_id is not None:
            clauses.append("relationship_id = %s")
            params.append(relationship_id)
        rows = self._fetchall(
            f"""
            SELECT id, vault_id, grantor_subject_id, grantee_subject_id,
                relationship_id, purpose, resource_type, resource_id,
                operations, status, expires_at, revoked_at, row_version,
                created_at, updated_at
            FROM access_grants
            WHERE {' AND '.join(clauses)}
            ORDER BY created_at ASC, id ASC
            """,
            tuple(params),
        )
        return [self._access_grant_payload(row) for row in rows]

    def revoke_access_grant(
        self,
        owner_subject_id: str,
        grant_id: str,
        *,
        expected_version: int,
        revoked_at_iso: str,
        reason: str,
    ) -> Optional[Dict[str, Any]]:
        row = self._fetchone(
            """
            WITH revoked AS (
                UPDATE access_grants
                SET status = 'revoked', revoked_at = %s,
                    updated_at = %s, row_version = row_version + 1
                WHERE vault_id = %s AND id = %s
                  AND status = 'active' AND row_version = %s
                RETURNING *
            ), event AS (
                INSERT INTO grant_events (
                    id, grant_id, relationship_id, event_type,
                    actor_subject_id, grant_version, reason, occurred_at
                )
                SELECT %s, id, relationship_id, 'revoked', grantor_subject_id,
                    row_version, %s, revoked_at
                FROM revoked
            ), bumped AS (
                UPDATE family_relationships
                SET grant_epoch = grant_epoch + 1, updated_at = NOW()
                WHERE id = (SELECT relationship_id FROM revoked)
                RETURNING grant_epoch
            )
            SELECT revoked.*, bumped.grant_epoch
            FROM revoked CROSS JOIN bumped
            """,
            (
                revoked_at_iso,
                revoked_at_iso,
                owner_subject_id,
                grant_id,
                expected_version,
                f"grant_event_{uuid.uuid4().hex}",
                reason,
            ),
            commit=True,
        )
        return None if row is None else self._access_grant_payload(row)

    def revoke_all_access_grants_for_subject(
        self,
        subject_id: str,
        *,
        revoked_at_iso: str,
        reason: str,
    ) -> int:
        row = self._fetchone(
            """
            WITH revoked AS (
                UPDATE access_grants
                SET status = 'revoked', revoked_at = %s,
                    updated_at = %s, row_version = row_version + 1
                WHERE status = 'active'
                  AND (grantor_subject_id = %s OR grantee_subject_id = %s)
                RETURNING *
            ), events AS (
                INSERT INTO grant_events (
                    id, grant_id, relationship_id, event_type,
                    actor_subject_id, grant_version, reason, occurred_at
                )
                SELECT 'grant_event_' || md5(random()::text || clock_timestamp()::text || id),
                    id, relationship_id, 'revoked', %s,
                    row_version, %s, revoked_at
                FROM revoked
                RETURNING grant_id
            ), bumped AS (
                UPDATE family_relationships
                SET grant_epoch = grant_epoch + 1, updated_at = NOW()
                WHERE id IN (SELECT DISTINCT relationship_id FROM revoked)
                RETURNING id
            )
            SELECT COUNT(*)::BIGINT AS revoked_count FROM revoked
            """,
            (
                revoked_at_iso,
                revoked_at_iso,
                subject_id,
                subject_id,
                subject_id,
                reason,
            ),
            commit=True,
        )
        return int((row or {}).get("revoked_count") or 0)

    def revoke_all_access_grants_for_relationship(
        self,
        owner_subject_id: str,
        relationship_id: str,
        *,
        revoked_at_iso: str,
        reason: str,
    ) -> int:
        row = self._fetchone(
            """
            WITH revoked AS (
                UPDATE access_grants
                SET status = 'revoked', revoked_at = %s,
                    updated_at = %s, row_version = row_version + 1
                WHERE status = 'active'
                  AND grantor_subject_id = %s
                  AND relationship_id = %s
                RETURNING *
            ), events AS (
                INSERT INTO grant_events (
                    id, grant_id, relationship_id, event_type,
                    actor_subject_id, grant_version, reason, occurred_at
                )
                SELECT 'grant_event_' || md5(random()::text || clock_timestamp()::text || id),
                    id, relationship_id, 'revoked', %s,
                    row_version, %s, revoked_at
                FROM revoked
                RETURNING grant_id
            ), bumped AS (
                UPDATE family_relationships
                SET grant_epoch = grant_epoch + 1, updated_at = NOW()
                WHERE id = %s AND EXISTS (SELECT 1 FROM revoked)
                RETURNING id
            )
            SELECT COUNT(*)::BIGINT AS revoked_count FROM revoked
            """,
            (
                revoked_at_iso,
                revoked_at_iso,
                owner_subject_id,
                relationship_id,
                owner_subject_id,
                reason,
                relationship_id,
            ),
            commit=True,
        )
        return int((row or {}).get("revoked_count") or 0)

    def record_access_grant_receipt(
        self,
        grant: Dict[str, Any],
        *,
        actor_subject_id: str,
        operation: str,
    ) -> Dict[str, Any]:
        row = self._fetchone(
            """
            INSERT INTO grant_events (
                id, grant_id, relationship_id, event_type,
                actor_subject_id, grant_version, reason, occurred_at
            )
            SELECT %s, id, relationship_id, 'accessed', %s,
                row_version, %s, NOW()
            FROM access_grants
            WHERE id = %s AND status = 'active'
              AND (expires_at IS NULL OR expires_at > NOW())
            RETURNING id, grant_id, relationship_id, event_type,
                actor_subject_id, grant_version, reason, occurred_at
            """,
            (
                f"grant_event_{uuid.uuid4().hex}",
                actor_subject_id,
                f"authorized:{operation}",
                grant["id"],
            ),
            commit=True,
        )
        if row is None:
            raise ValueError("active access grant is required for receipt")
        return {
            "id": str(row["id"]),
            "grantId": str(row["grant_id"]),
            "relationshipId": str(row["relationship_id"]),
            "eventType": str(row["event_type"]),
            "actorSubjectId": str(row["actor_subject_id"]),
            "grantVersion": int(row["grant_version"]),
            "reason": str(row["reason"]),
            "occurredAt": self._iso_value(row.get("occurred_at")),
        }

    def list_grant_events(self, grant_id: str) -> List[Dict[str, Any]]:
        rows = self._fetchall(
            """
            SELECT id, grant_id, relationship_id, event_type,
                actor_subject_id, grant_version, reason, occurred_at
            FROM grant_events
            WHERE grant_id = %s
            ORDER BY occurred_at ASC, id ASC
            """,
            (grant_id,),
        )
        return [
            {
                "id": str(row["id"]),
                "grantId": str(row["grant_id"]),
                "relationshipId": str(row["relationship_id"]),
                "eventType": str(row["event_type"]),
                "actorSubjectId": str(row["actor_subject_id"]),
                "grantVersion": int(row["grant_version"]),
                "reason": str(row["reason"]),
                "occurredAt": self._iso_value(row.get("occurred_at")),
            }
            for row in rows
        ]

    def list_access_receipts(
        self,
        *,
        owner_subject_id: str,
        grant_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        clauses = ["g.grantor_subject_id = %s", "e.event_type = 'accessed'"]
        params: List[Any] = [owner_subject_id]
        if grant_id is not None:
            clauses.append("g.id = %s")
            params.append(grant_id)
        rows = self._fetchall(
            f"""
            SELECT e.id, e.grant_id, e.relationship_id, e.actor_subject_id,
                e.grant_version, e.reason, e.occurred_at,
                g.grantor_subject_id, g.purpose, g.resource_type, g.resource_id
            FROM grant_events e
            JOIN access_grants g ON g.id = e.grant_id
            WHERE {' AND '.join(clauses)}
            ORDER BY e.occurred_at ASC, e.id ASC
            """,
            tuple(params),
        )
        receipts: List[Dict[str, Any]] = []
        for row in rows:
            reason = str(row.get("reason") or "")
            operation = reason.split(":", 1)[1] if reason.startswith("authorized:") else ""
            receipts.append(
                {
                    "id": str(row["id"]),
                    "decision": "allow",
                    "grantId": str(row["grant_id"]),
                    "relationshipId": str(row["relationship_id"]),
                    "ownerSubjectId": str(row["grantor_subject_id"]),
                    "granteeSubjectId": str(row["actor_subject_id"]),
                    "purpose": str(row["purpose"]),
                    "operation": operation,
                    "resourceType": str(row["resource_type"]),
                    "resourceId": row.get("resource_id"),
                    "grantVersion": int(row["grant_version"]),
                    "occurredAt": self._iso_value(row.get("occurred_at")),
                }
            )
        return receipts

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
        receipt_result = receipt["result"]
        if is_compact_knowledge_operation_receipt_result(receipt_result):
            cursor.execute(
                """
                SELECT revision, graph, mutation, created_at
                FROM kb_changes
                WHERE user_id = %s AND operation_id = %s
                """,
                (user_id, operation_id),
            )
            existing = cursor.fetchone()
            snapshot = None
            if existing is None:
                cursor.execute(
                    """
                    SELECT graph, revision, updated_at
                    FROM kb_snapshots
                    WHERE user_id = %s
                    """,
                    (user_id,),
                )
                snapshot_row = cursor.fetchone()
                if snapshot_row is not None:
                    snapshot = {
                        "graph": deepcopy(snapshot_row.get("graph") or {}),
                        "revision": int(snapshot_row.get("revision") or 0),
                        "updatedAt": self._iso_value(snapshot_row.get("updated_at")),
                    }
            result = rebuild_compact_knowledge_operation_result(
                receipt_result,
                user_id=user_id,
                operation_id=operation_id,
                change=self._kb_change_replay_row(existing),
                snapshot=snapshot,
            )
        else:
            result = deepcopy(receipt_result)
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
        governance_summary: Optional[Dict[str, Any]] = None,
    ) -> None:
        compact_result = compact_knowledge_operation_receipt_result(
            result,
            operation_id=operation_id,
            operation_kind=operation_kind,
            governance_summary=governance_summary,
        )
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
                    compact_result,
                )
            ),
        )

    def _kb_change_replay_row(self, row: Any) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        mutation = deepcopy(row.get("mutation"))
        return {
            "revision": int(row.get("revision") or 0),
            "graph": deepcopy(row.get("graph") or {}),
            "updatedAt": self._iso_value(row.get("created_at")),
            "mutationSchemaVersion": 2 if mutation is not None else 1,
            "mutation": mutation,
        }

    def _insert_payload(self, table: str, user_id: str, item: Dict[str, Any]) -> Dict[str, Any]:
        row = self._fetchone(
            f"""
            INSERT INTO {table} (user_id, id, payload, created_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (id) DO UPDATE SET
                payload = EXCLUDED.payload,
                created_at = NOW()
            WHERE {table}.user_id = EXCLUDED.user_id
            RETURNING payload
            """,
            (user_id, item["id"], item),
            commit=True,
        )
        if row is None:
            raise ResourceOwnershipConflict("resource id belongs to another owner")
        return deepcopy(row["payload"])

    @classmethod
    def _family_relationship_payload(cls, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": str(row["id"]),
            "vaultId": str(row["vault_id"]),
            "ownerSubjectId": str(row["owner_subject_id"]),
            "familyMemberId": str(row["family_member_id"]),
            "memberSubjectId": str(row["member_subject_id"]),
            "status": str(row["status"]),
            "relationshipEpoch": int(row.get("relationship_epoch") or 1),
            "grantEpoch": int(row.get("grant_epoch") or 0),
            "createdAt": cls._iso_value(row.get("created_at")),
            "updatedAt": cls._iso_value(row.get("updated_at")),
        }

    @classmethod
    def _access_grant_payload(cls, row: Dict[str, Any]) -> Dict[str, Any]:
        operations = row.get("operations") or []
        if isinstance(operations, str):
            try:
                operations = json.loads(operations)
            except json.JSONDecodeError:
                operations = []
        return {
            "id": str(row["id"]),
            "vaultId": str(row["vault_id"]),
            "grantorSubjectId": str(row["grantor_subject_id"]),
            "granteeSubjectId": str(row["grantee_subject_id"]),
            "relationshipId": str(row["relationship_id"]),
            "purpose": str(row["purpose"]),
            "resourceType": str(row["resource_type"]),
            "resourceId": row.get("resource_id"),
            "operations": list(operations) if isinstance(operations, (list, tuple)) else [],
            "status": str(row["status"]),
            "expiresAt": cls._iso_value(row.get("expires_at")),
            "revokedAt": cls._iso_value(row.get("revoked_at")),
            "rowVersion": int(row.get("row_version") or 1),
            "createdAt": cls._iso_value(row.get("created_at")),
            "updatedAt": cls._iso_value(row.get("updated_at")),
        }

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
        active = self._current_uow.get()
        if active is None:
            with self.request_unit_of_work(
                correlation_id=f"repo-{uuid.uuid4().hex}",
                command_id="repositoryFetchOne",
            ):
                return self._fetchone(sql, params, commit=commit)
        connection = active.connection
        try:
            with connection.cursor(row_factory=self._dict_row_factory()) as cursor:
                cursor.execute(sql, self._adapt_params(params))
                row = cursor.fetchone()
            return row
        except Exception:
            active.mark_rollback("statementFailure")
            raise

    def _fetchall(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        active = self._current_uow.get()
        if active is None:
            with self.request_unit_of_work(
                correlation_id=f"repo-{uuid.uuid4().hex}",
                command_id="repositoryFetchAll",
            ):
                return self._fetchall(sql, params)
        connection = active.connection
        try:
            with connection.cursor(row_factory=self._dict_row_factory()) as cursor:
                cursor.execute(sql, self._adapt_params(params))
                rows = cursor.fetchall()
            return rows
        except Exception:
            active.mark_rollback("statementFailure")
            raise

    @staticmethod
    def _adapt_params(params: tuple) -> tuple:
        return tuple(Jsonb(param) if isinstance(param, dict) else param for param in params)

    def _open_connection(self):
        active = self._current_uow.get()
        if active is not None:
            return active.connection
        try:
            connection = self._pool.getconn(timeout=self._pool_timeout_seconds)
        except ConnectionPoolExhausted:
            self._uow_metrics.pool_exhausted()
            raise
        self._uow_metrics.checkout()
        return connection

    @staticmethod
    def _dict_row_factory():
        try:
            from psycopg.rows import dict_row
            return dict_row
        except ImportError:
            return None

    def _commit(self, connection: Any) -> None:
        active = self._current_uow.get()
        if active is not None and active.connection is connection:
            return
        connection.commit()
        self._uow_metrics.committed()

    def _rollback(self, connection: Any) -> None:
        active = self._current_uow.get()
        if active is not None and active.connection is connection:
            active.mark_rollback("repositoryRollback")
            return
        rollback = getattr(connection, "rollback", None)
        if callable(rollback):
            rollback()
            self._uow_metrics.rolled_back()

    def _close(self, connection: Any) -> None:
        active = self._current_uow.get()
        if active is not None and active.connection is connection:
            return
        try:
            self._pool.putconn(connection)
        except Exception:
            self._uow_metrics.return_failed()
            raise
        finally:
            self._uow_metrics.release()

    @staticmethod
    def _iso_value(value: Any) -> str:
        if value is None:
            return PostgresStore._now()
        isoformat = getattr(value, "isoformat", None)
        return isoformat() if callable(isoformat) else str(value)

    @classmethod
    def _auth_challenge_record(cls, row: Dict[str, Any]) -> Dict[str, Any]:
        record = {
            "challengeId": str(row.get("id") or ""),
            "identityType": str(row.get("identity_type") or ""),
            "targetHashKeyVersion": str(
                row.get("target_hash_key_version") or ""
            ),
            "targetHash": str(row.get("target_hash") or ""),
            "codeHash": str(row.get("code_hash") or ""),
            "providerMode": str(row.get("provider_mode") or ""),
            "purpose": str(row.get("purpose") or ""),
            "status": str(row.get("status") or ""),
            "attempts": int(row.get("attempts") or 0),
            "maxAttempts": int(row.get("max_attempts") or 0),
            "internalVerificationEnabled": bool(
                row.get("internal_verification_enabled")
            ),
            "expiresAt": cls._iso_value(row.get("expires_at")),
            "createdAt": cls._iso_value(row.get("created_at")),
            "updatedAt": cls._iso_value(row.get("updated_at")),
        }
        if row.get("consumed_at") is not None:
            record["consumedAt"] = cls._iso_value(row.get("consumed_at"))
        return record

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
