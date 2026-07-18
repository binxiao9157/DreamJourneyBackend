from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from math import ceil
import secrets
from threading import RLock
from typing import Any, Dict, Iterable, List, Optional, Tuple
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
from app.services.data_rights_contract import (
    DataRightsCommandConflict,
    DataRightsExecutionConflict,
    DataRightsRequest,
    EXECUTION_OUTCOMES,
    aggregate_data_rights_status,
)
from app.services.account_deletion_state import (
    account_purge_block_reason,
    account_restore_block_reason,
    guard_account_upsert,
)
from app.services.account_deletion_receipts import (
    account_purge_subject_hash,
    build_account_purge_receipt,
)
from app.services.data_rights_module_inventory import record_terminal_cleanup_plan
from app.services.archive_store import (
    ArchiveItemDeletionForbidden,
    ArchiveItemNotFound,
    ArchiveItemOwnershipConflict,
    ResourceOwnershipConflict,
    is_sealed_time_letter,
)
from app.services.user_identity import stable_user_id
from app.observability.events import (
    EvidenceEventConflict,
    canonicalize_evidence_event,
    hash_evidence_identifier,
    normalize_evidence_timestamp,
    normalize_machine_code,
    normalize_retention_class,
)
from app.observability.operation_metrics import summarize_operation_metrics


