from copy import deepcopy
from datetime import datetime, timezone
from math import ceil
from threading import RLock
from typing import Any, Dict, List, Optional
import uuid

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
from app.services.archive_store import (
    ArchiveItemDeletionForbidden,
    ArchiveItemNotFound,
    ArchiveItemOwnershipConflict,
    is_sealed_time_letter,
)
from app.services.user_identity import stable_user_id


class InMemoryStore:
    def __init__(self):
        self._users: Dict[str, Dict[str, Any]] = {}
        self._kb_snapshots: Dict[str, Dict[str, Any]] = {}
        self._kb_changes: Dict[str, List[Dict[str, Any]]] = {}
        self._kb_change_feed_minimum_since_revisions: Dict[str, int] = {}
        self._kb_operation_receipts: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._kb_lock = RLock()
        self._archive_lock = RLock()
        self._memories: Dict[str, List[Dict[str, Any]]] = {}
        self._archive_items: Dict[str, List[Dict[str, Any]]] = {}
        self._mailbox_letters: Dict[str, List[Dict[str, Any]]] = {}
        self._profiles: Dict[str, Dict[str, Any]] = {}
        self._password_credentials: Dict[str, Dict[str, Any]] = {}
        self._family_members: Dict[str, List[Dict[str, Any]]] = {}
        self._care_snapshots: Dict[str, List[Dict[str, Any]]] = {}
        self._echo_delayed_replies: Dict[str, List[Dict[str, Any]]] = {}
        self._push_device_tokens: Dict[str, List[Dict[str, Any]]] = {}
        self._voice_profiles: Dict[str, List[Dict[str, Any]]] = {}
        self._voice_clone_slots: Dict[str, Dict[str, Any]] = {}
        self._digital_human_sessions: Dict[str, Dict[str, Any]] = {}
        self._auth_sessions: Dict[str, Dict[str, Any]] = {}

    def save_auth_session(self, session: Dict[str, Any]) -> Dict[str, Any]:
        item = deepcopy(session)
        self._auth_sessions[str(item["sessionId"])] = item
        return deepcopy(item)

    def get_auth_session_by_access_token_hash(self, token_hash: str) -> Optional[Dict[str, Any]]:
        session = next(
            (
                item
                for item in self._auth_sessions.values()
                if item.get("accessTokenHash") == token_hash
            ),
            None,
        )
        return None if session is None else deepcopy(session)

    def consume_auth_session_refresh(
        self,
        refresh_token_hash: str,
        consumed_at_iso: str,
    ) -> Optional[Dict[str, Any]]:
        session_id, session = next(
            (
                (key, item)
                for key, item in self._auth_sessions.items()
                if item.get("refreshTokenHash") == refresh_token_hash
            ),
            (None, None),
        )
        if session_id is None or session is None or session.get("status") != "active":
            return None
        if self._parse_iso_datetime(str(session.get("refreshExpiresAt") or "")) <= self._parse_iso_datetime(consumed_at_iso):
            expired = deepcopy(session)
            expired["status"] = "expired"
            expired["expiredAt"] = consumed_at_iso
            self._auth_sessions[session_id] = expired
            return None

        consumed = deepcopy(session)
        consumed["status"] = "rotated"
        consumed["rotatedAt"] = consumed_at_iso
        self._auth_sessions[session_id] = consumed
        return deepcopy(consumed)

    def revoke_auth_session_by_access_token_hash(
        self,
        access_token_hash: str,
        revoked_at_iso: str,
        reason: str,
    ) -> Optional[Dict[str, Any]]:
        session_id, session = next(
            (
                (key, item)
                for key, item in self._auth_sessions.items()
                if item.get("accessTokenHash") == access_token_hash
            ),
            (None, None),
        )
        if session_id is None or session is None:
            return None
        revoked = deepcopy(session)
        revoked["status"] = "revoked"
        revoked["revokedAt"] = revoked_at_iso
        revoked["revokeReason"] = reason
        self._auth_sessions[session_id] = revoked
        return deepcopy(revoked)

    def acquire_digital_human_session_lease(
        self,
        candidate: Dict[str, Any],
        *,
        max_concurrent_sessions: int,
        now_iso: str,
    ) -> Dict[str, Any]:
        item = deepcopy(candidate)
        now = self._parse_iso_datetime(now_iso)
        bounded_capacity = max(1, max_concurrent_sessions)

        for session_id, lease in list(self._digital_human_sessions.items()):
            if lease.get("status") != "active":
                continue
            expires_at = self._parse_iso_datetime(str(lease.get("expiresAt") or ""))
            if expires_at > now:
                continue
            expired = deepcopy(lease)
            expired["status"] = "expired"
            expired["expiredAt"] = now_iso
            expired["updatedAt"] = now_iso
            self._digital_human_sessions[session_id] = expired

        reusable = next(
            (
                lease
                for lease in self._digital_human_sessions.values()
                if lease.get("status") == "active"
                and self._same_digital_human_context(lease, item)
            ),
            None,
        )
        if reusable is not None:
            updated = deepcopy(reusable)
            updated["heartbeatAt"] = item.get("heartbeatAt") or now_iso
            updated["expiresAt"] = item.get("expiresAt")
            updated["updatedAt"] = now_iso
            self._digital_human_sessions[str(updated["sessionId"])] = updated
            return {
                "outcome": "reused",
                "lease": deepcopy(updated),
                "activeSessionCount": self._active_digital_human_session_count(str(item.get("resourceKey") or "")),
                "retryAfterSeconds": 0,
            }

        for session_id, lease in list(self._digital_human_sessions.items()):
            if lease.get("status") != "active":
                continue
            if lease.get("userId") != item.get("userId") or lease.get("deviceId") != item.get("deviceId"):
                continue
            released = deepcopy(lease)
            released["status"] = "released"
            released["releasedAt"] = now_iso
            released["releaseReason"] = "supersededByDeviceContext"
            released["updatedAt"] = now_iso
            self._digital_human_sessions[session_id] = released

        resource_key = str(item.get("resourceKey") or "")
        active = [
            lease
            for lease in self._digital_human_sessions.values()
            if lease.get("status") == "active" and lease.get("resourceKey") == resource_key
        ]
        if len(active) >= bounded_capacity:
            retry_after = min(
                max(1, ceil((self._parse_iso_datetime(str(lease.get("expiresAt") or "")) - now).total_seconds()))
                for lease in active
            )
            return {
                "outcome": "conflict",
                "lease": None,
                "activeSessionCount": len(active),
                "retryAfterSeconds": retry_after,
            }

        item["status"] = "active"
        item.setdefault("createdAt", now_iso)
        item.setdefault("heartbeatAt", now_iso)
        item["updatedAt"] = now_iso
        self._digital_human_sessions[str(item["sessionId"])] = item
        return {
            "outcome": "created",
            "lease": deepcopy(item),
            "activeSessionCount": len(active) + 1,
            "retryAfterSeconds": 0,
        }

    def drain_expired_digital_human_session_leases(self, *, now_iso: str) -> Dict[str, int]:
        now = self._parse_iso_datetime(now_iso)
        expired_count = 0
        for session_id, lease in list(self._digital_human_sessions.items()):
            if lease.get("status") != "active":
                continue
            if self._parse_iso_datetime(str(lease.get("expiresAt") or "")) > now:
                continue
            expired = deepcopy(lease)
            expired["status"] = "expired"
            expired["expiredAt"] = now_iso
            expired["updatedAt"] = now_iso
            self._digital_human_sessions[session_id] = expired
            expired_count += 1
        return {
            "expiredLeaseCount": expired_count,
            "activeLeaseCount": sum(
                1
                for lease in self._digital_human_sessions.values()
                if lease.get("status") == "active"
            ),
        }

    def heartbeat_digital_human_session_lease(
        self,
        session_id: str,
        *,
        user_id: str,
        device_id: str,
        heartbeat_at_iso: str,
        expires_at_iso: str,
    ) -> Optional[Dict[str, Any]]:
        lease = self._digital_human_sessions.get(session_id)
        if lease is None or lease.get("userId") != user_id or lease.get("deviceId") != device_id:
            return None
        if lease.get("status") != "active":
            return {"outcome": self._inactive_digital_human_outcome(lease), "lease": deepcopy(lease)}
        if self._parse_iso_datetime(str(lease.get("expiresAt") or "")) <= self._parse_iso_datetime(heartbeat_at_iso):
            expired = deepcopy(lease)
            expired["status"] = "expired"
            expired["expiredAt"] = heartbeat_at_iso
            expired["updatedAt"] = heartbeat_at_iso
            self._digital_human_sessions[session_id] = expired
            return {"outcome": "expired", "lease": deepcopy(expired)}

        updated = deepcopy(lease)
        updated["heartbeatAt"] = heartbeat_at_iso
        updated["expiresAt"] = expires_at_iso
        updated["updatedAt"] = heartbeat_at_iso
        self._digital_human_sessions[session_id] = updated
        return {"outcome": "active", "lease": deepcopy(updated)}

    def release_digital_human_session_lease(
        self,
        session_id: str,
        *,
        user_id: str,
        device_id: str,
        released_at_iso: str,
        reason: str,
    ) -> Optional[Dict[str, Any]]:
        lease = self._digital_human_sessions.get(session_id)
        if lease is None or lease.get("userId") != user_id or lease.get("deviceId") != device_id:
            return None
        if lease.get("status") == "released":
            return {"outcome": "alreadyReleased", "lease": deepcopy(lease)}
        if lease.get("status") == "expired":
            return {"outcome": "alreadyExpired", "lease": deepcopy(lease)}

        updated = deepcopy(lease)
        updated["status"] = "released"
        updated["releasedAt"] = released_at_iso
        updated["releaseReason"] = reason
        updated["updatedAt"] = released_at_iso
        self._digital_human_sessions[session_id] = updated
        return {"outcome": "released", "lease": deepcopy(updated)}

    def get_digital_human_session_lease(self, session_id: str) -> Optional[Dict[str, Any]]:
        lease = self._digital_human_sessions.get(session_id)
        return None if lease is None else deepcopy(lease)

    def upsert_user(self, phone: str, nickname: str) -> Dict[str, Any]:
        user_id = stable_user_id(phone)
        existing = self._users.get(user_id, {})
        user = {
            "id": user_id,
            "phone": phone,
            "nickname": nickname or "寻梦环游用户",
            "updatedAt": self._now(),
        }
        if existing.get("restoreCount") is not None:
            user["restoreCount"] = int(existing.get("restoreCount") or 0)
        user.setdefault("restoreCount", 0)
        user["deletionState"] = "active"
        self._users[user_id] = user
        return deepcopy(user)

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        user = self._users.get(user_id)
        return None if user is None else deepcopy(user)

    def soft_delete_user(
        self,
        user_id: str,
        *,
        phone: str,
        requested_at_iso: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        user = self._users.get(user_id)
        if user is None:
            return None
        if self._normalized_phone(user.get("phone", "")) != self._normalized_phone(phone):
            return None

        requested_at = self._parse_iso_datetime(requested_at_iso) if requested_at_iso else datetime.now(timezone.utc)
        deleted_at = requested_at.isoformat()
        purge_after = (requested_at + self._account_delete_retention_delta()).isoformat()
        item = deepcopy(user)
        item["deletionState"] = "softDeleted"
        item["deletedAt"] = deleted_at
        item["purgeAfter"] = purge_after
        item["restoreDeadline"] = purge_after
        item["retentionDays"] = 30
        item["dataExportSupported"] = False
        item["restoreLimit"] = 1
        item["restoreCount"] = int(item.get("restoreCount") or 0)
        item["updatedAt"] = self._now()
        self._users[user_id] = item
        return deepcopy(item)

    def restore_user(
        self,
        user_id: str,
        *,
        phone: str,
        nickname: str = "",
        restored_at_iso: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        user = self._users.get(user_id)
        if user is None:
            return None
        if self._normalized_phone(user.get("phone", "")) != self._normalized_phone(phone):
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
        self._users[user_id] = item
        return deepcopy(item)

    def purge_expired_deleted_users(self, cutoff_iso: str) -> List[Dict[str, Any]]:
        cutoff = self._parse_iso_datetime(cutoff_iso)
        purged: List[Dict[str, Any]] = []
        for user_id, user in list(self._users.items()):
            if user.get("deletionState") != "softDeleted":
                continue
            deadline = self._parse_iso_datetime(str(user.get("restoreDeadline") or user.get("purgeAfter") or ""))
            if deadline > cutoff:
                continue
            tombstone = {
                "id": user_id,
                "phone": user.get("phone", ""),
                "nickname": "",
                "deletionState": "purged",
                "purgedAt": cutoff.isoformat(),
                "restoreCount": int(user.get("restoreCount") or 0),
            }
            self._users[user_id] = tombstone
            self._profiles.pop(user_id, None)
            self._password_credentials.pop(user_id, None)
            self._kb_snapshots.pop(user_id, None)
            self._kb_changes.pop(user_id, None)
            self._kb_change_feed_minimum_since_revisions.pop(user_id, None)
            self._kb_operation_receipts.pop(user_id, None)
            self._memories.pop(user_id, None)
            self._archive_items.pop(user_id, None)
            self._mailbox_letters.pop(user_id, None)
            self._family_members.pop(user_id, None)
            self._care_snapshots.pop(user_id, None)
            self._echo_delayed_replies.pop(user_id, None)
            self._push_device_tokens.pop(user_id, None)
            self._digital_human_sessions = {
                session_id: lease
                for session_id, lease in self._digital_human_sessions.items()
                if lease.get("userId") != user_id
            }
            self._auth_sessions = {
                session_id: session
                for session_id, session in self._auth_sessions.items()
                if session.get("userId") != user_id
            }
            for slot in self._voice_clone_slots.values():
                if slot.get("userId") != user_id:
                    continue
                slot["status"] = "retired"
                slot["updatedAt"] = cutoff.isoformat()
            self._voice_profiles.pop(user_id, None)
            purged.append(deepcopy(tombstone))
        return purged

    def save_profile(self, user_id: str, profile: Dict[str, Any]) -> Dict[str, Any]:
        item = deepcopy(profile)
        item["userId"] = user_id
        item["updatedAt"] = self._now()
        self._profiles[user_id] = item
        return deepcopy(item)

    def get_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        profile = self._profiles.get(user_id)
        return None if profile is None else deepcopy(profile)

    def save_password_credential(self, user_id: str, credential: Dict[str, Any]) -> Dict[str, Any]:
        item = deepcopy(credential)
        item["userId"] = user_id
        item["updatedAt"] = self._now()
        self._password_credentials[user_id] = item
        return deepcopy(item)

    def get_password_credential(self, user_id: str) -> Optional[Dict[str, Any]]:
        credential = self._password_credentials.get(user_id)
        return None if credential is None else deepcopy(credential)

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
        with self._kb_lock:
            self._kb_change_feed_minimum_since_revisions.setdefault(user_id, 0)
            receipt = self._kb_operation_receipts.get(user_id, {}).get(operation_id)
            if receipt is not None:
                verify_knowledge_operation_receipt(
                    receipt,
                    operation_kind=operation_kind,
                    payload_hash=payload_hash,
                )
                result = self._rebuild_kb_operation_receipt_locked(
                    user_id,
                    operation_id,
                    receipt["result"],
                )
                result["duplicate"] = True
                result["operationPayloadVerified"] = True
                return result

            changes = self._kb_changes.setdefault(user_id, [])
            existing = next((item for item in changes if item["operationId"] == operation_id), None)
            if existing is not None:
                stored_mutation = existing.get("mutation")
                return {
                    "userId": user_id,
                    "graph": deepcopy(existing["graph"]),
                    "revision": existing["revision"],
                    "updatedAt": existing["createdAt"],
                    "operationId": operation_id,
                    "duplicate": True,
                    "operationPayloadVerified": False,
                    "mutationSchemaVersion": 2 if stored_mutation is not None else 1,
                    "mutation": deepcopy(stored_mutation),
                }

            current = self._kb_snapshots.get(user_id)
            current_revision = int((current or {}).get("revision") or 0)
            if base_revision is not None and base_revision != current_revision:
                if allow_revision_noop and current is not None:
                    result = {
                        **deepcopy(current),
                        "operationId": operation_id,
                        "duplicate": False,
                        "operationPayloadVerified": True,
                        "mutationSchemaVersion": 1,
                        "mutation": None,
                        "compatibilityNoOp": True,
                    }
                    if record_compatibility_noop_receipt:
                        self._store_kb_operation_receipt_locked(
                            user_id,
                            operation_id,
                            operation_kind,
                            schema_version,
                            payload_hash,
                            result,
                            governance_summary=receipt_governance_summary,
                        )
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
            updated_at = self._now()
            snapshot = {
                "userId": user_id,
                "graph": deepcopy(next_graph),
                "revision": revision,
                "updatedAt": updated_at,
            }
            change = {
                "revision": revision,
                "operationId": operation_id,
                "graph": deepcopy(next_graph),
                "createdAt": updated_at,
                "mutationSchemaVersion": 2 if normalized_mutation is not None else 1,
                "mutation": deepcopy(normalized_mutation),
            }
            result = {
                **deepcopy(snapshot),
                "operationId": operation_id,
                "duplicate": False,
                "operationPayloadVerified": True,
                "mutationSchemaVersion": change["mutationSchemaVersion"],
                "mutation": deepcopy(normalized_mutation),
            }
            self._kb_snapshots[user_id] = snapshot
            changes.append(change)
            self._store_kb_operation_receipt_locked(
                user_id,
                operation_id,
                operation_kind,
                schema_version,
                payload_hash,
                result,
                governance_summary=receipt_governance_summary,
            )
            return result

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
        with self._kb_lock:
            receipt = self._kb_operation_receipts.get(user_id, {}).get(operation_id)
            if receipt is not None:
                verify_knowledge_operation_receipt(
                    receipt,
                    operation_kind=operation_kind,
                    payload_hash=payload_hash,
                )
                result = self._rebuild_kb_operation_receipt_locked(
                    user_id,
                    operation_id,
                    receipt["result"],
                )
                result["duplicate"] = True
                result["operationPayloadVerified"] = True
                return result
            existing = next(
                (
                    item
                    for item in self._kb_changes.get(user_id, [])
                    if item["operationId"] == operation_id
                ),
                None,
            )
            if existing is None:
                return None
            stored_mutation = existing.get("mutation")
            return {
                "userId": user_id,
                "graph": deepcopy(existing["graph"]),
                "revision": existing["revision"],
                "updatedAt": existing["createdAt"],
                "operationId": operation_id,
                "duplicate": True,
                "operationPayloadVerified": False,
                "mutationSchemaVersion": 2 if stored_mutation is not None else 1,
                "mutation": deepcopy(stored_mutation),
            }

    def _store_kb_operation_receipt_locked(
        self,
        user_id: str,
        operation_id: str,
        operation_kind: str,
        schema_version: int,
        payload_hash: str,
        result: Dict[str, Any],
        *,
        governance_summary: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._kb_operation_receipts.setdefault(user_id, {})[operation_id] = {
            "operationKind": operation_kind,
            "schemaVersion": schema_version,
            "payloadHash": payload_hash,
            "result": compact_knowledge_operation_receipt_result(
                result,
                operation_id=operation_id,
                operation_kind=operation_kind,
                governance_summary=governance_summary,
            ),
        }

    def _rebuild_kb_operation_receipt_locked(
        self,
        user_id: str,
        operation_id: str,
        receipt_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not is_compact_knowledge_operation_receipt_result(receipt_result):
            return deepcopy(receipt_result)
        change = next(
            (
                item
                for item in self._kb_changes.get(user_id, [])
                if item.get("operationId") == operation_id
            ),
            None,
        )
        snapshot = self._kb_snapshots.get(user_id)
        return rebuild_compact_knowledge_operation_result(
            receipt_result,
            user_id=user_id,
            operation_id=operation_id,
            change=None if change is None else deepcopy(change),
            snapshot=None if snapshot is None else deepcopy(snapshot),
        )

    def get_kb_snapshot(self, user_id: str) -> Optional[Dict[str, Any]]:
        with self._kb_lock:
            snapshot = self._kb_snapshots.get(user_id)
            if snapshot is None:
                return None
            return deepcopy(snapshot["graph"])

    def get_kb_snapshot_record(self, user_id: str) -> Optional[Dict[str, Any]]:
        with self._kb_lock:
            snapshot = self._kb_snapshots.get(user_id)
            return None if snapshot is None else deepcopy(snapshot)

    def list_kb_changes(
        self,
        user_id: str,
        since_revision: int,
        through_revision: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        with self._kb_lock:
            return self._list_kb_changes_locked(
                user_id,
                since_revision,
                through_revision=through_revision,
                limit=limit,
            )

    def get_kb_change_page(
        self,
        user_id: str,
        since_revision: int,
        through_revision: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        with self._kb_lock:
            snapshot = self._kb_snapshots.get(user_id)
            return {
                "currentRevision": int((snapshot or {}).get("revision") or 0),
                "minimumSinceRevision": int(
                    self._kb_change_feed_minimum_since_revisions.get(user_id, 0)
                ),
                "changes": self._list_kb_changes_locked(
                    user_id,
                    since_revision,
                    through_revision=through_revision,
                    limit=limit,
                ),
            }

    def _list_kb_changes_locked(
        self,
        user_id: str,
        since_revision: int,
        *,
        through_revision: Optional[int],
        limit: Optional[int],
    ) -> List[Dict[str, Any]]:
        changes = []
        stored_changes = sorted(
            self._kb_changes.get(user_id, []),
            key=lambda item: int(item.get("revision") or 0),
        )
        for stored in stored_changes:
            revision = int(stored.get("revision") or 0)
            if revision <= since_revision:
                continue
            if through_revision is not None and revision > through_revision:
                continue
            item = deepcopy(stored)
            item.setdefault(
                "mutationSchemaVersion",
                2 if item.get("mutation") is not None else 1,
            )
            item.setdefault("mutation", None)
            changes.append(item)
            if limit is not None and len(changes) >= limit:
                break
        return changes

    def add_memory(self, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        item = deepcopy(payload)
        item.setdefault("id", f"memory_{len(self._memories.get(user_id, [])) + 1}")
        item["userId"] = user_id
        item["createdAt"] = self._now()
        self._memories.setdefault(user_id, []).insert(0, item)
        return deepcopy(item)

    def list_memories(self, user_id: str) -> List[Dict[str, Any]]:
        return deepcopy(self._memories.get(user_id, []))

    def add_archive_item(self, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        item = deepcopy(payload)
        item.setdefault("id", f"archive_{len(self._archive_items.get(user_id, [])) + 1}")
        item["userId"] = user_id
        with self._archive_lock:
            conflicting_owner = next(
                (
                    owner_id
                    for owner_id, owner_items in self._archive_items.items()
                    if owner_id != user_id
                    and any(entry.get("id") == item["id"] for entry in owner_items)
                ),
                None,
            )
            if conflicting_owner is not None:
                raise ArchiveItemOwnershipConflict("archive item id belongs to another owner")
            items = self._archive_items.setdefault(user_id, [])
            existing = next((entry for entry in items if entry.get("id") == item["id"]), None)
            if existing is not None:
                item.setdefault("createdAt", existing.get("createdAt") or self._now())
            else:
                item.setdefault("createdAt", self._now())
            item.setdefault("updatedAt", self._now())
            items[:] = [entry for entry in items if entry.get("id") != item["id"]]
            items.insert(0, item)
            return deepcopy(item)

    def list_archive_items(self, user_id: str) -> List[Dict[str, Any]]:
        with self._archive_lock:
            return deepcopy(self._archive_items.get(user_id, []))

    def delete_archive_item(self, user_id: str, item_id: str) -> Optional[Dict[str, Any]]:
        with self._archive_lock:
            items = self._archive_items.get(user_id, [])
            for index, item in enumerate(items):
                if item.get("id") == item_id:
                    return deepcopy(items.pop(index))
            return None

    def delete_archive_item_with_kb_mutation(
        self,
        user_id: str,
        item_id: str,
        *,
        operation_id: str,
        base_revision: int,
        mutation: Optional[Dict[str, Any]] = None,
        governance_summary: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        operation_kind = KB_OPERATION_ARCHIVE_DELETE
        schema_version = 1
        payload_hash = knowledge_operation_payload_fingerprint(
            operation_kind,
            schema_version,
            {"itemId": item_id},
        )
        with self._archive_lock, self._kb_lock:
            self._kb_change_feed_minimum_since_revisions.setdefault(user_id, 0)
            receipt = self._kb_operation_receipts.get(user_id, {}).get(operation_id)
            if receipt is not None:
                verify_knowledge_operation_receipt(
                    receipt,
                    operation_kind=operation_kind,
                    payload_hash=payload_hash,
                )
                result = self._rebuild_kb_operation_receipt_locked(
                    user_id,
                    operation_id,
                    receipt["result"],
                )
                result["item"] = None
                result["duplicate"] = True
                result["operationPayloadVerified"] = True
                return result

            existing = next(
                (
                    change
                    for change in self._kb_changes.get(user_id, [])
                    if change["operationId"] == operation_id
                ),
                None,
            )
            if existing is not None:
                stored_mutation = existing.get("mutation")
                return {
                    "item": None,
                    "duplicate": True,
                    "operationPayloadVerified": False,
                    "revision": int(existing["revision"]),
                    "graph": deepcopy(existing["graph"]),
                    "mutationSchemaVersion": 2 if stored_mutation is not None else 1,
                    "mutation": deepcopy(stored_mutation),
                }

            items = self._archive_items.get(user_id, [])
            item_index = next(
                (index for index, item in enumerate(items) if item.get("id") == item_id),
                None,
            )
            if item_index is None:
                raise ArchiveItemNotFound("archive item not found")
            item = items[item_index]
            if is_sealed_time_letter(item):
                raise ArchiveItemDeletionForbidden("sealed timeLetter cannot be deleted")

            current = self._kb_snapshots.get(user_id)
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
                updated_at = self._now()
                self._kb_snapshots[user_id] = {
                    "userId": user_id,
                    "graph": deepcopy(graph),
                    "revision": revision,
                    "updatedAt": updated_at,
                }
                self._kb_changes.setdefault(user_id, []).append(
                    {
                        "revision": revision,
                        "operationId": operation_id,
                        "graph": deepcopy(graph),
                        "createdAt": updated_at,
                        "mutationSchemaVersion": 2,
                        "mutation": deepcopy(normalized_mutation),
                    }
                )

            deleted = deepcopy(items.pop(item_index))
            result = {
                "item": deleted,
                "duplicate": False,
                "operationPayloadVerified": True,
                "revision": revision,
                "graph": graph,
                "mutationSchemaVersion": 2 if normalized_mutation is not None else None,
                "mutation": deepcopy(normalized_mutation),
            }
            receipt_result = {**deepcopy(result), "item": None}
            self._store_kb_operation_receipt_locked(
                user_id,
                operation_id,
                operation_kind,
                schema_version,
                payload_hash,
                receipt_result,
                governance_summary=governance_summary,
            )
            return result

    def mark_due_time_letters_delivered(
        self,
        cutoff_iso: str,
        delivered_at_iso: str,
        limit: int = 25,
    ) -> List[Dict[str, Any]]:
        due: List[Dict[str, Any]] = []
        bounded_limit = max(1, min(limit, 100))
        for user_id, items in self._archive_items.items():
            for index, item in enumerate(items):
                if len(due) >= bounded_limit:
                    break
                if not self._is_due_scheduled_time_letter(item, cutoff_iso):
                    continue
                updated = self._mark_time_letter_delivered(item, user_id, delivered_at_iso)
                items[index] = updated
                due.append(deepcopy(updated))
            if len(due) >= bounded_limit:
                break
        return sorted(due, key=lambda item: str(item.get("openAt") or item.get("metadata", {}).get("openAt") or ""))

    def add_mailbox_letter(self, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        item = deepcopy(payload)
        item.setdefault("id", f"mailbox_{len(self._mailbox_letters.get(user_id, [])) + 1}")
        item["userId"] = user_id
        item["updatedAt"] = self._now()
        item.setdefault("createdAt", item["updatedAt"])

        letters = self._mailbox_letters.setdefault(user_id, [])
        letters[:] = [letter for letter in letters if letter.get("id") != item["id"]]
        letters.insert(0, item)
        return deepcopy(item)

    def list_mailbox_letters(self, user_id: str) -> List[Dict[str, Any]]:
        return deepcopy(self._mailbox_letters.get(user_id, []))

    def mark_mailbox_letter_read(
        self,
        user_id: str,
        letter_id: str,
        read_at_iso: str,
    ) -> Optional[Dict[str, Any]]:
        letters = self._mailbox_letters.get(user_id, [])
        for index, letter in enumerate(letters):
            if str(letter.get("id") or "") != letter_id:
                continue
            updated = deepcopy(letter)
            updated["status"] = "read"
            updated["readAt"] = read_at_iso
            updated["updatedAt"] = read_at_iso
            letters[index] = updated
            return deepcopy(updated)
        return None

    def archive_mailbox_letter(
        self,
        user_id: str,
        letter_id: str,
        archived_at_iso: str,
    ) -> Optional[Dict[str, Any]]:
        letters = self._mailbox_letters.get(user_id, [])
        for index, letter in enumerate(letters):
            if str(letter.get("id") or "") != letter_id:
                continue
            updated = deepcopy(letter)
            updated["status"] = "archived"
            updated["archivedAt"] = archived_at_iso
            updated["updatedAt"] = archived_at_iso
            letters[index] = updated
            return deepcopy(updated)
        return None

    def add_echo_delayed_reply(self, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        item = deepcopy(payload)
        item.setdefault("id", item.get("delayedReplyId") or f"echo_delayed_{len(self._echo_delayed_replies.get(user_id, [])) + 1}")
        item["userId"] = user_id
        item["createdAt"] = self._now()

        replies = self._echo_delayed_replies.setdefault(user_id, [])
        replies[:] = [reply for reply in replies if reply.get("id") != item["id"]]
        replies.insert(0, item)
        return deepcopy(item)

    def list_echo_delayed_replies(self, user_id: str) -> List[Dict[str, Any]]:
        return deepcopy(self._echo_delayed_replies.get(user_id, []))

    def mark_due_echo_delayed_replies_for_dispatch(
        self,
        cutoff_iso: str,
        dispatched_at_iso: str,
        limit: int = 25,
    ) -> List[Dict[str, Any]]:
        due: List[Dict[str, Any]] = []
        bounded_limit = max(1, min(limit, 100))
        for user_id, replies in self._echo_delayed_replies.items():
            for index, reply in enumerate(replies):
                if len(due) >= bounded_limit:
                    break
                if reply.get("deliveryState") != "scheduled":
                    continue
                deliver_at = str(reply.get("deliverAt") or "")
                if not deliver_at or deliver_at > cutoff_iso:
                    continue
                updated = deepcopy(reply)
                updated["userId"] = user_id
                updated["deliveryState"] = "readyForProvider"
                updated["pushProviderState"] = "queued"
                updated["dispatchAttemptedAt"] = dispatched_at_iso
                updated["providerDeliveryAttempted"] = False
                replies[index] = updated
                due.append(deepcopy(updated))
            if len(due) >= bounded_limit:
                break
        return sorted(due, key=lambda item: str(item.get("deliverAt") or ""))

    @staticmethod
    def _is_due_scheduled_time_letter(item: Dict[str, Any], cutoff_iso: str) -> bool:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        if str(item.get("kind") or "").strip() != "timeLetter":
            return False
        delivery_state = str(item.get("deliveryState") or metadata.get("deliveryState") or "").strip()
        if delivery_state != "sealed":
            return False
        delivery_status = str(
            item.get("deliveryStatus")
            or metadata.get("deliveryStatus")
            or metadata.get("deliveryExecutionState")
            or ""
        ).strip()
        if delivery_status != "scheduled":
            return False
        open_at = str(item.get("openAt") or metadata.get("openAt") or "").strip()
        return bool(open_at) and open_at <= cutoff_iso

    @staticmethod
    def _mark_time_letter_delivered(
        item: Dict[str, Any],
        user_id: str,
        delivered_at_iso: str,
    ) -> Dict[str, Any]:
        updated = deepcopy(item)
        metadata = deepcopy(updated.get("metadata") if isinstance(updated.get("metadata"), dict) else {})
        updated["userId"] = user_id
        updated["deliveryStatus"] = "delivered"
        updated["deliveryExecutionState"] = "delivered"
        updated["deliveryScheduleState"] = "dispatched"
        updated["deliveryProviderState"] = "local_notification_and_in_app"
        updated["deliveredAt"] = delivered_at_iso
        updated["dispatchAttemptedAt"] = delivered_at_iso
        updated["providerDeliveryAttempted"] = False
        metadata["deliveryStatus"] = "delivered"
        metadata["deliveryExecutionState"] = "delivered"
        metadata["deliveryScheduleState"] = "dispatched"
        metadata["deliveryProviderState"] = "local_notification_and_in_app"
        metadata["deliveredAt"] = delivered_at_iso
        metadata["dispatchAttemptedAt"] = delivered_at_iso
        updated["metadata"] = metadata
        updated["updatedAt"] = delivered_at_iso
        return updated

    def save_push_device_token(self, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        item = deepcopy(payload)
        item.setdefault("id", item.get("deviceTokenId") or f"push_token_{len(self._push_device_tokens.get(user_id, [])) + 1}")
        item.setdefault("deviceTokenId", item["id"])
        item["userId"] = user_id
        item["updatedAt"] = self._now()

        tokens = self._push_device_tokens.setdefault(user_id, [])
        tokens[:] = [token for token in tokens if token.get("id") != item["id"]]
        tokens.insert(0, item)
        return deepcopy(item)

    def list_push_device_tokens(self, user_id: str) -> List[Dict[str, Any]]:
        return deepcopy(self._push_device_tokens.get(user_id, []))

    def save_voice_profile(self, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        item = deepcopy(payload)
        item["userId"] = user_id
        item.setdefault("id", item.get("voiceProfileId") or f"voice_profile_{len(self._voice_profiles.get(user_id, [])) + 1}")
        item.setdefault("voiceProfileId", item["id"])
        item["updatedAt"] = self._now()

        for existing_user_id, existing_profiles in self._voice_profiles.items():
            if existing_user_id == user_id:
                continue
            if any(profile.get("voiceProfileId") == item["voiceProfileId"] for profile in existing_profiles):
                raise ValueError("voiceProfileId is already owned by another user")

        profiles = self._voice_profiles.setdefault(user_id, [])
        profiles[:] = [profile for profile in profiles if profile.get("voiceProfileId") != item["voiceProfileId"]]
        profiles.insert(0, item)
        return deepcopy(item)

    def list_voice_profiles(self, user_id: str) -> List[Dict[str, Any]]:
        return deepcopy(self._voice_profiles.get(user_id, []))

    def get_voice_profile(self, user_id: str, voice_profile_id: str) -> Optional[Dict[str, Any]]:
        profiles = self._voice_profiles.get(user_id, [])
        for profile in profiles:
            if profile.get("voiceProfileId") == voice_profile_id:
                return deepcopy(profile)
        return None

    def allocate_voice_clone_slot(
        self,
        provider_speaker_ids: List[str],
        *,
        user_id: str,
        voice_profile_id: str,
        persona_scope: str,
        digital_human_id: str,
    ) -> Optional[Dict[str, Any]]:
        now = self._now()
        configured_ids = [speaker_id.strip() for speaker_id in provider_speaker_ids if speaker_id.strip()]
        for provider_speaker_id in configured_ids:
            slot = self._voice_clone_slots.setdefault(
                provider_speaker_id,
                {
                    "providerSpeakerId": provider_speaker_id,
                    "voiceProfileId": None,
                    "userId": None,
                    "personaScope": None,
                    "digitalHumanId": None,
                    "status": "available",
                    "trainingAttempts": 0,
                    "configured": True,
                    "assignedAt": None,
                    "updatedAt": now,
                },
            )
            slot["configured"] = True

        existing = next(
            (
                slot
                for slot in self._voice_clone_slots.values()
                if slot.get("voiceProfileId") == voice_profile_id
                and slot.get("providerSpeakerId") in configured_ids
                and slot.get("status") not in {"retired", "deleted"}
            ),
            None,
        )
        if existing is not None:
            if existing.get("userId") != user_id:
                return None
            return deepcopy(existing)

        available = next(
            (
                self._voice_clone_slots[provider_speaker_id]
                for provider_speaker_id in configured_ids
                if self._voice_clone_slots[provider_speaker_id].get("configured") is True
                and self._voice_clone_slots[provider_speaker_id].get("voiceProfileId") is None
                and self._voice_clone_slots[provider_speaker_id].get("status") == "available"
            ),
            None,
        )
        if available is None:
            return None

        available.update(
            {
                "voiceProfileId": voice_profile_id,
                "userId": user_id,
                "personaScope": persona_scope,
                "digitalHumanId": digital_human_id,
                "status": "assigned",
                "assignedAt": now,
                "updatedAt": now,
            }
        )
        return deepcopy(available)

    def get_voice_clone_slot(self, voice_profile_id: str) -> Optional[Dict[str, Any]]:
        for slot in self._voice_clone_slots.values():
            if slot.get("voiceProfileId") == voice_profile_id:
                return deepcopy(slot)
        return None

    def list_voice_clone_slots(self) -> List[Dict[str, Any]]:
        return deepcopy(list(self._voice_clone_slots.values()))

    def update_voice_clone_slot(
        self,
        voice_profile_id: str,
        *,
        status: str,
        increment_training_attempts: bool = False,
    ) -> Optional[Dict[str, Any]]:
        for slot in self._voice_clone_slots.values():
            if slot.get("voiceProfileId") != voice_profile_id:
                continue
            slot["status"] = status
            if increment_training_attempts:
                slot["trainingAttempts"] = int(slot.get("trainingAttempts") or 0) + 1
            slot["updatedAt"] = self._now()
            return deepcopy(slot)
        return None

    def add_family_member(self, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        item = deepcopy(payload)
        item.setdefault("id", f"family_{len(self._family_members.get(user_id, [])) + 1}")
        item.setdefault("invitationCode", "")
        item.setdefault("invitationURL", "")
        item["userId"] = user_id
        item["ownerUserId"] = user_id
        item["createdAt"] = self._now()
        self._family_members.setdefault(user_id, []).append(item)
        return deepcopy(item)

    def list_family_members(self, user_id: str) -> List[Dict[str, Any]]:
        return deepcopy(self._family_members.get(user_id, []))

    def accept_family_member(self, user_id: str, member_id: str, phone: str) -> Optional[Dict[str, Any]]:
        members = self._family_members.get(user_id, [])
        normalized_phone = self._normalized_phone(phone)
        for index, item in enumerate(members):
            if item.get("id") != member_id:
                continue
            expected_phone = self._normalized_phone(str(item.get("phone") or ""))
            if expected_phone and normalized_phone != expected_phone:
                return None
            if item.get("accessStatus") == "revoked" or item.get("invitationStatus") == "revoked":
                return None
            if item.get("accessStatus") == "active" and item.get("invitationStatus") == "accepted":
                accepted = deepcopy(item)
                accepted["ownerUserId"] = user_id
                return accepted
            accepted = deepcopy(item)
            accepted["accessStatus"] = "active"
            accepted["invitationStatus"] = "accepted"
            accepted["isOnline"] = True
            accepted["acceptedAt"] = self._now()
            accepted["lastUpdated"] = "刚刚接受邀请"
            accepted["ownerUserId"] = user_id
            members[index] = accepted
            return deepcopy(accepted)
        return None

    def accept_family_invitation_code(self, invitation_code: str, phone: str) -> Optional[Dict[str, Any]]:
        normalized_code = invitation_code.strip()
        if not normalized_code:
            return None
        normalized_phone = self._normalized_phone(phone)
        for user_id, members in self._family_members.items():
            for index, item in enumerate(members):
                if str(item.get("invitationCode") or "").strip() != normalized_code:
                    continue
                expected_phone = self._normalized_phone(str(item.get("phone") or ""))
                if expected_phone and normalized_phone != expected_phone:
                    return None
                if item.get("accessStatus") == "revoked" or item.get("invitationStatus") == "revoked":
                    return None
                if item.get("accessStatus") == "active" and item.get("invitationStatus") == "accepted":
                    accepted = deepcopy(item)
                    accepted["ownerUserId"] = user_id
                    return accepted
                accepted = deepcopy(item)
                accepted["accessStatus"] = "active"
                accepted["invitationStatus"] = "accepted"
                accepted["isOnline"] = True
                accepted["acceptedAt"] = self._now()
                accepted["lastUpdated"] = "刚刚接受邀请"
                accepted["ownerUserId"] = user_id
                members[index] = accepted
                return deepcopy(accepted)
        return None

    def revoke_family_member(self, user_id: str, member_id: str) -> Optional[Dict[str, Any]]:
        members = self._family_members.get(user_id, [])
        for index, item in enumerate(members):
            if item.get("id") != member_id:
                continue
            revoked = deepcopy(item)
            revoked["accessStatus"] = "revoked"
            revoked["invitationStatus"] = "revoked"
            revoked["isOnline"] = False
            revoked["revokedAt"] = self._now()
            revoked["lastUpdated"] = "访问已撤回"
            members[index] = revoked
            return deepcopy(revoked)
        return None

    def save_care_snapshot(
        self,
        user_id: str,
        snapshot: Dict[str, Any],
        viewer_family_member_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        item = {
            "id": f"care_{len(self._care_snapshots.get(user_id, [])) + 1}",
            "userId": user_id,
            "viewerFamilyMemberID": viewer_family_member_id,
            "snapshot": deepcopy(snapshot),
            "createdAt": self._now(),
        }
        self._care_snapshots.setdefault(user_id, []).insert(0, item)
        return deepcopy(item)

    def get_latest_care_snapshot(
        self,
        user_id: str,
        viewer_family_member_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        snapshots = self._care_snapshots.get(user_id, [])
        for item in snapshots:
            if item.get("viewerFamilyMemberID") == viewer_family_member_id:
                return deepcopy(item)
        return None

    def list_care_snapshots(
        self,
        user_id: str,
        viewer_family_member_id: Optional[str] = None,
        limit: int = 7,
    ) -> List[Dict[str, Any]]:
        snapshots = self._care_snapshots.get(user_id, [])
        filtered = [
            item for item in snapshots
            if item.get("viewerFamilyMemberID") == viewer_family_member_id
        ]
        return deepcopy(filtered[:max(1, min(limit, 30))])

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _normalized_phone(phone: str) -> str:
        return "".join(ch for ch in phone if ch.isdigit())

    @staticmethod
    def _account_delete_retention_delta():
        from datetime import timedelta
        return timedelta(days=30)

    @staticmethod
    def _parse_iso_datetime(value: str) -> datetime:
        if not value:
            return datetime.min.replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    @staticmethod
    def _same_digital_human_context(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
        return all(
            left.get(key) == right.get(key)
            for key in ("resourceKey", "userId", "deviceId", "personaId", "scene", "lifecycleMode")
        )

    def _active_digital_human_session_count(self, resource_key: str) -> int:
        return sum(
            1
            for lease in self._digital_human_sessions.values()
            if lease.get("status") == "active" and lease.get("resourceKey") == resource_key
        )

    @staticmethod
    def _inactive_digital_human_outcome(lease: Dict[str, Any]) -> str:
        if lease.get("status") == "released":
            return "alreadyReleased"
        if lease.get("status") == "expired":
            return "expired"
        return "inactive"