class InMemoryStore:
    def __init__(self):
        self._users: Dict[str, Dict[str, Any]] = {}
        self._kb_snapshots: Dict[str, Dict[str, Any]] = {}
        self._kb_changes: Dict[str, List[Dict[str, Any]]] = {}
        self._kb_change_feed_minimum_since_revisions: Dict[str, int] = {}
        self._kb_operation_receipts: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._kb_lock = RLock()
        self._archive_lock = RLock()
        self._evidence_lock = RLock()
        self._memories: Dict[str, List[Dict[str, Any]]] = {}
        self._archive_items: Dict[str, List[Dict[str, Any]]] = {}
        self._mailbox_letters: Dict[str, List[Dict[str, Any]]] = {}
        self._profiles: Dict[str, Dict[str, Any]] = {}
        self._password_credentials: Dict[str, Dict[str, Any]] = {}
        self._family_members: Dict[str, List[Dict[str, Any]]] = {}
        self._family_relationships: Dict[str, Dict[str, Any]] = {}
        self._access_grants: Dict[str, Dict[str, Any]] = {}
        self._grant_events: Dict[str, List[Dict[str, Any]]] = {}
        self._delegated_access_lock = RLock()
        self._care_snapshots: Dict[str, List[Dict[str, Any]]] = {}
        self._echo_delayed_replies: Dict[str, List[Dict[str, Any]]] = {}
        self._push_device_tokens: Dict[str, List[Dict[str, Any]]] = {}
        self._voice_profiles: Dict[str, List[Dict[str, Any]]] = {}
        self._voice_clone_slots: Dict[str, Dict[str, Any]] = {}
        self._digital_human_sessions: Dict[str, Dict[str, Any]] = {}
        self._auth_sessions: Dict[str, Dict[str, Any]] = {}
        self._auth_token_families: Dict[str, Dict[str, Any]] = {}
        self._auth_session_events: Dict[str, Dict[str, Any]] = {}
        self._auth_lock = RLock()
        self._auth_challenges: Dict[str, Dict[str, Any]] = {}
        self._identity_hash_key_versions: Dict[str, str] = {}
        self._subjects: Dict[str, Dict[str, Any]] = {}
        self._identity_bindings: Dict[str, Dict[str, Any]] = {}
        self._identity_binding_ids_by_target: Dict[Tuple[str, str, str], str] = {}
        self._identity_proofs: Dict[str, Dict[str, Any]] = {}
        self._identity_lock = RLock()
        self._evidence_events: Dict[str, Dict[str, Any]] = {}
        self._rights_requests: Dict[str, Dict[str, Any]] = {}
        self._rights_request_ids_by_command: Dict[Tuple[str, str], str] = {}
        self._rights_executions: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        self._rights_receipts: Dict[str, Dict[str, Any]] = {}
        self._rights_access_revocation_outbox: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._account_purge_receipts: Dict[str, Dict[str, Any]] = {}
        self._rights_lock = RLock()

    @contextmanager
    def auth_user_operation(self, user_id: str):
        if not str(user_id or "").strip():
            raise ValueError("auth user id is required")
        with self._auth_lock:
            yield

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
        event_id = model.eventId
        candidate = {
            "eventId": event_id,
            "operationId": model.operationId,
            "eventType": model.type,
            "schemaVersion": model.schemaVersion,
            "retentionClass": normalized_retention_class,
            "expiresAt": expires_at,
            "legalHold": bool(legal_hold),
            "payloadHash": payload_hash,
            "payload": payload,
            "occurredAt": model.occurredAt.isoformat(),
            "createdAt": self._now(),
        }
        with self._evidence_lock:
            existing = self._evidence_events.get(event_id)
            if existing is not None:
                if any(
                    existing.get(key) != candidate.get(key)
                    for key in (
                        "payloadHash",
                        "retentionClass",
                        "expiresAt",
                        "legalHold",
                    )
                ):
                    raise EvidenceEventConflict(
                        "eventId already exists with different payload or retention metadata"
                    )
                return self._evidence_append_result(existing, outcome="deduplicated")
            self._evidence_events[event_id] = candidate
            return self._evidence_append_result(candidate, outcome="appended")

    def summarize_evidence_events(
        self,
        *,
        operation: str,
        now_iso: Optional[str] = None,
        event_limit: int = 500,
    ) -> Dict[str, Any]:
        normalized_operation = normalize_machine_code(operation)
        now = self._parse_iso_datetime(now_iso or self._now())
        with self._evidence_lock:
            records = [
                deepcopy(record)
                for record in self._evidence_events.values()
                if record["payload"].get("operation") == normalized_operation
                and self._evidence_record_is_visible(record, now)
            ]
        records.sort(key=lambda item: self._parse_iso_datetime(str(item["occurredAt"])))
        decisions = Counter(str(item["payload"].get("decision") or "unknown") for item in records)
        features = Counter(str(item["payload"].get("feature") or "unknown") for item in records)
        bounded_limit = max(1, min(event_limit, 500))
        return {
            "eventCount": len(records),
            "decisionCounts": dict(sorted(decisions.items())),
            "featureCounts": dict(sorted(features.items())),
            "windowStartedAt": records[0]["occurredAt"] if records else None,
            "windowEndedAt": records[-1]["occurredAt"] if records else None,
            "events": [item["payload"] for item in records[-bounded_limit:]],
        }

    def summarize_operation_metrics(
        self,
        *,
        expected_routes: Iterable[str] = (),
        now_iso: Optional[str] = None,
        event_limit: int = 5_000,
    ) -> Dict[str, Any]:
        now = self._parse_iso_datetime(now_iso or self._now())
        bounded_limit = max(1, min(event_limit, 5_000))
        with self._evidence_lock:
            records = [
                deepcopy(record)
                for record in self._evidence_events.values()
                if record.get("eventType") == "operationMetric"
                and self._evidence_record_is_visible(record, now)
            ]
        records.sort(
            key=lambda item: (
                self._parse_iso_datetime(str(item["occurredAt"])),
                str(item.get("eventId") or ""),
            )
        )
        return summarize_operation_metrics(
            [item["payload"] for item in records[-bounded_limit:]],
            expected_routes=expected_routes,
        )

    def expire_evidence_events(self, cutoff_iso: str) -> Dict[str, Any]:
        cutoff = self._parse_iso_datetime(normalize_evidence_timestamp(cutoff_iso))
        expired_ids: List[str] = []
        held_count = 0
        with self._evidence_lock:
            for event_id, record in list(self._evidence_events.items()):
                expires_at = record.get("expiresAt")
                if not expires_at or self._parse_iso_datetime(str(expires_at)) > cutoff:
                    continue
                if record.get("legalHold") is True:
                    held_count += 1
                    continue
                expired_ids.append(event_id)
                self._evidence_events.pop(event_id, None)
        return {
            "schemaVersion": 1,
            "retentionAction": "expire",
            "cutoff": cutoff.astimezone(timezone.utc).isoformat(),
            "expiredCount": len(expired_ids),
            "heldCount": held_count,
            "expiredEventIdHashes": [
                hash_evidence_identifier(event_id) for event_id in sorted(expired_ids)
            ],
        }

    @staticmethod
    def _evidence_record_is_visible(record: Dict[str, Any], now: datetime) -> bool:
        if record.get("legalHold") is True:
            return True
        expires_at = record.get("expiresAt")
        return not expires_at or InMemoryStore._parse_iso_datetime(str(expires_at)) > now

    @staticmethod
    def _evidence_append_result(record: Dict[str, Any], *, outcome: str) -> Dict[str, Any]:
        return {
            "outcome": outcome,
            "eventId": record["eventId"],
            "payloadHash": record["payloadHash"],
            "retentionClass": record["retentionClass"],
            "expiresAt": record.get("expiresAt"),
            "legalHold": bool(record.get("legalHold")),
        }

    def save_auth_session(self, session: Dict[str, Any]) -> Dict[str, Any]:
        item = deepcopy(session)
        with self._auth_lock:
            self._auth_sessions[str(item["sessionId"])] = item
        return deepcopy(item)

    def create_auth_token_family(
        self,
        family: Dict[str, Any],
        session: Dict[str, Any],
        event: Dict[str, Any],
    ) -> Dict[str, Any]:
        family_item = deepcopy(family)
        session_item = deepcopy(session)
        event_item = deepcopy(event)
        family_id = str(family_item["tokenFamilyId"])
        session_id = str(session_item["sessionId"])
        event_id = str(event_item["eventId"])
        with self._auth_lock:
            if family_id in self._auth_token_families:
                raise ValueError("auth token family already exists")
            if session_id in self._auth_sessions:
                raise ValueError("auth session already exists")
            self._auth_token_families[family_id] = family_item
            self._auth_sessions[session_id] = session_item
            self._auth_session_events[event_id] = event_item
        return deepcopy(session_item)

    def get_auth_session_by_access_token_hash(self, token_hash: str) -> Optional[Dict[str, Any]]:
        with self._auth_lock:
            session = next(
                (
                    item
                    for item in self._auth_sessions.values()
                    if item.get("accessTokenHash") == token_hash
                ),
                None,
            )
            if session is None:
                return None
            result = deepcopy(session)
            family_id = str(result.get("tokenFamilyId") or "")
            if family_id:
                family = self._auth_token_families.get(family_id)
                result["familyStatus"] = (
                    str(family.get("status") or "missing")
                    if family is not None
                    else "missing"
                )
            return result

    def rotate_auth_session_refresh(
        self,
        refresh_token_hash: str,
        *,
        successor: Dict[str, Any],
        rotated_at_iso: str,
        rotation_receipt_id: str,
        reuse_receipt_id: str,
    ) -> Dict[str, Any]:
        rotated_at = self._parse_iso_datetime(rotated_at_iso)
        with self._auth_lock:
            session_id, session = self._auth_session_by_refresh_hash(refresh_token_hash)
            if session_id is None or session is None:
                return {"outcome": "invalid"}
            family_id = str(session.get("tokenFamilyId") or "")
            if not family_id or int(session.get("contractVersion") or 1) < 2:
                return {"outcome": "legacyReauthRequired"}
            family = self._auth_token_families.get(family_id)
            if family is None:
                return {"outcome": "invalid"}
            if str(family.get("userId") or "") != str(session.get("userId") or ""):
                return {"outcome": "invalid"}
            if session.get("status") == "rotated":
                self._revoke_family_locked(
                    family_id,
                    revoked_at_iso=rotated_at_iso,
                    reason="refreshTokenReuse",
                    receipt_id=reuse_receipt_id,
                    event_type="refreshReuseDetected",
                    source_session=session,
                )
                return {"outcome": "reuseDetected"}
            if session.get("status") != "active" or family.get("status") != "active":
                return {"outcome": "invalid"}
            if int(family.get("currentSessionVersion") or 0) != int(
                session.get("sessionVersion") or 0
            ):
                return {"outcome": "invalid"}
            account = self._users.get(str(session.get("userId") or ""))
            if account is not None and (
                str(account.get("deletionState") or "active") != "active"
                or str(account.get("accessState") or "active") != "active"
                or int(account.get("authEpoch") or 0) != int(session.get("authEpoch") or 0)
            ):
                return {"outcome": "accountSuspended"}
            try:
                refresh_expires_at = self._parse_iso_datetime(
                    str(session.get("refreshExpiresAt") or "")
                )
            except (TypeError, ValueError):
                return {"outcome": "invalid"}
            if refresh_expires_at <= rotated_at:
                expired = deepcopy(session)
                expired["status"] = "expired"
                expired["expiredAt"] = rotated_at_iso
                self._auth_sessions[session_id] = expired
                return {"outcome": "expired"}

            version = int(session.get("sessionVersion") or 0) + 1
            successor_item = deepcopy(successor)
            successor_item.update(
                {
                    "userId": session["userId"],
                    "tokenFamilyId": family_id,
                    "parentSessionId": session_id,
                    "sessionVersion": version,
                    "authEpoch": int(session.get("authEpoch") or 0),
                }
            )
            successor_id = str(successor_item["sessionId"])
            if successor_id in self._auth_sessions:
                raise ValueError("auth successor session already exists")

            consumed = deepcopy(session)
            consumed.update(
                {
                    "status": "rotated",
                    "rotatedAt": rotated_at_iso,
                    "successorSessionId": successor_id,
                }
            )
            updated_family = deepcopy(family)
            updated_family["currentSessionVersion"] = version
            updated_family["updatedAt"] = rotated_at_iso
            self._auth_sessions[session_id] = consumed
            self._auth_sessions[successor_id] = successor_item
            self._auth_token_families[family_id] = updated_family
            self._auth_session_events[rotation_receipt_id] = {
                "eventId": rotation_receipt_id,
                "tokenFamilyId": family_id,
                "sessionId": successor_id,
                "userId": session["userId"],
                "eventType": "sessionRotated",
                "reason": "refreshConsumed",
                "sessionVersion": version,
                "occurredAt": rotated_at_iso,
                "contractVersion": 1,
            }
            return {"outcome": "rotated", "session": deepcopy(successor_item)}

    def revoke_auth_session_by_access_token_hash(
        self,
        access_token_hash: str,
        revoked_at_iso: str,
        reason: str,
        *,
        receipt_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        with self._auth_lock:
            session_id, session = self._auth_session_by_access_hash(access_token_hash)
            if session_id is None or session is None:
                return None
            revoked = deepcopy(session)
            revoked["status"] = "revoked"
            revoked["revokedAt"] = revoked_at_iso
            revoked["revokeReason"] = reason
            self._auth_sessions[session_id] = revoked
            if receipt_id:
                self._auth_session_events[receipt_id] = self._auth_revoke_event(
                    receipt_id,
                    revoked,
                    event_type="sessionRevoked",
                    reason=reason,
                    occurred_at_iso=revoked_at_iso,
                )
            return {
                **deepcopy(revoked),
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
        with self._auth_lock:
            _, session = self._auth_session_by_access_hash(access_token_hash)
            if session is None:
                return None
            family_id = str(session.get("tokenFamilyId") or "")
            if not family_id:
                return self.revoke_auth_session_by_access_token_hash(
                    access_token_hash,
                    revoked_at_iso,
                    reason,
                    receipt_id=receipt_id,
                )
            return self._revoke_family_locked(
                family_id,
                revoked_at_iso=revoked_at_iso,
                reason=reason,
                receipt_id=receipt_id,
                event_type="familyRevoked",
                source_session=session,
            )

    def revoke_all_auth_token_families(
        self,
        user_id: str,
        revoked_at_iso: str,
        reason: str,
        *,
        receipt_id: str,
    ) -> Dict[str, Any]:
        with self._auth_lock:
            family_ids = sorted(
                family_id
                for family_id, family in self._auth_token_families.items()
                if family.get("userId") == user_id and family.get("status") == "active"
            )
            revoked_session_count = 0
            for index, family_id in enumerate(family_ids):
                result = self._revoke_family_locked(
                    family_id,
                    revoked_at_iso=revoked_at_iso,
                    reason=reason,
                    receipt_id=f"{receipt_id}_{index + 1}",
                    event_type="allDevicesRevoked",
                    source_session=None,
                )
                revoked_session_count += int(result["revokedSessionCount"])

            legacy_session_ids = [
                session_id
                for session_id, session in self._auth_sessions.items()
                if session.get("userId") == user_id
                and not session.get("tokenFamilyId")
                and session.get("status") == "active"
            ]
            for session_id in legacy_session_ids:
                legacy = deepcopy(self._auth_sessions[session_id])
                legacy.update(
                    {
                        "status": "revoked",
                        "revokedAt": revoked_at_iso,
                        "revokeReason": reason,
                    }
                )
                self._auth_sessions[session_id] = legacy
            revoked_session_count += len(legacy_session_ids)
            self._auth_session_events[receipt_id] = {
                "eventId": receipt_id,
                "tokenFamilyId": None,
                "sessionId": None,
                "userId": user_id,
                "eventType": "allDevicesRevoked",
                "reason": reason,
                "sessionVersion": 0,
                "occurredAt": revoked_at_iso,
                "contractVersion": 1,
            }
            return {
                "scope": "allDevices",
                "userId": user_id,
                "revocationReceiptId": receipt_id,
                "revokedFamilyCount": len(family_ids),
                "revokedSessionCount": revoked_session_count,
                "revokedAt": revoked_at_iso,
                "reason": reason,
                "contractVersion": 1,
            }

    def list_auth_session_events(self, token_family_id: str) -> List[Dict[str, Any]]:
        with self._auth_lock:
            events = [
                deepcopy(event)
                for event in self._auth_session_events.values()
                if event.get("tokenFamilyId") == token_family_id
            ]
        events.sort(key=lambda event: (str(event.get("occurredAt") or ""), str(event.get("eventId") or "")))
        return events

    def _auth_session_by_refresh_hash(
        self,
        token_hash: str,
    ) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        return next(
            (
                (key, item)
                for key, item in self._auth_sessions.items()
                if item.get("refreshTokenHash") == token_hash
            ),
            (None, None),
        )

    def _auth_session_by_access_hash(
        self,
        token_hash: str,
    ) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        return next(
            (
                (key, item)
                for key, item in self._auth_sessions.items()
                if item.get("accessTokenHash") == token_hash
            ),
            (None, None),
        )

    def _revoke_family_locked(
        self,
        family_id: str,
        *,
        revoked_at_iso: str,
        reason: str,
        receipt_id: str,
        event_type: str,
        source_session: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        family = self._auth_token_families.get(family_id)
        if family is None:
            return {
                "scope": "family",
                "tokenFamilyId": family_id,
                "revocationReceiptId": receipt_id,
                "revokedFamilyCount": 0,
                "revokedSessionCount": 0,
            }
        updated_family = deepcopy(family)
        updated_family.update(
            {
                "status": "revoked",
                "revokedAt": revoked_at_iso,
                "revokeReason": reason,
                "updatedAt": revoked_at_iso,
            }
        )
        self._auth_token_families[family_id] = updated_family
        revoked_count = 0
        for session_id, session in list(self._auth_sessions.items()):
            if session.get("tokenFamilyId") != family_id or session.get("status") != "active":
                continue
            revoked = deepcopy(session)
            revoked.update(
                {
                    "status": "revoked",
                    "revokedAt": revoked_at_iso,
                    "revokeReason": reason,
                }
            )
            self._auth_sessions[session_id] = revoked
            revoked_count += 1
        event_session = source_session or next(
            (
                session
                for session in self._auth_sessions.values()
                if session.get("tokenFamilyId") == family_id
            ),
            {"userId": family.get("userId"), "sessionVersion": 0, "sessionId": None},
        )
        self._auth_session_events[receipt_id] = self._auth_revoke_event(
            receipt_id,
            event_session,
            event_type=event_type,
            reason=reason,
            occurred_at_iso=revoked_at_iso,
        )
        return {
            "scope": "family",
            "tokenFamilyId": family_id,
            "userId": family.get("userId"),
            "revocationReceiptId": receipt_id,
            "revokedFamilyCount": 1,
            "revokedSessionCount": revoked_count,
            "revokedAt": revoked_at_iso,
            "reason": reason,
            "contractVersion": 1,
        }

    @staticmethod
    def _auth_revoke_event(
        event_id: str,
        session: Dict[str, Any],
        *,
        event_type: str,
        reason: str,
        occurred_at_iso: str,
    ) -> Dict[str, Any]:
        return {
            "eventId": event_id,
            "tokenFamilyId": session.get("tokenFamilyId"),
            "sessionId": session.get("sessionId"),
            "userId": session.get("userId"),
            "eventType": event_type,
            "reason": reason,
            "sessionVersion": int(session.get("sessionVersion") or 0),
            "occurredAt": occurred_at_iso,
            "contractVersion": 1,
        }

    def ensure_identity_hash_key_version(
        self,
        version: str,
        fingerprint: str,
    ) -> Dict[str, Any]:
        with self._identity_lock:
            existing = self._identity_hash_key_versions.get(version)
            if existing is not None:
                return {
                    "outcome": "ready" if existing == fingerprint else "conflict",
                    "version": version,
                }
            if self._identity_hash_key_versions:
                return {"outcome": "conflict", "version": version}
            self._identity_hash_key_versions[version] = fingerprint
            return {"outcome": "ready", "version": version}

    def save_auth_challenge(self, challenge: Dict[str, Any]) -> Dict[str, Any]:
        persisted_fields = (
            "challengeId",
            "identityType",
            "targetHashKeyVersion",
            "targetHash",
            "codeHash",
            "providerMode",
            "purpose",
            "status",
            "attempts",
            "maxAttempts",
            "internalVerificationEnabled",
            "createdAt",
            "expiresAt",
        )
        item = {
            field: deepcopy(challenge[field])
            for field in persisted_fields
        }
        challenge_id = str(item["challengeId"])
        with self._identity_lock:
            if challenge_id in self._auth_challenges:
                raise ValueError("identity challenge already exists")
            self._auth_challenges[challenge_id] = item
        return deepcopy(item)

    def get_auth_challenge(self, challenge_id: str) -> Optional[Dict[str, Any]]:
        with self._identity_lock:
            challenge = self._auth_challenges.get(challenge_id)
            return None if challenge is None else deepcopy(challenge)

    def get_latest_auth_challenge(
        self,
        *,
        identity_type: str,
        target_hash_key_version: str,
        target_hash: str,
        purpose: str,
    ) -> Optional[Dict[str, Any]]:
        with self._identity_lock:
            matches = [
                challenge
                for challenge in self._auth_challenges.values()
                if challenge.get("identityType") == identity_type
                and challenge.get("targetHashKeyVersion") == target_hash_key_version
                and challenge.get("targetHash") == target_hash
                and challenge.get("purpose") == purpose
            ]
            if not matches:
                return None
            latest = max(
                matches,
                key=lambda item: self._parse_iso_datetime(str(item["createdAt"])),
            )
            return deepcopy(latest)

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
        attempted_at = self._parse_iso_datetime(attempted_at_iso)
        with self._identity_lock:
            challenge = self._auth_challenges.get(challenge_id)
            if challenge is None:
                return {"outcome": "missing"}
            if challenge.get("status") != "active":
                return {"outcome": "inactive"}
            if self._parse_iso_datetime(str(challenge["expiresAt"])) <= attempted_at:
                challenge["status"] = "expired"
                challenge["updatedAt"] = attempted_at.isoformat()
                return {"outcome": "expired"}

            attempts = int(challenge.get("attempts") or 0) + 1
            challenge["attempts"] = attempts
            challenge["updatedAt"] = attempted_at.isoformat()
            code_matches = bool(
                challenge.get("internalVerificationEnabled")
                and secrets.compare_digest(
                    str(challenge.get("codeHash") or ""),
                    code_hash,
                )
            )
            if not code_matches:
                if attempts >= int(challenge.get("maxAttempts") or 1):
                    challenge["status"] = "locked"
                return {"outcome": "invalid"}

            target_key = (
                str(challenge["identityType"]),
                str(challenge["targetHashKeyVersion"]),
                str(challenge["targetHash"]),
            )
            existing_binding_id = self._identity_binding_ids_by_target.get(target_key)
            if existing_binding_id is None:
                subject = {
                    "subjectId": subject_id,
                    "status": "active",
                    "createdAt": attempted_at.isoformat(),
                }
                binding = {
                    "bindingId": binding_id,
                    "subjectId": subject_id,
                    "identityType": challenge["identityType"],
                    "targetHashKeyVersion": challenge["targetHashKeyVersion"],
                    "targetHash": challenge["targetHash"],
                    "providerMode": challenge["providerMode"],
                    "status": "active",
                    "verifiedAt": attempted_at.isoformat(),
                    "createdAt": attempted_at.isoformat(),
                }
                self._subjects[subject_id] = subject
                self._identity_bindings[binding_id] = binding
                self._identity_binding_ids_by_target[target_key] = binding_id
            else:
                binding_id = existing_binding_id
                binding = self._identity_bindings[binding_id]
                subject_id = str(binding["subjectId"])
                subject = self._subjects.get(subject_id)
                if (
                    binding.get("status") != "active"
                    or subject is None
                    or subject.get("status") != "active"
                ):
                    return {"outcome": "identityDisabled"}
                binding["providerMode"] = challenge["providerMode"]
                binding["verifiedAt"] = attempted_at.isoformat()

            proof = {
                "proofReceiptId": proof_id,
                "challengeId": challenge_id,
                "bindingId": binding_id,
                "subjectId": subject_id,
                "providerMode": challenge["providerMode"],
                "verifiedAt": attempted_at.isoformat(),
                "contractVersion": 1,
            }
            self._identity_proofs[proof_id] = proof
            challenge["status"] = "consumed"
            challenge["consumedAt"] = attempted_at.isoformat()
            return {
                "outcome": "verified",
                "subjectId": subject_id,
                "bindingId": binding_id,
                "proofReceiptId": proof_id,
                "verifiedAt": attempted_at.isoformat(),
            }

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

    def create_rights_request(self, request: DataRightsRequest) -> Dict[str, Any]:
        if not isinstance(request, DataRightsRequest):
            raise TypeError("rights request contract is required")
        record = {
            "id": request.request_id,
            "subjectHash": request.subject_hash,
            "commandIdHash": request.command_id_hash,
            "payloadHash": request.payload_hash,
            "identityProofHash": request.identity_proof_hash,
            "action": request.action,
            "scopeHash": request.scope_hash,
            "status": request.status,
            "contractVersion": 1,
            "createdAt": request.created_at,
            "updatedAt": request.updated_at,
        }
        key = (request.subject_hash, request.command_id_hash)
        with self._rights_lock:
            existing_id = self._rights_request_ids_by_command.get(key)
            if existing_id is not None:
                existing = self._rights_requests[existing_id]
                if existing["payloadHash"] != request.payload_hash:
                    raise DataRightsCommandConflict(
                        "commandId cannot be reused with a different payload"
                    )
                return {"outcome": "deduplicated", "request": deepcopy(existing)}
            if request.request_id in self._rights_requests:
                existing = self._rights_requests[request.request_id]
                if existing != record:
                    raise DataRightsCommandConflict("request id is already bound")
                return {"outcome": "deduplicated", "request": deepcopy(existing)}
            self._rights_requests[request.request_id] = record
            self._rights_request_ids_by_command[key] = request.request_id
            return {"outcome": "created", "request": deepcopy(record)}

    def get_rights_request(self, request_id: str) -> Optional[Dict[str, Any]]:
        with self._rights_lock:
            request = self._rights_requests.get(str(request_id or ""))
            return None if request is None else deepcopy(request)

    def record_rights_execution(
        self,
        request_id: str,
        *,
        module_id: str,
        resource_type: str,
        execution_id_hash: str,
        outcome: str,
        evidence_id_hash: Optional[str] = None,
        updated_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_outcome = str(outcome or "")
        if normalized_outcome not in EXECUTION_OUTCOMES:
            raise ValueError("outcome is unsupported")
        normalized_module = str(module_id or "").strip()
        normalized_resource = str(resource_type or "").strip()
        if not normalized_module or not normalized_resource:
            raise ValueError("module_id and resource_type are required")
        timestamp = updated_at or self._now()
        key = (str(request_id), normalized_module, normalized_resource)
        with self._rights_lock:
            request = self._rights_requests.get(str(request_id))
            if request is None:
                raise KeyError("rights request not found")
            existing = self._rights_executions.get(key)
            if existing is not None and existing["executionIdHash"] == execution_id_hash:
                if any(
                    existing.get(field) != value
                    for field, value in (
                        ("outcome", normalized_outcome),
                        ("evidenceIdHash", evidence_id_hash),
                    )
                ):
                    raise DataRightsExecutionConflict(
                        "execution id cannot be reused with different execution data"
                    )
                return {
                    "outcome": "deduplicated",
                    "request": deepcopy(request),
                    "execution": deepcopy(existing),
                }
            attempt = int(existing.get("attempt") or 0) + 1 if existing else 1
            execution = {
                "id": f"rx_{request_id}_{normalized_module}_{attempt}",
                "requestId": str(request_id),
                "moduleId": normalized_module,
                "resourceType": normalized_resource,
                "executionIdHash": execution_id_hash,
                "outcome": normalized_outcome,
                "attempt": attempt,
                "evidenceIdHash": evidence_id_hash,
                "updatedAt": timestamp,
            }
            self._rights_executions[key] = execution
            outcomes = [
                item["outcome"]
                for item in self._rights_executions.values()
                if item["requestId"] == str(request_id)
            ]
            request["status"] = aggregate_data_rights_status(outcomes)
            request["updatedAt"] = timestamp
            return {
                "outcome": "updated" if existing else "recorded",
                "request": deepcopy(request),
                "execution": deepcopy(execution),
            }

    def append_resource_deletion_receipt(
        self,
        *,
        receipt_id: str,
        request_id: str,
        execution_id_hash: str,
        module_id: str,
        resource_scope_hash: str,
        outcome: str,
        receipt_hash: str,
        evidence_event_id_hash: Optional[str] = None,
        created_at: Optional[str] = None,
        retention_until: Optional[str] = None,
    ) -> Dict[str, Any]:
        if str(outcome or "") not in {"completed", "partial", "unsupported", "failed"}:
            raise ValueError("receipt outcome is unsupported")
        record = {
            "id": str(receipt_id),
            "requestId": str(request_id),
            "executionIdHash": str(execution_id_hash),
            "moduleId": str(module_id),
            "resourceScopeHash": str(resource_scope_hash),
            "outcome": str(outcome),
            "receiptHash": str(receipt_hash),
            "evidenceEventIdHash": evidence_event_id_hash,
            "createdAt": created_at or self._now(),
            "retentionUntil": retention_until,
        }
        with self._rights_lock:
            if str(request_id) not in self._rights_requests:
                raise KeyError("rights request not found")
            existing = self._rights_receipts.get(str(receipt_id))
            if existing is not None:
                if existing != record:
                    raise ValueError("resource deletion receipt is append-only")
                return {"outcome": "deduplicated", "receipt": deepcopy(existing)}
            self._rights_receipts[str(receipt_id)] = record
            return {"outcome": "appended", "receipt": deepcopy(record)}

    def record_rights_access_revocation_outbox(
        self,
        *,
        event_id: str,
        request_id: str,
        user_id: str,
        auth_epoch: int,
        provider_capability_state: str,
        session_revocation: Dict[str, Any],
        delegated_grant_revocation: Dict[str, Any],
        created_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_request_id = str(request_id or "").strip()
        normalized_user_id = str(user_id or "").strip()
        if not normalized_request_id or not normalized_user_id:
            raise ValueError("request_id and user_id are required")
        if int(auth_epoch) < 0:
            raise ValueError("auth_epoch must be non-negative")
        if provider_capability_state != "revoked":
            raise ValueError("provider capability state must be revoked")
        record = {
            "id": str(event_id),
            "requestId": normalized_request_id,
            "userId": normalized_user_id,
            "eventType": "RightsAccessRevoked",
            "authEpoch": int(auth_epoch),
            "providerCapabilityState": provider_capability_state,
            "status": "pending",
            "sessionRevocation": {
                "scope": str(session_revocation.get("scope") or ""),
                "revocationReceiptId": str(session_revocation.get("revocationReceiptId") or ""),
                "revokedFamilyCount": int(session_revocation.get("revokedFamilyCount") or 0),
                "revokedSessionCount": int(session_revocation.get("revokedSessionCount") or 0),
            },
            "delegatedGrantRevocation": {
                "revokedGrantCount": int(delegated_grant_revocation.get("revokedGrantCount") or 0),
            },
            "createdAt": created_at or self._now(),
        }
        key = (normalized_request_id, "RightsAccessRevoked")
        with self._rights_lock:
            if normalized_request_id not in self._rights_requests:
                raise KeyError("rights request not found")
            existing = self._rights_access_revocation_outbox.get(key)
            if existing is not None:
                if any(
                    existing.get(field) != record.get(field)
                    for field in (
                        "id",
                        "userId",
                        "authEpoch",
                        "providerCapabilityState",
                    )
                ):
                    raise ValueError("rights access revocation outbox is immutable")
                return {"outcome": "deduplicated", "event": deepcopy(existing)}
            self._rights_access_revocation_outbox[key] = record
            return {"outcome": "recorded", "event": deepcopy(record)}

    def list_rights_access_revocation_outbox(self, request_id: str) -> List[Dict[str, Any]]:
        normalized_request_id = str(request_id or "").strip()
        with self._rights_lock:
            records = [
                deepcopy(record)
                for (candidate_request_id, _), record in self._rights_access_revocation_outbox.items()
                if candidate_request_id == normalized_request_id
            ]
        records.sort(key=lambda record: (record["createdAt"], record["id"]))
        return records

    def summarize_rights_request(self, request_id: str) -> Optional[Dict[str, Any]]:
        with self._rights_lock:
            request = self._rights_requests.get(str(request_id or ""))
            if request is None:
                return None
            executions = [
                deepcopy(item)
                for item in self._rights_executions.values()
                if item["requestId"] == str(request_id)
            ]
            executions.sort(key=lambda item: (item["moduleId"], item["resourceType"]))
            receipts = [
                deepcopy(item)
                for item in self._rights_receipts.values()
                if item["requestId"] == str(request_id)
            ]
            receipts.sort(key=lambda item: (item["createdAt"], item["id"]))
            return {
                "request": deepcopy(request),
                "executions": executions,
                "receipts": receipts,
            }

    def upsert_user(self, phone: str, nickname: str) -> Dict[str, Any]:
        user_id = stable_user_id(phone)
        with self._auth_lock:
            existing = self._users.get(user_id, {})
            guard_account_upsert(existing)
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
            user["accessState"] = "active"
            user["authEpoch"] = int(existing.get("authEpoch") or 0)
            user["providerCapabilityState"] = str(
                existing.get("providerCapabilityState") or "enabled"
            )
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
        deletion_request_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        user = self._users.get(user_id)
        if user is None:
            return None
        if user.get("deletionState") == "purged":
            return None
        if self._normalized_phone(user.get("phone", "")) != self._normalized_phone(phone):
            return None

        if (
            user.get("deletionState") == "softDeleted"
            and user.get("accessState") == "suspended_restorable"
        ):
            return deepcopy(user)

        requested_at = self._parse_iso_datetime(requested_at_iso) if requested_at_iso else datetime.now(timezone.utc)
        deleted_at = requested_at.isoformat()
        purge_after = (requested_at + self._account_delete_retention_delta()).isoformat()
        item = deepcopy(user)
        item["deletionState"] = "softDeleted"
        item["accessState"] = "suspended_restorable"
        item["authEpoch"] = int(item.get("authEpoch") or 0) + 1
        item["providerCapabilityState"] = "revoked"
        item["deletedAt"] = deleted_at
        item["purgeAfter"] = purge_after
        item["restoreDeadline"] = purge_after
        item["retentionDays"] = 30
        item["dataExportSupported"] = True
        item["dataExportState"] = "availableBeforeDeletionOnly"
        item["restoreLimit"] = 1
        item["restoreCount"] = int(item.get("restoreCount") or 0)
        item.setdefault("retentionHolds", [])
        if deletion_request_id:
            item["deletionRequestId"] = str(deletion_request_id)
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
        with self._auth_lock:
            user = self._users.get(user_id)
            if user is None:
                return None
            if self._normalized_phone(user.get("phone", "")) != self._normalized_phone(phone):
                return None

            restored_at = restored_at_iso or self._now()
            if account_restore_block_reason(user, restored_at) is not None:
                return None
            item = deepcopy(user)
            item["nickname"] = nickname or item.get("nickname") or "寻梦环游用户"
            item["deletionState"] = "active"
            item["accessState"] = "active"
            item["restoreCount"] = int(item.get("restoreCount") or 0) + 1
            item["restoredAt"] = restored_at
            item["updatedAt"] = restored_at
            for key in (
                "deletedAt",
                "purgeAfter",
                "restoreDeadline",
                "retentionDays",
                "dataExportSupported",
                "dataExportState",
                "restoreLimit",
                "deletionRequestId",
            ):
                item.pop(key, None)
            self._users[user_id] = item
            return deepcopy(item)

    def purge_expired_deleted_users(self, cutoff_iso: str) -> List[Dict[str, Any]]:
        with self._auth_lock:
            return self._purge_expired_deleted_users_locked(cutoff_iso)

    def _terminal_account_cleanup_counts_locked(self, user_id: str) -> Dict[str, int]:
        relationship_ids = {
            relationship_id
            for relationship_id, relationship in self._family_relationships.items()
            if user_id in {
                relationship.get("ownerSubjectId"),
                relationship.get("memberSubjectId"),
            }
        }
        grant_ids = {
            grant_id
            for grant_id, grant in self._access_grants.items()
            if grant.get("relationshipId") in relationship_ids
            or user_id in {
                grant.get("grantorSubjectId"),
                grant.get("granteeSubjectId"),
            }
        }
        return {
            "profile": int(user_id in self._profiles),
            "passwordCredential": int(user_id in self._password_credentials),
            "knowledgeSnapshot": int(user_id in self._kb_snapshots),
            "knowledgeChange": len(self._kb_changes.get(user_id, [])),
            "knowledgeReceipt": len(self._kb_operation_receipts.get(user_id, {})),
            "knowledgeFeedState": int(user_id in self._kb_change_feed_minimum_since_revisions),
            "memory": len(self._memories.get(user_id, [])),
            "archive": len(self._archive_items.get(user_id, [])),
            "mailbox": len(self._mailbox_letters.get(user_id, [])),
            "familyMember": len(self._family_members.get(user_id, [])),
            "familyRelationship": len(relationship_ids),
            "accessGrant": len(grant_ids),
            "grantEvent": sum(len(self._grant_events.get(grant_id, [])) for grant_id in grant_ids),
            "care": len(self._care_snapshots.get(user_id, [])),
            "echo": len(self._echo_delayed_replies.get(user_id, [])),
            "pushToken": len(self._push_device_tokens.get(user_id, [])),
            "voiceProfile": len(self._voice_profiles.get(user_id, [])),
            "voiceCloneSlotRetired": sum(
                1
                for slot in self._voice_clone_slots.values()
                if slot.get("userId") == user_id
                and slot.get("status") not in {"retired", "deleted"}
            ),
            "digitalHumanSession": sum(
                1
                for lease in self._digital_human_sessions.values()
                if lease.get("userId") == user_id
            ),
            "authSession": sum(
                1
                for session in self._auth_sessions.values()
                if session.get("userId") == user_id
            ),
            "authTokenFamily": sum(
                1
                for family in self._auth_token_families.values()
                if family.get("userId") == user_id
            ),
            "authSessionEvent": sum(
                1
                for event in self._auth_session_events.values()
                if event.get("userId") == user_id
            ),
        }

    def _purge_expired_deleted_users_locked(self, cutoff_iso: str) -> List[Dict[str, Any]]:
        cutoff = self._parse_iso_datetime(cutoff_iso)
        purged: List[Dict[str, Any]] = []
        for user_id, user in list(self._users.items()):
            if account_purge_block_reason(user, cutoff) is not None:
                continue
            cleanup_counts = self._terminal_account_cleanup_counts_locked(user_id)
            receipt = build_account_purge_receipt(
                user_id=user_id,
                account=user,
                purged_at=cutoff,
            )
            existing_receipt = self._account_purge_receipts.get(receipt["subjectHash"])
            if existing_receipt is not None and existing_receipt != receipt:
                raise ValueError("account purge receipt is append-only")
            self._account_purge_receipts.setdefault(
                receipt["subjectHash"],
                deepcopy(receipt),
            )
            tombstone = {
                "id": user_id,
                "phone": "",
                "nickname": "",
                "deletionState": "purged",
                "accessState": "purged",
                "authEpoch": int(user.get("authEpoch") or 0),
                "providerCapabilityState": "revoked",
                "purgedAt": cutoff.isoformat(),
                "restoreCount": int(user.get("restoreCount") or 0),
                "terminalPurgeReceiptId": receipt["id"],
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
            purged_relationship_ids = {
                relationship_id
                for relationship_id, relationship in self._family_relationships.items()
                if user_id in {
                    relationship.get("ownerSubjectId"),
                    relationship.get("memberSubjectId"),
                }
            }
            purged_grant_ids = {
                grant_id
                for grant_id, grant in self._access_grants.items()
                if grant.get("relationshipId") in purged_relationship_ids
                or user_id in {
                    grant.get("grantorSubjectId"),
                    grant.get("granteeSubjectId"),
                }
            }
            for grant_id in purged_grant_ids:
                self._access_grants.pop(grant_id, None)
                self._grant_events.pop(grant_id, None)
            for relationship_id in purged_relationship_ids:
                self._family_relationships.pop(relationship_id, None)
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
            purged_family_ids = {
                family_id
                for family_id, family in self._auth_token_families.items()
                if family.get("userId") == user_id
            }
            self._auth_token_families = {
                family_id: family
                for family_id, family in self._auth_token_families.items()
                if family_id not in purged_family_ids
            }
            self._auth_session_events = {
                event_id: event
                for event_id, event in self._auth_session_events.items()
                if event.get("userId") != user_id
            }
            for slot in self._voice_clone_slots.values():
                if slot.get("userId") != user_id:
                    continue
                slot["status"] = "retired"
                slot["updatedAt"] = cutoff.isoformat()
            self._voice_profiles.pop(user_id, None)
            record_terminal_cleanup_plan(
                self,
                request_id=str(user.get("deletionRequestId") or ""),
                terminal_purge_receipt_id=str(receipt["id"]),
                updated_at=cutoff.isoformat(),
                resource_counts=cleanup_counts,
                retention_until=None,
            )
            purged.append(deepcopy(tombstone))
        return purged

    def get_account_purge_receipt(self, user_id: str) -> Optional[Dict[str, Any]]:
        receipt = self._account_purge_receipts.get(account_purge_subject_hash(user_id))
        return None if receipt is None else deepcopy(receipt)

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
        item.setdefault("id", self._new_resource_id("memory"))
        item["userId"] = user_id
        item["createdAt"] = self._now()
        self._assert_resource_owner(self._memories, user_id, str(item["id"]))
        self._memories.setdefault(user_id, []).insert(0, item)
        return deepcopy(item)

    def list_memories(self, user_id: str) -> List[Dict[str, Any]]:
        return deepcopy(self._memories.get(user_id, []))

    def resolve_resource_authority(
        self,
        resource_type: str,
        resource_id: str,
    ) -> Optional[Dict[str, Any]]:
        if resource_type == "digitalHumanSession":
            item = self._digital_human_sessions.get(resource_id)
            if item is None:
                return None
            user_id = str(item.get("userId") or "")
            return {
                "resourceType": resource_type,
                "resourceId": resource_id,
                "vaultId": user_id,
                "ownerSubjectId": user_id,
                "rowVersion": int(item.get("resourceVersion") or 1),
                "authorityState": str(item.get("authorityState") or "active"),
            }
        collections = {
            "archiveItem": self._archive_items,
            "familyMember": self._family_members,
            "mailboxLetter": self._mailbox_letters,
            "voiceProfile": self._voice_profiles,
        }
        resources = collections.get(resource_type)
        if resources is None:
            raise ValueError(f"unsupported resource type: {resource_type}")
        for user_id, items in resources.items():
            for item in items:
                item_id = str(item.get("id") or item.get("sessionId") or item.get("voiceProfileId") or "")
                if item_id != resource_id:
                    continue
                return {
                    "resourceType": resource_type,
                    "resourceId": resource_id,
                    "vaultId": user_id,
                    "ownerSubjectId": user_id,
                    "rowVersion": int(item.get("resourceVersion") or 1),
                    "authorityState": str(item.get("authorityState") or "active"),
                }
        return None

    def add_archive_item(self, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        item = deepcopy(payload)
        item.setdefault("id", self._new_resource_id("archive"))
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
            return deepcopy(
                [
                    item
                    for item in self._archive_items.get(user_id, [])
                    if str(item.get("authorityState") or "active") == "active"
                ]
            )

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
        expected_version: Optional[int] = None,
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
            current_resource_version = int(item.get("resourceVersion") or 1)
            if expected_version is not None and expected_version != current_resource_version:
                from app.services.archive_store import ResourceVersionConflict
                raise ResourceVersionConflict(
                    expected_version=expected_version,
                    current_version=current_resource_version,
                )
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
        item.setdefault("id", self._new_resource_id("mailbox"))
        item["userId"] = user_id
        item["updatedAt"] = self._now()
        item.setdefault("createdAt", item["updatedAt"])

        self._assert_resource_owner(self._mailbox_letters, user_id, str(item["id"]))
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
        item.setdefault("id", item.get("delayedReplyId") or self._new_resource_id("echo_delayed"))
        item["userId"] = user_id
        item["createdAt"] = self._now()

        self._assert_resource_owner(self._echo_delayed_replies, user_id, str(item["id"]))
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
        updated["ownerUserId"] = user_id
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
        metadata["ownerUserId"] = user_id
        updated["metadata"] = metadata
        updated["updatedAt"] = delivered_at_iso
        return updated

    def save_push_device_token(self, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        item = deepcopy(payload)
        item.setdefault("id", item.get("deviceTokenId") or self._new_resource_id("push_token"))
        item.setdefault("deviceTokenId", item["id"])
        item["userId"] = user_id
        item["updatedAt"] = self._now()

        self._assert_resource_owner(self._push_device_tokens, user_id, str(item["id"]))
        tokens = self._push_device_tokens.setdefault(user_id, [])
        tokens[:] = [token for token in tokens if token.get("id") != item["id"]]
        tokens.insert(0, item)
        return deepcopy(item)

    def list_push_device_tokens(self, user_id: str) -> List[Dict[str, Any]]:
        return deepcopy(self._push_device_tokens.get(user_id, []))

    def save_voice_profile(self, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        item = deepcopy(payload)
        item["userId"] = user_id
        item.setdefault("id", item.get("voiceProfileId") or self._new_resource_id("voice_profile"))
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
        item.setdefault("id", self._new_resource_id("family"))
        item.setdefault("invitationCode", "")
        item.setdefault("invitationURL", "")
        item["userId"] = user_id
        item["ownerUserId"] = user_id
        item["createdAt"] = self._now()
        self._assert_resource_owner(self._family_members, user_id, str(item["id"]))
        self._family_members.setdefault(user_id, []).append(item)
        return deepcopy(item)

    def list_family_members(self, user_id: str) -> List[Dict[str, Any]]:
        return deepcopy(self._family_members.get(user_id, []))

    @contextmanager
    def delegated_access_relationship_scope(
        self,
        *,
        owner_subject_id: str,
        relationship_id: str,
    ):
        _ = owner_subject_id, relationship_id
        with self._delegated_access_lock:
            yield

    def upsert_family_relationship(self, relationship: Dict[str, Any]) -> Dict[str, Any]:
        candidate = deepcopy(relationship)
        relationship_id = str(candidate.get("id") or "").strip()
        owner_subject_id = str(candidate.get("ownerSubjectId") or "").strip()
        family_member_id = str(candidate.get("familyMemberId") or "").strip()
        member_subject_id = str(candidate.get("memberSubjectId") or "").strip()
        if not all((relationship_id, owner_subject_id, family_member_id, member_subject_id)):
            raise ValueError("family relationship authority is incomplete")
        with self._delegated_access_lock:
            existing = self._family_relationships.get(relationship_id)
            if existing is None:
                now = self._now()
                candidate.setdefault("vaultId", owner_subject_id)
                candidate["relationshipEpoch"] = 1
                candidate["grantEpoch"] = 0
                candidate["createdAt"] = now
                candidate["updatedAt"] = now
                self._family_relationships[relationship_id] = candidate
                return deepcopy(candidate)
            if (
                existing.get("ownerSubjectId") != owner_subject_id
                or existing.get("familyMemberId") != family_member_id
            ):
                raise ValueError("family relationship authority conflict")
            current_status = str(existing.get("status") or "pending")
            requested_status = str(candidate.get("status") or current_status)
            if current_status in {"paused", "revoked"} and requested_status == "accepted":
                requested_status = current_status
            elif current_status == "accepted" and requested_status == "pending":
                requested_status = current_status
            current_member_subject = str(existing.get("memberSubjectId") or "")
            if requested_status == "accepted" and (
                current_status == "pending"
                or current_member_subject.startswith("legacy-unverified:")
            ):
                current_member_subject = member_subject_id
            changed = (
                requested_status != current_status
                or current_member_subject != existing.get("memberSubjectId")
            )
            if changed:
                existing["status"] = requested_status
                existing["memberSubjectId"] = current_member_subject
                existing["relationshipEpoch"] = int(existing.get("relationshipEpoch") or 1) + 1
                existing["updatedAt"] = self._now()
            return deepcopy(existing)

    def get_family_relationship(
        self,
        owner_subject_id: str,
        relationship_id: str,
    ) -> Optional[Dict[str, Any]]:
        with self._delegated_access_lock:
            relationship = self._family_relationships.get(relationship_id)
            if relationship is None or relationship.get("ownerSubjectId") != owner_subject_id:
                return None
            return deepcopy(relationship)

    def get_family_relationship_by_member(
        self,
        owner_subject_id: str,
        family_member_id: str,
    ) -> Optional[Dict[str, Any]]:
        with self._delegated_access_lock:
            for relationship in self._family_relationships.values():
                if (
                    relationship.get("ownerSubjectId") == owner_subject_id
                    and relationship.get("familyMemberId") == family_member_id
                ):
                    return deepcopy(relationship)
        return None

    def list_family_relationships(self, owner_subject_id: str) -> List[Dict[str, Any]]:
        with self._delegated_access_lock:
            values = [
                deepcopy(item)
                for item in self._family_relationships.values()
                if item.get("ownerSubjectId") == owner_subject_id
            ]
        return sorted(values, key=lambda item: (str(item.get("createdAt") or ""), str(item.get("id") or "")))

    def update_family_relationship_status(
        self,
        owner_subject_id: str,
        relationship_id: str,
        *,
        status: str,
        expected_epoch: int,
    ) -> Optional[Dict[str, Any]]:
        with self._delegated_access_lock:
            relationship = self._family_relationships.get(relationship_id)
            if (
                relationship is None
                or relationship.get("ownerSubjectId") != owner_subject_id
                or int(relationship.get("relationshipEpoch") or 1) != expected_epoch
            ):
                return None
            relationship["status"] = status
            relationship["relationshipEpoch"] = expected_epoch + 1
            relationship["updatedAt"] = self._now()
            return deepcopy(relationship)

    def create_access_grant(self, grant: Dict[str, Any]) -> Dict[str, Any]:
        candidate = deepcopy(grant)
        grant_id = str(candidate.get("id") or "").strip()
        relationship_id = str(candidate.get("relationshipId") or "").strip()
        with self._delegated_access_lock:
            if not grant_id or grant_id in self._access_grants:
                raise ValueError("access grant id conflict")
            relationship = self._family_relationships.get(relationship_id)
            if relationship is None:
                raise ValueError("family relationship not found")
            self._access_grants[grant_id] = candidate
            self._append_grant_event_locked(candidate, "granted", reason="ownerGranted")
            self._bump_relationship_grant_epoch_locked(relationship)
            return deepcopy(candidate)

    def get_access_grant(self, grant_id: str) -> Optional[Dict[str, Any]]:
        with self._delegated_access_lock:
            grant = self._access_grants.get(grant_id)
            return None if grant is None else deepcopy(grant)

    def list_access_grants(
        self,
        *,
        owner_subject_id: str,
        relationship_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self._delegated_access_lock:
            values = [
                deepcopy(item)
                for item in self._access_grants.values()
                if item.get("grantorSubjectId") == owner_subject_id
                and (relationship_id is None or item.get("relationshipId") == relationship_id)
            ]
        return sorted(values, key=lambda item: (str(item.get("createdAt") or ""), str(item.get("id") or "")))

    def revoke_access_grant(
        self,
        owner_subject_id: str,
        grant_id: str,
        *,
        expected_version: int,
        revoked_at_iso: str,
        reason: str,
    ) -> Optional[Dict[str, Any]]:
        with self._delegated_access_lock:
            grant = self._access_grants.get(grant_id)
            if grant is None or grant.get("grantorSubjectId") != owner_subject_id:
                return None
            if int(grant.get("rowVersion") or 1) != expected_version:
                return None
            if grant.get("status") != "revoked":
                grant["status"] = "revoked"
                grant["revokedAt"] = revoked_at_iso
                grant["updatedAt"] = revoked_at_iso
                grant["rowVersion"] = expected_version + 1
                self._append_grant_event_locked(grant, "revoked", reason=reason)
                relationship = self._family_relationships.get(str(grant.get("relationshipId") or ""))
                if relationship is not None:
                    self._bump_relationship_grant_epoch_locked(relationship)
            return deepcopy(grant)

    def revoke_all_access_grants_for_subject(
        self,
        subject_id: str,
        *,
        revoked_at_iso: str,
        reason: str,
    ) -> int:
        revoked_count = 0
        with self._delegated_access_lock:
            for grant in self._access_grants.values():
                if grant.get("status") != "active":
                    continue
                if subject_id not in {
                    grant.get("grantorSubjectId"),
                    grant.get("granteeSubjectId"),
                }:
                    continue
                grant["status"] = "revoked"
                grant["revokedAt"] = revoked_at_iso
                grant["updatedAt"] = revoked_at_iso
                grant["rowVersion"] = int(grant.get("rowVersion") or 1) + 1
                self._append_grant_event_locked(grant, "revoked", reason=reason)
                relationship = self._family_relationships.get(str(grant.get("relationshipId") or ""))
                if relationship is not None:
                    self._bump_relationship_grant_epoch_locked(relationship)
                revoked_count += 1
        return revoked_count

    def revoke_all_access_grants_for_relationship(
        self,
        owner_subject_id: str,
        relationship_id: str,
        *,
        revoked_at_iso: str,
        reason: str,
    ) -> int:
        revoked_count = 0
        with self._delegated_access_lock:
            for grant in self._access_grants.values():
                if grant.get("status") != "active":
                    continue
                if grant.get("grantorSubjectId") != owner_subject_id:
                    continue
                if grant.get("relationshipId") != relationship_id:
                    continue
                grant["status"] = "revoked"
                grant["revokedAt"] = revoked_at_iso
                grant["updatedAt"] = revoked_at_iso
                grant["rowVersion"] = int(grant.get("rowVersion") or 1) + 1
                self._append_grant_event_locked(
                    grant,
                    "revoked",
                    reason=reason,
                    actor_subject_id=owner_subject_id,
                )
                revoked_count += 1
            relationship = self._family_relationships.get(relationship_id)
            if revoked_count and relationship is not None:
                self._bump_relationship_grant_epoch_locked(relationship)
        return revoked_count

    def record_access_grant_receipt(
        self,
        grant: Dict[str, Any],
        *,
        actor_subject_id: str,
        operation: str,
    ) -> Dict[str, Any]:
        with self._delegated_access_lock:
            stored = self._access_grants.get(str(grant.get("id") or ""))
            if stored is None or stored.get("status") != "active":
                raise ValueError("active access grant is required for receipt")
            event = self._append_grant_event_locked(
                stored,
                "accessed",
                reason=f"authorized:{operation}",
                actor_subject_id=actor_subject_id,
            )
            return deepcopy(event)

    def list_grant_events(self, grant_id: str) -> List[Dict[str, Any]]:
        with self._delegated_access_lock:
            return deepcopy(self._grant_events.get(grant_id, []))

    def list_access_receipts(
        self,
        *,
        owner_subject_id: str,
        grant_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self._delegated_access_lock:
            receipts: List[Dict[str, Any]] = []
            for stored_grant_id, events in self._grant_events.items():
                if grant_id is not None and stored_grant_id != grant_id:
                    continue
                grant = self._access_grants.get(stored_grant_id)
                if grant is None or grant.get("grantorSubjectId") != owner_subject_id:
                    continue
                for event in events:
                    if event.get("eventType") != "accessed":
                        continue
                    receipts.append(self._access_receipt_payload(grant, event))
            return sorted(
                deepcopy(receipts),
                key=lambda item: (str(item.get("occurredAt") or ""), str(item["id"])),
            )

    def _append_grant_event_locked(
        self,
        grant: Dict[str, Any],
        event_type: str,
        *,
        reason: str,
        actor_subject_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        event = {
            "id": f"grant_event_{uuid.uuid4().hex}",
            "grantId": grant["id"],
            "relationshipId": grant["relationshipId"],
            "eventType": event_type,
            "actorSubjectId": actor_subject_id or grant["grantorSubjectId"],
            "grantVersion": int(grant.get("rowVersion") or 1),
            "reason": reason,
            "occurredAt": self._now(),
        }
        self._grant_events.setdefault(str(grant["id"]), []).append(event)
        return event

    @staticmethod
    def _access_receipt_payload(
        grant: Dict[str, Any],
        event: Dict[str, Any],
    ) -> Dict[str, Any]:
        reason = str(event.get("reason") or "")
        operation = reason.split(":", 1)[1] if reason.startswith("authorized:") else ""
        return {
            "id": event["id"],
            "decision": "allow",
            "grantId": grant["id"],
            "relationshipId": grant["relationshipId"],
            "ownerSubjectId": grant["grantorSubjectId"],
            "granteeSubjectId": event["actorSubjectId"],
            "purpose": grant["purpose"],
            "operation": operation,
            "resourceType": grant["resourceType"],
            "resourceId": grant.get("resourceId"),
            "grantVersion": event["grantVersion"],
            "occurredAt": event["occurredAt"],
        }

    def _bump_relationship_grant_epoch_locked(self, relationship: Dict[str, Any]) -> None:
        relationship["grantEpoch"] = int(relationship.get("grantEpoch") or 0) + 1
        relationship["updatedAt"] = self._now()

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
            "id": self._new_resource_id("care"),
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
    def _assert_resource_owner(
        resources: Dict[str, List[Dict[str, Any]]],
        user_id: str,
        resource_id: str,
    ) -> None:
        for existing_user_id, items in resources.items():
            if existing_user_id == user_id:
                continue
            if any(str(item.get("id") or "") == resource_id for item in items):
                raise ResourceOwnershipConflict("resource id belongs to another owner")

    @staticmethod
    def _new_resource_id(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex}"

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
