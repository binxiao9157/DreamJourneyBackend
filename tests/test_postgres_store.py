import unittest
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier

from psycopg.types.json import Jsonb

from app.services.auth_sessions import AuthSessionError, AuthSessionService
from app.observability.events import EvidenceEventConflict
from app.services.archive_store import (
    ArchiveItemDeletionForbidden,
    ArchiveItemNotFound,
    ArchiveItemOwnershipConflict,
    ResourceOwnershipConflict,
)
from app.services.in_memory_store import InMemoryStore
from app.services.knowledge_store import (
    KnowledgeOperationPayloadConflict,
    KnowledgeRevisionConflict,
)
from app.services.postgres_store import PostgresStore
from app.services.store_factory import init_store


def unwrap_jsonb(value):
    return value.obj if isinstance(value, Jsonb) else value


def make_digital_human_lease(
    session_id,
    *,
    user_id="u1",
    device_id="device-1",
    persona_id="persona-1",
    resource_key="resource-1",
    lifecycle_mode="sunlight",
    created_at="2026-07-10T00:00:00+00:00",
    expires_at="2026-07-10T00:03:00+00:00",
):
    return {
        "sessionId": session_id,
        "resourceKey": resource_key,
        "userId": user_id,
        "deviceId": device_id,
        "personaId": persona_id,
        "scene": "echo",
        "lifecycleMode": lifecycle_mode,
        "providerMode": "cloudRender",
        "status": "active",
        "createdAt": created_at,
        "heartbeatAt": created_at,
        "expiresAt": expires_at,
    }


def make_evidence_event(event_id, *, reason="policyAllowed"):
    return {
        "eventId": event_id,
        "schemaVersion": 1,
        "type": "operation",
        "operationId": "op_release_policy",
        "correlationId": None,
        "principalHash": None,
        "resourceType": "releasePolicy",
        "resourceIdHash": None,
        "state": "succeeded",
        "reason": reason,
        "attempt": 1,
        "occurredAt": "2026-07-16T12:00:00+00:00",
        "env": "test",
        "build": "42",
        "redactionVersion": 1,
        "operation": "releasePolicyDecision",
        "route": "GET /config/runtime",
        "latencyMs": 0,
        "policyVersion": "release-policy-v1",
        "clientBuild": 42,
        "feature": "runtimeConfig",
        "decision": "typedRuntimeContract",
    }


ARCHIVE_DELETE_GRAPH = {
    "facts": [
        {
            "id": "fact-1",
            "statement": "source",
            "privacyMetadata": {"scope": "generationAllowed"},
        }
    ]
}


def archive_delete_mutation():
    return {
        "upserts": {},
        "tombstones": [
            {
                "entityType": "facts",
                "entityId": "fact-1",
                "deletedAt": "2026-07-11T00:00:00Z",
            }
        ],
    }


def seed_archive_delete_state(connection, *, item=None):
    graph = deepcopy(ARCHIVE_DELETE_GRAPH)
    archive_item = deepcopy(
        item or {"id": "archive-1", "userId": "u1", "kind": "photo"}
    )
    connection.kb_snapshots["u1"] = graph
    connection.kb_snapshot_revisions["u1"] = 1
    connection.kb_changes["u1"] = [
        {
            "revision": 1,
            "operation_id": "seed",
            "graph": deepcopy(graph),
            "mutation": None,
            "created_at": "2026-07-10T00:00:00+00:00",
        }
    ]
    connection.archive_items["u1"] = [archive_item]
    return archive_item


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.result = None
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        self.connection.executed.append((normalized, params))
        params = params or ()
        self.rowcount = 0

        def matching_evidence(operation, now_iso):
            now = datetime.fromisoformat(str(now_iso).replace("Z", "+00:00"))
            return [
                item
                for item in self.connection.evidence_events.values()
                if item["event_type"] == "operation"
                and item["payload"].get("operation") == operation
                and (
                    item["legal_hold"]
                    or item["expires_at"] is None
                    or datetime.fromisoformat(
                        str(item["expires_at"]).replace("Z", "+00:00")
                    )
                    > now
                )
            ]

        def has_resource_conflict(collections, user_id, item_id):
            return any(
                existing_user_id != user_id
                and any(str(item.get("id") or "") == item_id for item in items)
                for existing_user_id, items in collections.items()
            )

        if normalized.startswith("INSERT INTO evidence_events"):
            (
                event_id,
                operation_id,
                event_type,
                schema_version,
                retention_class,
                expires_at,
                legal_hold,
                payload_hash,
                payload,
                occurred_at,
            ) = params
            payload = unwrap_jsonb(payload)
            if event_id in self.connection.evidence_events:
                self.result = None
            else:
                item = {
                    "event_id": event_id,
                    "operation_id": operation_id,
                    "event_type": event_type,
                    "schema_version": schema_version,
                    "retention_class": retention_class,
                    "expires_at": expires_at,
                    "legal_hold": legal_hold,
                    "payload_hash": payload_hash,
                    "payload": dict(payload),
                    "occurred_at": occurred_at,
                }
                self.connection.evidence_events[event_id] = item
                self.result = {
                    key: item[key]
                    for key in (
                        "event_id",
                        "payload_hash",
                        "retention_class",
                        "expires_at",
                        "legal_hold",
                    )
                }
        elif normalized.startswith(
            "SELECT event_id, payload_hash, retention_class, expires_at, legal_hold FROM evidence_events"
        ):
            item = self.connection.evidence_events.get(params[0])
            self.result = None if item is None else dict(item)
        elif normalized.startswith("SELECT COUNT(*) AS event_count"):
            matches = matching_evidence(params[0], params[1])
            occurred = [item["occurred_at"] for item in matches]
            self.result = {
                "event_count": len(matches),
                "window_started_at": min(occurred) if occurred else None,
                "window_ended_at": max(occurred) if occurred else None,
            }
        elif normalized.startswith("SELECT payload->>'decision' AS value"):
            counts = {}
            for item in matching_evidence(params[0], params[1]):
                value = item["payload"].get("decision")
                counts[value] = counts.get(value, 0) + 1
            self.result = [
                {"value": value, "count": count}
                for value, count in counts.items()
            ]
        elif normalized.startswith("SELECT payload->>'feature' AS value"):
            counts = {}
            for item in matching_evidence(params[0], params[1]):
                value = item["payload"].get("feature")
                counts[value] = counts.get(value, 0) + 1
            self.result = [
                {"value": value, "count": count}
                for value, count in counts.items()
            ]
        elif normalized.startswith("SELECT payload FROM evidence_events"):
            matches = matching_evidence(params[0], params[1])
            matches.sort(key=lambda item: item["occurred_at"], reverse=True)
            self.result = [
                {"payload": dict(item["payload"])}
                for item in matches[: int(params[2])]
            ]
        elif normalized.startswith("SELECT pg_advisory_xact_lock"):
            if self.connection.advisory_barrier is not None:
                self.connection.advisory_barrier.wait(timeout=2)
            self.result = {"locked": True}
        elif (
            normalized.startswith("SELECT id, payload FROM digital_human_sessions")
            and "expires_at <= %s" in normalized
        ):
            now = datetime.fromisoformat(str(params[0]).replace("Z", "+00:00"))
            self.result = [
                {"id": item.get("sessionId"), "payload": dict(item)}
                for item in self.connection.digital_human_sessions.values()
                if item.get("status") == "active"
                and datetime.fromisoformat(
                    str(item.get("expiresAt") or "").replace("Z", "+00:00")
                )
                <= now
            ]
        elif normalized.startswith("SELECT id, payload FROM digital_human_sessions"):
            resource_key, user_id, device_id = params
            self.result = [
                {"id": item.get("sessionId"), "payload": dict(item)}
                for item in self.connection.digital_human_sessions.values()
                if item.get("status") == "active"
                and (
                    item.get("resourceKey") == resource_key
                    or (item.get("userId") == user_id and item.get("deviceId") == device_id)
                )
            ]
        elif normalized.startswith("SELECT payload FROM digital_human_sessions"):
            session_id = params[0]
            item = self.connection.digital_human_sessions.get(session_id)
            self.result = None if item is None else {"payload": dict(item)}
        elif normalized.startswith(
            "SELECT COUNT(*) AS count FROM digital_human_sessions WHERE status = 'active'"
        ):
            self.result = {
                "count": sum(
                    1
                    for item in self.connection.digital_human_sessions.values()
                    if item.get("status") == "active"
                )
            }
        elif normalized.startswith(
            "SELECT operation_kind, schema_version, payload_hash, result FROM kb_operation_receipts"
        ):
            user_id, operation_id = params
            item = self.connection.kb_operation_receipts.get(user_id, {}).get(operation_id)
            self.result = None if item is None else deepcopy(item)
        elif normalized.startswith("SELECT revision, graph, mutation, created_at FROM kb_changes"):
            user_id, operation_id = params
            item = next(
                (
                    change
                    for change in self.connection.kb_changes.get(user_id, [])
                    if change["operation_id"] == operation_id
                ),
                None,
            )
            self.result = None if item is None else dict(item)
        elif normalized.startswith("SELECT graph, revision, updated_at FROM kb_snapshots"):
            user_id = params[0]
            value = self.connection.kb_snapshots.get(user_id)
            if value is None:
                self.result = None
            else:
                self.result = {
                    "graph": value,
                    "revision": self.connection.kb_snapshot_revisions.get(user_id, 0),
                    "updated_at": "2026-07-10T00:00:00+00:00",
                }
        elif normalized.startswith(
            "SELECT minimum_since_revision FROM kb_change_feed_state"
        ):
            user_id = params[0]
            minimum = self.connection.kb_change_feed_minimum_since_revisions.get(user_id)
            self.result = (
                None
                if minimum is None
                else {"minimum_since_revision": minimum}
            )
        elif normalized.startswith("SELECT graph FROM kb_snapshots"):
            user_id = params[0]
            value = self.connection.kb_snapshots.get(user_id)
            self.result = None if value is None else {"graph": value}
        elif normalized.startswith(
            "SELECT revision, operation_id, graph, mutation, created_at FROM kb_changes"
        ):
            user_id, since_revision = params[:2]
            param_index = 2
            through_revision = None
            if "revision <= %s" in normalized:
                through_revision = params[param_index]
                param_index += 1
            limit = None
            if "LIMIT %s" in normalized:
                limit = params[param_index]
            matches = [
                dict(item)
                for item in self.connection.kb_changes.get(user_id, [])
                if int(item["revision"]) > since_revision
                and (
                    through_revision is None
                    or int(item["revision"]) <= through_revision
                )
            ]
            matches.sort(key=lambda item: int(item["revision"]))
            self.result = matches if limit is None else matches[:limit]
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
        elif normalized.startswith(
            "SELECT payload, row_version FROM archive_items WHERE user_id = %s AND id = %s"
        ):
            user_id, item_id = params
            item = next(
                (
                    item
                    for item in self.connection.archive_items.get(user_id, [])
                    if item.get("id") == item_id
                ),
                None,
            )
            self.result = None if item is None else {
                "payload": item,
                "row_version": int(item.get("resourceVersion") or 1),
            }
        elif normalized.startswith(
            "SELECT payload FROM archive_items WHERE user_id = %s AND id = %s"
        ):
            user_id, item_id = params
            item = next(
                (
                    item
                    for item in self.connection.archive_items.get(user_id, [])
                    if item.get("id") == item_id
                ),
                None,
            )
            self.result = None if item is None else {"payload": item}
        elif normalized.startswith("SELECT payload FROM archive_items"):
            user_id = params[0]
            self.result = [{"payload": item} for item in self.connection.archive_items.get(user_id, [])]
        elif normalized.startswith("SELECT id, vault_id, owner_subject_id, row_version, authority_state FROM"):
            item_id = params[0]
            table_collections = {
                "archive_items": self.connection.archive_items,
                "digital_human_sessions": {
                    str(item.get("userId") or ""): [item]
                    for item in self.connection.digital_human_sessions.values()
                },
                "family_members": self.connection.family_members,
                "mailbox_letters": self.connection.mailbox_letters,
                "voice_profiles": self.connection.voice_profiles,
            }
            table = next((name for name in table_collections if f"FROM {name}" in normalized), None)
            self.result = None
            if table is not None:
                for user_id, items in table_collections[table].items():
                    for item in items:
                        candidate_id = str(
                            item.get("id")
                            or item.get("sessionId")
                            or item.get("voiceProfileId")
                            or ""
                        )
                        if candidate_id == item_id:
                            self.result = {
                                "id": item_id,
                                "vault_id": user_id,
                                "owner_subject_id": user_id,
                                "row_version": int(item.get("resourceVersion") or 1),
                                "authority_state": str(item.get("authorityState") or "active"),
                            }
                            break
                    if self.result is not None:
                        break
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
        elif normalized.startswith("SELECT provider_speaker_id, voice_profile_id, user_id, persona_scope, digital_human_id, status, training_attempts, configured, assigned_at, updated_at FROM voice_clone_slots WHERE voice_profile_id = %s"):
            voice_profile_id = params[0]
            self.result = next(
                (
                    dict(slot)
                    for slot in self.connection.voice_clone_slots.values()
                    if slot.get("voice_profile_id") == voice_profile_id
                ),
                None,
            )
        elif normalized.startswith("SELECT provider_speaker_id, voice_profile_id, user_id, persona_scope, digital_human_id, status, training_attempts, configured, assigned_at, updated_at FROM voice_clone_slots"):
            self.result = [dict(slot) for slot in self.connection.voice_clone_slots.values()]
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
        elif normalized.startswith("SELECT id, payload FROM users WHERE id = %s"):
            user_id = params[0]
            user = self.connection.users.get(user_id)
            self.result = (
                None
                if user is None
                else {"id": user_id, "payload": deepcopy(user)}
            )
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
        elif normalized.startswith("INSERT INTO token_families"):
            family_id, user_id, status, version, contract_version, created_at, updated_at = params
            self.connection.auth_token_families[family_id] = {
                "id": family_id,
                "user_id": user_id,
                "status": status,
                "current_session_version": version,
                "contract_version": contract_version,
                "created_at": created_at,
                "updated_at": updated_at,
            }
            self.rowcount = 1
            self.result = None
        elif normalized.startswith("SELECT user_id, family_id, payload FROM auth_sessions"):
            token_hash = params[0]
            session = next(
                (
                    item
                    for item in self.connection.auth_sessions.values()
                    if item.get("refreshTokenHash") == token_hash
                ),
                None,
            )
            self.result = (
                None
                if session is None
                else {
                    "user_id": session["userId"],
                    "family_id": session.get("tokenFamilyId"),
                    "payload": dict(session),
                }
            )
        elif normalized.startswith("SELECT a.payload, a.status, a.access_expires_at"):
            token_hash = params[0]
            session = next(
                (
                    item
                    for item in self.connection.auth_sessions.values()
                    if item.get("accessTokenHash") == token_hash
                ),
                None,
            )
            if session is None:
                self.result = None
            else:
                family_id = session.get("tokenFamilyId")
                family = self.connection.auth_token_families.get(family_id, {})
                self.result = {
                    "payload": dict(session),
                    "status": session.get("status"),
                    "access_expires_at": session.get("accessExpiresAt"),
                    "family_id": family_id,
                    "session_version": session.get("sessionVersion"),
                    "family_status": family.get("status", "legacy"),
                }
        elif normalized.startswith(
            "SELECT id, user_id, family_id, session_version FROM auth_sessions"
        ):
            token_hash = params[0]
            session = next(
                (
                    item
                    for item in self.connection.auth_sessions.values()
                    if item.get("accessTokenHash") == token_hash
                ),
                None,
            )
            self.result = (
                None
                if session is None
                else {
                    "id": session["sessionId"],
                    "user_id": session["userId"],
                    "family_id": session.get("tokenFamilyId"),
                    "session_version": session.get("sessionVersion"),
                }
            )
        elif normalized.startswith("SELECT id, user_id, family_id, session_version, status, refresh_expires_at"):
            token_hash = params[0]
            session = next(
                (
                    item
                    for item in self.connection.auth_sessions.values()
                    if item.get("refreshTokenHash") == token_hash
                ),
                None,
            )
            self.result = (
                None
                if session is None
                else {
                    "id": session["sessionId"],
                    "user_id": session["userId"],
                    "family_id": session.get("tokenFamilyId"),
                    "session_version": session.get("sessionVersion"),
                    "status": session.get("status"),
                    "refresh_expires_at": session.get("refreshExpiresAt"),
                    "payload": dict(session),
                }
            )
        elif normalized.startswith("SELECT id, user_id, status, current_session_version FROM token_families"):
            family = self.connection.auth_token_families.get(params[0])
            self.result = None if family is None else dict(family)
        elif normalized.startswith("SELECT id FROM token_families WHERE id = %s"):
            family = self.connection.auth_token_families.get(params[0])
            self.result = None if family is None else {"id": family["id"]}
        elif normalized.startswith("UPDATE token_families SET current_session_version"):
            version, updated_at, family_id = params
            family = self.connection.auth_token_families.get(family_id)
            if family is not None and family.get("status") == "active":
                family["current_session_version"] = version
                family["updated_at"] = updated_at
                self.rowcount = 1
            self.result = None
        elif normalized.startswith("UPDATE token_families SET status = 'revoked'"):
            revoked_at, reason, updated_at, family_id = params
            family = self.connection.auth_token_families.get(family_id)
            if family is not None and family.get("status") == "active":
                family.update(
                    {
                        "status": "revoked",
                        "revoked_at": revoked_at,
                        "revoke_reason": reason,
                        "updated_at": updated_at,
                    }
                )
                self.rowcount = 1
            self.result = None
        elif normalized.startswith("INSERT INTO session_events"):
            event_id, family_id, session_id, user_id, event_type, reason, version, contract, occurred_at = params
            self.connection.auth_session_events[event_id] = {
                "id": event_id,
                "family_id": family_id,
                "session_id": session_id,
                "user_id": user_id,
                "event_type": event_type,
                "reason": reason,
                "session_version": version,
                "contract_version": contract,
                "occurred_at": occurred_at,
            }
            self.rowcount = 1
            self.result = None
        elif normalized.startswith("SELECT payload FROM auth_sessions WHERE access_token_hash"):
            token_hash = params[0]
            session = next(
                (
                    item
                    for item in self.connection.auth_sessions.values()
                    if item.get("accessTokenHash") == token_hash
                ),
                None,
            )
            self.result = None if session is None else {"payload": dict(session)}
        elif normalized.startswith("SELECT payload FROM auth_sessions WHERE refresh_token_hash"):
            token_hash = params[0]
            session = next(
                (
                    item
                    for item in self.connection.auth_sessions.values()
                    if item.get("refreshTokenHash") == token_hash
                ),
                None,
            )
            self.result = None if session is None else {"payload": dict(session)}
        elif normalized.startswith("INSERT INTO auth_sessions"):
            session_id = params[0]
            payload = unwrap_jsonb(params[5])
            self.connection.auth_sessions[session_id] = dict(payload)
            self.rowcount = 1
            self.result = {"payload": payload}
        elif normalized.startswith("UPDATE auth_sessions SET status = 'rotated'") and "WHERE id = %s" in normalized:
            patch, successor_id, rotated_at, session_id = params
            patch = unwrap_jsonb(patch)
            session = self.connection.auth_sessions.get(session_id)
            if session is not None and session.get("status") == "active":
                session.update(patch)
                session["successorSessionId"] = successor_id
                session["rotatedAt"] = rotated_at
                self.rowcount = 1
            self.result = None
        elif normalized.startswith("UPDATE auth_sessions SET status = 'revoked'") and "WHERE family_id = %s" in normalized:
            patch, _, _, family_id = params
            patch = unwrap_jsonb(patch)
            for session in self.connection.auth_sessions.values():
                if session.get("tokenFamilyId") == family_id and session.get("status") == "active":
                    session.update(patch)
                    self.rowcount += 1
            self.result = None
        elif (
            normalized.startswith("UPDATE auth_sessions SET status = 'revoked'")
            and "access_token_hash = %s" in normalized
        ):
            patch, _, _, access_token_hash, user_id = params
            patch = unwrap_jsonb(patch)
            match = next(
                (
                    item
                    for item in self.connection.auth_sessions.values()
                    if item.get("accessTokenHash") == access_token_hash
                    and item.get("userId") == user_id
                    and item.get("status") == "active"
                ),
                None,
            )
            if match is None:
                self.result = None
            else:
                match.update(patch)
                self.rowcount = 1
                self.result = {
                    "id": match["sessionId"],
                    "user_id": match["userId"],
                    "family_id": match.get("tokenFamilyId"),
                    "session_version": match.get("sessionVersion"),
                    "payload": dict(match),
                }
        elif normalized.startswith("UPDATE auth_sessions SET reuse_detected_at"):
            reuse_at, session_id = params
            session = self.connection.auth_sessions.get(session_id)
            if session is not None:
                session["reuseDetectedAt"] = reuse_at
                self.rowcount = 1
            self.result = None
        elif normalized.startswith("UPDATE auth_sessions"):
            patch = unwrap_jsonb(params[0])
            if "WHERE refresh_token_hash = %s" in normalized:
                _, refresh_token_hash, cutoff_iso = params[1:]
                match = next(
                    (
                        item
                        for item in self.connection.auth_sessions.values()
                        if item.get("refreshTokenHash") == refresh_token_hash
                        and item.get("status") == "active"
                        and str(item.get("refreshExpiresAt") or "") > cutoff_iso
                    ),
                    None,
                )
            else:
                access_token_hash = params[2]
                match = next(
                    (
                        item
                        for item in self.connection.auth_sessions.values()
                        if item.get("accessTokenHash") == access_token_hash
                        and item.get("status") == "active"
                    ),
                    None,
                )
            if match is None:
                self.result = None
            else:
                match.update(patch)
                self.result = {"payload": dict(match)}
        elif normalized.startswith("DELETE FROM auth_sessions"):
            user_id = params[0]
            deleted = [
                {"payload": item}
                for item in self.connection.auth_sessions.values()
                if item.get("userId") == user_id
            ]
            self.connection.auth_sessions = {
                session_id: item
                for session_id, item in self.connection.auth_sessions.items()
                if item.get("userId") != user_id
            }
            self.result = deleted
        elif normalized.startswith("INSERT INTO digital_human_sessions"):
            session_id = params[0]
            payload = unwrap_jsonb(params[7])
            self.connection.digital_human_sessions[session_id] = dict(payload)
            self.result = {"payload": payload}
        elif normalized.startswith("UPDATE digital_human_sessions"):
            payload = unwrap_jsonb(params[6])
            session_id = params[10]
            self.connection.digital_human_sessions[session_id] = dict(payload)
            self.result = {"payload": payload}
        elif normalized.startswith("DELETE FROM digital_human_sessions"):
            user_id = params[0]
            deleted = [
                {"payload": item}
                for item in self.connection.digital_human_sessions.values()
                if item.get("userId") == user_id
            ]
            self.connection.digital_human_sessions = {
                session_id: item
                for session_id, item in self.connection.digital_human_sessions.items()
                if item.get("userId") != user_id
            }
            self.result = deleted
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
        elif normalized.startswith("INSERT INTO kb_change_feed_state"):
            user_id = params[0]
            minimum = int(params[1]) if len(params) > 1 else 0
            self.connection.kb_change_feed_minimum_since_revisions.setdefault(
                user_id,
                minimum,
            )
            self.result = {
                "minimum_since_revision": self.connection.kb_change_feed_minimum_since_revisions[user_id]
            }
        elif normalized.startswith("INSERT INTO kb_snapshots"):
            if len(params) == 3:
                user_id, graph, revision = params
            else:
                user_id, graph = params
                revision = self.connection.kb_snapshot_revisions.get(user_id, 0) + 1
            graph = unwrap_jsonb(graph)
            self.connection.kb_snapshots[user_id] = dict(graph)
            self.connection.kb_snapshot_revisions[user_id] = revision
            self.result = {
                "graph": graph,
                "revision": revision,
                "updated_at": "2026-07-10T00:00:00+00:00",
            }
        elif normalized.startswith("INSERT INTO kb_operation_receipts"):
            user_id, operation_id, operation_kind, schema_version, payload_hash, result = params
            result = unwrap_jsonb(result)
            item = {
                "operation_kind": operation_kind,
                "schema_version": schema_version,
                "payload_hash": payload_hash,
                "result": deepcopy(result),
            }
            self.connection.kb_operation_receipts.setdefault(user_id, {})[operation_id] = item
            self.result = None
        elif normalized.startswith("INSERT INTO kb_changes"):
            user_id, revision, operation_id, graph, mutation = params
            graph = unwrap_jsonb(graph)
            mutation = unwrap_jsonb(mutation)
            item = {
                "revision": revision,
                "operation_id": operation_id,
                "graph": dict(graph),
                "mutation": None if mutation is None else dict(mutation),
                "created_at": "2026-07-10T00:00:00+00:00",
            }
            self.connection.kb_changes.setdefault(user_id, []).append(item)
            self.result = dict(item)
        elif normalized.startswith("INSERT INTO memories"):
            user_id, item_id, payload = params
            payload = unwrap_jsonb(payload)
            if has_resource_conflict(self.connection.memories, user_id, item_id):
                self.result = None
                return
            self.connection.memories.setdefault(user_id, []).insert(0, dict(payload))
            self.result = {"payload": payload}
        elif normalized.startswith("INSERT INTO archive_items"):
            user_id, item_id, payload = params
            payload = unwrap_jsonb(payload)
            conflicting_owner = next(
                (
                    owner_id
                    for owner_id, owner_items in self.connection.archive_items.items()
                    if owner_id != user_id
                    and any(item.get("id") == item_id for item in owner_items)
                ),
                None,
            )
            if conflicting_owner is not None and "WHERE archive_items.user_id = EXCLUDED.user_id" in normalized:
                self.result = None
                return
            items = self.connection.archive_items.setdefault(user_id, [])
            items[:] = [item for item in items if item.get("id") != item_id]
            items.insert(0, dict(payload))
            self.result = {"payload": payload}
        elif normalized.startswith("DELETE FROM archive_items"):
            if len(params) == 1:
                user_id = params[0]
                self.result = [
                    {"payload": item}
                    for item in self.connection.archive_items.pop(user_id, [])
                ]
                return
            user_id, item_id = params
            items = self.connection.archive_items.get(user_id, [])
            for index, item in enumerate(items):
                if item.get("id") == item_id:
                    self.result = {"payload": items.pop(index)}
                    break
            else:
                self.result = None
        elif normalized.startswith("DELETE FROM kb_operation_receipts"):
            user_id = params[0]
            deleted = [
                {"operation_id": operation_id}
                for operation_id in self.connection.kb_operation_receipts.pop(user_id, {})
            ]
            self.result = deleted
        elif normalized.startswith("DELETE FROM kb_change_feed_state"):
            user_id = params[0]
            minimum = self.connection.kb_change_feed_minimum_since_revisions.pop(
                user_id,
                None,
            )
            self.result = [] if minimum is None else [{"user_id": user_id}]
        elif normalized.startswith("UPDATE archive_items"):
            payload, user_id, item_id = params[:3]
            cutoff_iso = params[3] if len(params) > 3 else None
            payload = unwrap_jsonb(payload)
            items = self.connection.archive_items.get(user_id, [])
            if self.connection.deliver_archive_item_before_next_update:
                self.connection.deliver_archive_item_before_next_update = False
                for existing in items:
                    if existing.get("id") != item_id:
                        continue
                    metadata = dict(existing.get("metadata") or {})
                    existing["deliveryStatus"] = "delivered"
                    existing["deliveryExecutionState"] = "delivered"
                    metadata["deliveryStatus"] = "delivered"
                    metadata["deliveryExecutionState"] = "delivered"
                    existing["metadata"] = metadata
                    break
            for index, item in enumerate(items):
                if item.get("id") == item_id:
                    if (
                        "payload->>'deliveryStatus'" in normalized
                        and "= 'scheduled'" in normalized
                        and (
                            item.get("deliveryStatus")
                            or (item.get("metadata") or {}).get("deliveryStatus")
                            or (item.get("metadata") or {}).get("deliveryExecutionState")
                        )
                        != "scheduled"
                    ):
                        self.result = None
                        break
                    if (
                        cutoff_iso is not None
                        and "payload->>'openAt'" in normalized
                        and str(item.get("openAt") or (item.get("metadata") or {}).get("openAt") or "") > cutoff_iso
                    ):
                        self.result = None
                        break
                    items[index] = dict(payload)
                    self.result = {"payload": payload}
                    break
            else:
                self.result = None
        elif normalized.startswith("INSERT INTO mailbox_letters"):
            user_id, item_id, payload = params
            payload = unwrap_jsonb(payload)
            if has_resource_conflict(self.connection.mailbox_letters, user_id, item_id):
                self.result = None
                return
            letters = self.connection.mailbox_letters.setdefault(user_id, [])
            letters[:] = [item for item in letters if item.get("id") != item_id]
            letters.insert(0, dict(payload))
            self.result = {"payload": payload}
        elif normalized.startswith("INSERT INTO echo_delayed_replies"):
            user_id, item_id, payload = params
            payload = unwrap_jsonb(payload)
            if has_resource_conflict(self.connection.echo_delayed_replies, user_id, item_id):
                self.result = None
                return
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
            if has_resource_conflict(self.connection.push_device_tokens, user_id, item_id):
                self.result = None
                return
            tokens = self.connection.push_device_tokens.setdefault(user_id, [])
            tokens[:] = [item for item in tokens if item.get("id") != item_id]
            tokens.insert(0, dict(payload))
            self.result = {"payload": payload}
        elif normalized.startswith("INSERT INTO voice_profiles"):
            user_id, item_id, payload = params
            payload = unwrap_jsonb(payload)
            conflicting_owner = next(
                (
                    existing_user_id
                    for existing_user_id, items in self.connection.voice_profiles.items()
                    if existing_user_id != user_id
                    and any(item.get("voiceProfileId") == item_id for item in items)
                ),
                None,
            )
            if conflicting_owner is not None:
                self.result = None
                return
            profiles = self.connection.voice_profiles.setdefault(user_id, [])
            profiles[:] = [item for item in profiles if item.get("voiceProfileId") != item_id]
            profiles.insert(0, dict(payload))
            self.result = {"payload": payload}
        elif normalized.startswith("INSERT INTO voice_clone_slots"):
            provider_speaker_id = params[0]
            slot = self.connection.voice_clone_slots.setdefault(
                provider_speaker_id,
                {
                    "provider_speaker_id": provider_speaker_id,
                    "voice_profile_id": None,
                    "user_id": None,
                    "persona_scope": None,
                    "digital_human_id": None,
                    "status": "available",
                    "training_attempts": 0,
                    "configured": True,
                    "assigned_at": None,
                    "updated_at": "2026-07-10T00:00:00+00:00",
                },
            )
            slot["configured"] = True
            self.result = dict(slot)
        elif normalized.startswith("WITH candidate AS") and "UPDATE voice_clone_slots" in normalized:
            provider_speaker_ids, voice_profile_id, candidate_user_id, _, assigned_voice_profile_id, user_id, persona_scope, digital_human_id, _ = params
            candidates = [
                slot
                for provider_speaker_id, slot in self.connection.voice_clone_slots.items()
                if provider_speaker_id in provider_speaker_ids
                and slot.get("configured") is True
                and (
                    (
                        slot.get("voice_profile_id") == voice_profile_id
                        and slot.get("user_id") == candidate_user_id
                        and slot.get("status") not in {"retired", "deleted"}
                    )
                    or (
                        slot.get("voice_profile_id") is None
                        and slot.get("status") == "available"
                    )
                )
            ]
            candidates.sort(
                key=lambda slot: (
                    0 if slot.get("voice_profile_id") == voice_profile_id else 1,
                    slot.get("provider_speaker_id") or "",
                )
            )
            if not candidates:
                self.result = None
            else:
                slot = candidates[0]
                existing_assignment = slot.get("voice_profile_id") == voice_profile_id
                slot["voice_profile_id"] = assigned_voice_profile_id
                slot["user_id"] = user_id
                slot["persona_scope"] = persona_scope
                slot["digital_human_id"] = digital_human_id
                if not existing_assignment:
                    slot["status"] = "assigned"
                    slot["assigned_at"] = "2026-07-10T00:00:00+00:00"
                slot["updated_at"] = "2026-07-10T00:00:00+00:00"
                self.result = dict(slot)
        elif normalized.startswith("UPDATE voice_clone_slots") and "WHERE user_id = %s" in normalized:
            user_id = params[0]
            retired = []
            for slot in self.connection.voice_clone_slots.values():
                if slot.get("user_id") != user_id or slot.get("status") in {"retired", "deleted"}:
                    continue
                slot["status"] = "retired"
                slot["updated_at"] = "2026-07-10T00:00:00+00:00"
                retired.append({"provider_speaker_id": slot.get("provider_speaker_id")})
            self.result = retired
        elif normalized.startswith("UPDATE voice_clone_slots"):
            status, increment_attempts, voice_profile_id = params
            slot = next(
                (
                    item
                    for item in self.connection.voice_clone_slots.values()
                    if item.get("voice_profile_id") == voice_profile_id
                ),
                None,
            )
            if slot is None:
                self.result = None
            else:
                slot["status"] = status
                slot["training_attempts"] = int(slot.get("training_attempts") or 0) + int(increment_attempts)
                slot["updated_at"] = "2026-07-10T00:00:00+00:00"
                self.result = dict(slot)
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
            if has_resource_conflict(self.connection.family_members, user_id, item_id):
                self.result = None
                return
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
    def __init__(self, *, advisory_barrier=None):
        self.executed = []
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0
        self.advisory_barrier = advisory_barrier
        self.users = {}
        self.kb_snapshots = {}
        self.kb_snapshot_revisions = {}
        self.kb_changes = {}
        self.kb_change_feed_minimum_since_revisions = {}
        self.kb_operation_receipts = {}
        self.memories = {}
        self.archive_items = {}
        self.mailbox_letters = {}
        self.echo_delayed_replies = {}
        self.push_device_tokens = {}
        self.voice_profiles = {}
        self.voice_clone_slots = {}
        self.digital_human_sessions = {}
        self.auth_sessions = {}
        self.auth_token_families = {}
        self.auth_session_events = {}
        self.evidence_events = {}
        self.profiles = {}
        self.password_credentials = {}
        self.family_members = {}
        self.care_snapshots = {}
        self.deliver_archive_item_before_next_update = False

    def cursor(self, row_factory=None):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closes += 1


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
        self.closes = 0

    def cursor(self, row_factory=None):
        return FailingCursor(self.error)

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closes += 1


class FailOnArchiveDeleteCursor(FakeCursor):
    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        if (
            self.connection.fail_archive_delete
            and normalized.startswith("DELETE FROM archive_items")
            and len(params or ()) == 2
        ):
            raise RuntimeError("archive delete failed")
        return super().execute(sql, params)


class TransactionalFailingConnection(FakeConnection):
    def __init__(self):
        super().__init__()
        self.fail_archive_delete = False
        self._capture_committed_state()

    def cursor(self, row_factory=None):
        return FailOnArchiveDeleteCursor(self)

    def commit(self):
        super().commit()
        self._capture_committed_state()

    def rollback(self):
        super().rollback()
        state = deepcopy(self._committed_state)
        self.kb_snapshots = state["kb_snapshots"]
        self.kb_snapshot_revisions = state["kb_snapshot_revisions"]
        self.kb_changes = state["kb_changes"]
        self.kb_change_feed_minimum_since_revisions = state[
            "kb_change_feed_minimum_since_revisions"
        ]
        self.kb_operation_receipts = state["kb_operation_receipts"]
        self.archive_items = state["archive_items"]

    def _capture_committed_state(self):
        self._committed_state = deepcopy(
            {
                "kb_snapshots": self.kb_snapshots,
                "kb_snapshot_revisions": self.kb_snapshot_revisions,
                "kb_changes": self.kb_changes,
                "kb_change_feed_minimum_since_revisions": self.kb_change_feed_minimum_since_revisions,
                "kb_operation_receipts": self.kb_operation_receipts,
                "archive_items": self.archive_items,
            }
        )


class PostgresStoreTests(unittest.TestCase):
    def test_store_has_no_schema_ddl_entrypoint(self):
        store = PostgresStore(connection_factory=lambda: FakeConnection())

        self.assertFalse(hasattr(store, "init_schema"))

    def test_postgres_evidence_store_is_append_only_idempotent_and_restart_readable(self):
        connection = FakeConnection()
        first_store = PostgresStore(connection_factory=lambda: connection)
        event = make_evidence_event("evt_postgres_release_policy")

        created = first_store.append_evidence_event(
            event,
            retention_class="rolloutObservation",
            expires_at_iso="2026-08-15T12:00:00+00:00",
        )
        duplicate = first_store.append_evidence_event(
            event,
            retention_class="rolloutObservation",
            expires_at_iso="2026-08-15T12:00:00+00:00",
        )
        recreated_store = PostgresStore(connection_factory=lambda: connection)
        summary = recreated_store.summarize_evidence_events(
            operation="releasePolicyDecision",
            now_iso="2026-07-16T13:00:00+00:00",
        )

        self.assertEqual(created["outcome"], "appended")
        self.assertEqual(duplicate["outcome"], "deduplicated")
        self.assertEqual(summary["eventCount"], 1)
        self.assertEqual(summary["decisionCounts"], {"typedRuntimeContract": 1})
        self.assertEqual(summary["featureCounts"], {"runtimeConfig": 1})
        self.assertEqual(summary["events"][0]["eventId"], event["eventId"])

        with self.assertRaises(EvidenceEventConflict):
            recreated_store.append_evidence_event(
                make_evidence_event(
                    "evt_postgres_release_policy",
                    reason="tamperedReason",
                ),
                retention_class="rolloutObservation",
                expires_at_iso="2026-08-15T12:00:00+00:00",
            )

    def test_postgres_store_persists_and_rotates_opaque_auth_sessions(self):
        connection = FakeConnection()
        store = PostgresStore(connection_factory=lambda: connection)
        service = AuthSessionService(
            store,
            access_ttl_seconds=900,
            refresh_ttl_seconds=3600,
        )
        now = datetime.now(timezone.utc)

        issued = service.issue("user-auth", now=now)
        resolved = service.resolve_access_token(issued["accessToken"], now=now)
        refreshed = service.refresh(issued["refreshToken"], now=now)

        self.assertEqual(resolved["userId"], "user-auth")
        self.assertNotEqual(refreshed["accessToken"], issued["accessToken"])
        self.assertIsNone(service.resolve_access_token(issued["accessToken"], now=now))
        with self.assertRaises(AuthSessionError):
            service.refresh(issued["refreshToken"], now=now)
        auth_sql = "\n".join(statement for statement, _ in connection.executed)
        self.assertIn("UPDATE auth_sessions", auth_sql)
        self.assertIn("WHERE refresh_token_hash = %s FOR UPDATE", auth_sql)
        self.assertIn("UPDATE token_families", auth_sql)
        self.assertIn("INSERT INTO session_events", auth_sql)
        self.assertNotIn("SELECT payload FROM auth_sessions WHERE refresh_token_hash", auth_sql)

    def test_postgres_session_revoke_uses_user_family_session_lock_order(self):
        connection = FakeConnection()
        store = PostgresStore(connection_factory=lambda: connection)
        service = AuthSessionService(
            store,
            access_ttl_seconds=900,
            refresh_ttl_seconds=3600,
        )
        issued = service.issue("user-revoke-lock-order")
        connection.executed.clear()

        revoked = service.revoke_access_token(
            issued["accessToken"],
            scope="session",
            reason="logout",
        )

        statements = [" ".join(statement.split()) for statement, _ in connection.executed]
        advisory_index = next(
            index
            for index, statement in enumerate(statements)
            if "pg_advisory_xact_lock" in statement
        )
        family_index = next(
            index
            for index, statement in enumerate(statements)
            if statement.startswith("SELECT id FROM token_families")
            and "FOR UPDATE" in statement
        )
        session_index = next(
            index
            for index, statement in enumerate(statements)
            if statement.startswith("UPDATE auth_sessions")
            and "access_token_hash" in statement
        )
        self.assertEqual(revoked["scope"], "session")
        self.assertLess(advisory_index, family_index)
        self.assertLess(family_index, session_index)

    def test_in_memory_store_arbitrates_digital_human_session_leases(self):
        store = InMemoryStore()
        first = store.acquire_digital_human_session_lease(
            make_digital_human_lease("session-1"),
            max_concurrent_sessions=1,
            now_iso="2026-07-10T00:00:00+00:00",
        )
        repeated = store.acquire_digital_human_session_lease(
            make_digital_human_lease(
                "session-duplicate",
                expires_at="2026-07-10T00:04:00+00:00",
            ),
            max_concurrent_sessions=1,
            now_iso="2026-07-10T00:01:00+00:00",
        )
        conflict = store.acquire_digital_human_session_lease(
            make_digital_human_lease(
                "session-2",
                user_id="u2",
                device_id="device-2",
            ),
            max_concurrent_sessions=1,
            now_iso="2026-07-10T00:01:00+00:00",
        )

        self.assertEqual(first["outcome"], "created")
        self.assertEqual(repeated["outcome"], "reused")
        self.assertEqual(repeated["lease"]["sessionId"], "session-1")
        self.assertEqual(repeated["lease"]["expiresAt"], "2026-07-10T00:04:00+00:00")
        self.assertEqual(conflict["outcome"], "conflict")
        self.assertEqual(conflict["activeSessionCount"], 1)
        self.assertGreater(conflict["retryAfterSeconds"], 0)

        heartbeat = store.heartbeat_digital_human_session_lease(
            "session-1",
            user_id="u1",
            device_id="device-1",
            heartbeat_at_iso="2026-07-10T00:02:00+00:00",
            expires_at_iso="2026-07-10T00:05:00+00:00",
        )
        released = store.release_digital_human_session_lease(
            "session-1",
            user_id="u1",
            device_id="device-1",
            released_at_iso="2026-07-10T00:02:30+00:00",
            reason="pageExit",
        )
        repeated_release = store.release_digital_human_session_lease(
            "session-1",
            user_id="u1",
            device_id="device-1",
            released_at_iso="2026-07-10T00:02:40+00:00",
            reason="pageExit",
        )

        self.assertEqual(heartbeat["outcome"], "active")
        self.assertEqual(heartbeat["lease"]["expiresAt"], "2026-07-10T00:05:00+00:00")
        self.assertEqual(released["outcome"], "released")
        self.assertEqual(released["lease"]["releaseReason"], "pageExit")
        self.assertEqual(repeated_release["outcome"], "alreadyReleased")

        after_release = store.acquire_digital_human_session_lease(
            make_digital_human_lease(
                "session-2",
                user_id="u2",
                device_id="device-2",
                created_at="2026-07-10T00:03:00+00:00",
                expires_at="2026-07-10T00:06:00+00:00",
            ),
            max_concurrent_sessions=1,
            now_iso="2026-07-10T00:03:00+00:00",
        )
        self.assertEqual(after_release["outcome"], "created")

    def test_in_memory_store_replaces_same_device_context_and_expires_abandoned_lease(self):
        store = InMemoryStore()
        store.acquire_digital_human_session_lease(
            make_digital_human_lease(
                "session-old",
                expires_at="2026-07-10T00:01:00+00:00",
            ),
            max_concurrent_sessions=1,
            now_iso="2026-07-10T00:00:00+00:00",
        )
        replacement = store.acquire_digital_human_session_lease(
            make_digital_human_lease(
                "session-new",
                persona_id="persona-2",
                lifecycle_mode="star",
                created_at="2026-07-10T00:00:30+00:00",
                expires_at="2026-07-10T00:03:30+00:00",
            ),
            max_concurrent_sessions=1,
            now_iso="2026-07-10T00:00:30+00:00",
        )

        self.assertEqual(replacement["outcome"], "created")
        old = store.get_digital_human_session_lease("session-old")
        self.assertEqual(old["status"], "released")
        self.assertEqual(old["releaseReason"], "supersededByDeviceContext")

        after_expiry = store.acquire_digital_human_session_lease(
            make_digital_human_lease(
                "session-after-expiry",
                user_id="u2",
                device_id="device-2",
                created_at="2026-07-10T00:04:00+00:00",
                expires_at="2026-07-10T00:07:00+00:00",
            ),
            max_concurrent_sessions=1,
            now_iso="2026-07-10T00:04:00+00:00",
        )
        self.assertEqual(after_expiry["outcome"], "created")
        self.assertEqual(store.get_digital_human_session_lease("session-new")["status"], "expired")

    def test_postgres_store_arbitrates_digital_human_session_leases_under_advisory_lock(self):
        connection = FakeConnection()
        store = PostgresStore(connection_factory=lambda: connection)

        first = store.acquire_digital_human_session_lease(
            make_digital_human_lease("session-1"),
            max_concurrent_sessions=1,
            now_iso="2026-07-10T00:00:00+00:00",
        )
        repeated = store.acquire_digital_human_session_lease(
            make_digital_human_lease("session-duplicate"),
            max_concurrent_sessions=1,
            now_iso="2026-07-10T00:01:00+00:00",
        )
        conflict = store.acquire_digital_human_session_lease(
            make_digital_human_lease(
                "session-2",
                user_id="u2",
                device_id="device-2",
            ),
            max_concurrent_sessions=1,
            now_iso="2026-07-10T00:01:00+00:00",
        )
        heartbeat = store.heartbeat_digital_human_session_lease(
            "session-1",
            user_id="u1",
            device_id="device-1",
            heartbeat_at_iso="2026-07-10T00:02:00+00:00",
            expires_at_iso="2026-07-10T00:05:00+00:00",
        )
        released = store.release_digital_human_session_lease(
            "session-1",
            user_id="u1",
            device_id="device-1",
            released_at_iso="2026-07-10T00:02:30+00:00",
            reason="pageExit",
        )

        self.assertEqual(first["outcome"], "created")
        self.assertEqual(repeated["outcome"], "reused")
        self.assertEqual(conflict["outcome"], "conflict")
        self.assertEqual(heartbeat["outcome"], "active")
        self.assertEqual(released["outcome"], "released")
        sql = "\n".join(statement for statement, _ in connection.executed)
        self.assertIn("pg_advisory_xact_lock", sql)
        self.assertNotIn("accesstoken", str(connection.digital_human_sessions))
        self.assertNotIn("appkey", str(connection.digital_human_sessions))

    def test_postgres_store_expires_abandoned_digital_human_lease_before_capacity_check(self):
        connection = FakeConnection()
        store = PostgresStore(connection_factory=lambda: connection)
        store.acquire_digital_human_session_lease(
            make_digital_human_lease(
                "session-expiring",
                expires_at="2026-07-10T00:01:00+00:00",
            ),
            max_concurrent_sessions=1,
            now_iso="2026-07-10T00:00:00+00:00",
        )

        acquired = store.acquire_digital_human_session_lease(
            make_digital_human_lease(
                "session-after-expiry",
                user_id="u2",
                device_id="device-2",
                created_at="2026-07-10T00:02:00+00:00",
                expires_at="2026-07-10T00:05:00+00:00",
            ),
            max_concurrent_sessions=1,
            now_iso="2026-07-10T00:02:00+00:00",
        )

        self.assertEqual(acquired["outcome"], "created")
        self.assertEqual(store.get_digital_human_session_lease("session-expiring")["status"], "expired")

    def test_in_memory_store_drains_elapsed_digital_human_leases_idempotently(self):
        store = InMemoryStore()
        store.acquire_digital_human_session_lease(
            make_digital_human_lease(
                "session-expired",
                expires_at="2026-07-10T00:01:00+00:00",
            ),
            max_concurrent_sessions=2,
            now_iso="2026-07-10T00:00:00+00:00",
        )
        store.acquire_digital_human_session_lease(
            make_digital_human_lease(
                "session-active",
                user_id="u2",
                device_id="device-2",
                expires_at="2026-07-10T00:05:00+00:00",
            ),
            max_concurrent_sessions=2,
            now_iso="2026-07-10T00:00:00+00:00",
        )

        first = store.drain_expired_digital_human_session_leases(
            now_iso="2026-07-10T00:02:00+00:00"
        )
        repeated = store.drain_expired_digital_human_session_leases(
            now_iso="2026-07-10T00:02:30+00:00"
        )

        self.assertEqual(first["expiredLeaseCount"], 1)
        self.assertEqual(first["activeLeaseCount"], 1)
        self.assertEqual(repeated["expiredLeaseCount"], 0)
        self.assertEqual(repeated["activeLeaseCount"], 1)
        self.assertEqual(store.get_digital_human_session_lease("session-expired")["status"], "expired")

    def test_postgres_store_drains_elapsed_digital_human_leases_idempotently(self):
        connection = FakeConnection()
        store = PostgresStore(connection_factory=lambda: connection)
        store.acquire_digital_human_session_lease(
            make_digital_human_lease(
                "session-expired",
                expires_at="2026-07-10T00:01:00+00:00",
            ),
            max_concurrent_sessions=2,
            now_iso="2026-07-10T00:00:00+00:00",
        )
        store.acquire_digital_human_session_lease(
            make_digital_human_lease(
                "session-active",
                user_id="u2",
                device_id="device-2",
                expires_at="2026-07-10T00:05:00+00:00",
            ),
            max_concurrent_sessions=2,
            now_iso="2026-07-10T00:00:00+00:00",
        )

        first = store.drain_expired_digital_human_session_leases(
            now_iso="2026-07-10T00:02:00+00:00"
        )
        repeated = store.drain_expired_digital_human_session_leases(
            now_iso="2026-07-10T00:02:30+00:00"
        )

        self.assertEqual(first["expiredLeaseCount"], 1)
        self.assertEqual(first["activeLeaseCount"], 1)
        self.assertEqual(repeated["expiredLeaseCount"], 0)
        self.assertEqual(repeated["activeLeaseCount"], 1)
        self.assertEqual(connection.digital_human_sessions["session-expired"]["status"], "expired")

    def test_store_initialization_drains_elapsed_digital_human_leases(self):
        class DrainAwareStore:
            def __init__(self):
                self.events = []

            def init_schema(self):
                raise AssertionError("API startup must not execute schema DDL")

            def drain_expired_digital_human_session_leases(self, *, now_iso):
                self.events.append(("drain", now_iso))

        store = DrainAwareStore()
        init_store(store)

        self.assertEqual(store.events[0][0], "drain")
        self.assertTrue(store.events[0][1].endswith("+00:00"))

    def test_store_persists_kb_snapshot_by_user(self):
        connection = FakeConnection()
        store = PostgresStore(connection_factory=lambda: connection)

        store.save_kb_snapshot("u1", {"people": [{"id": "p1"}]})
        store.save_kb_snapshot("u2", {"people": [{"id": "p2"}]})

        self.assertEqual(store.get_kb_snapshot("u1")["people"][0]["id"], "p1")
        self.assertEqual(store.get_kb_snapshot("u2")["people"][0]["id"], "p2")

    def test_store_applies_idempotent_kb_mutation_and_change_feed(self):
        connection = FakeConnection()
        store = PostgresStore(connection_factory=lambda: connection)

        first = store.apply_kb_mutation(
            "u1",
            {"facts": [{"id": "f1", "statement": "第一条事实"}]},
            operation_id="op-1",
            base_revision=0,
        )
        repeated = store.apply_kb_mutation(
            "u1",
            {"facts": [{"id": "f1", "statement": "第一条事实"}]},
            operation_id="op-1",
            base_revision=999,
        )

        self.assertEqual(first["revision"], 1)
        self.assertFalse(first["duplicate"])
        self.assertTrue(repeated["duplicate"])
        self.assertEqual(store.get_kb_snapshot("u1")["facts"][0]["statement"], "第一条事实")
        self.assertEqual([item["operationId"] for item in store.list_kb_changes("u1", 0)], ["op-1"])
        self.assertEqual(store.list_kb_changes("u1", 0)[0]["mutationSchemaVersion"], 1)
        self.assertIsNone(store.list_kb_changes("u1", 0)[0]["mutation"])
        self.assertTrue(repeated["operationPayloadVerified"])

        with self.assertRaises(KnowledgeOperationPayloadConflict):
            store.apply_kb_mutation(
                "u1",
                {"facts": [{"id": "f1", "statement": "different"}]},
                operation_id="op-1",
                base_revision=1,
            )

        with self.assertRaises(KnowledgeRevisionConflict):
            store.apply_kb_mutation("u1", {"facts": []}, operation_id="op-2", base_revision=0)

    def test_change_feed_uses_revision_upper_bound_order_and_sql_limit(self):
        connection = FakeConnection()
        connection.kb_changes["u1"] = [
            {
                "revision": revision,
                "operation_id": f"op-{revision}",
                "graph": {"facts": []},
                "mutation": None,
                "created_at": "2026-07-10T00:00:00+00:00",
            }
            for revision in [4, 2, 3, 1]
        ]
        store = PostgresStore(connection_factory=lambda: connection)

        changes = store.list_kb_changes(
            "u1",
            since_revision=1,
            through_revision=4,
            limit=2,
        )

        self.assertEqual([item["revision"] for item in changes], [2, 3])
        sql, params = connection.executed[-1]
        self.assertIn("revision > %s AND revision <= %s", sql)
        self.assertIn("ORDER BY revision ASC LIMIT %s", sql)
        self.assertEqual(params, ("u1", 1, 4, 2))

    def test_change_page_reads_floor_snapshot_and_changes_under_user_lock(self):
        connection = FakeConnection()
        connection.kb_snapshots["u1"] = {"facts": []}
        connection.kb_snapshot_revisions["u1"] = 4
        connection.kb_change_feed_minimum_since_revisions["u1"] = 1
        connection.kb_changes["u1"] = [
            {
                "revision": revision,
                "operation_id": f"op-{revision}",
                "graph": {"facts": []},
                "mutation": None,
                "created_at": "2026-07-10T00:00:00+00:00",
            }
            for revision in range(2, 5)
        ]
        store = PostgresStore(connection_factory=lambda: connection)

        page = store.get_kb_change_page(
            "u1",
            since_revision=1,
            through_revision=4,
            limit=2,
        )

        self.assertEqual(page["currentRevision"], 4)
        self.assertEqual(page["minimumSinceRevision"], 1)
        self.assertEqual([item["revision"] for item in page["changes"]], [2, 3])
        sql = [statement for statement, _ in connection.executed]
        self.assertIn("SELECT pg_advisory_xact_lock(hashtext(%s))", sql[0])
        self.assertTrue(any("FROM kb_change_feed_state" in statement for statement in sql))
        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)
        self.assertEqual(connection.closes, 1)

    def test_change_page_rolls_back_and_closes_on_sql_failure(self):
        connection = FailingConnection(RuntimeError("change page failed"))
        store = PostgresStore(connection_factory=lambda: connection)

        with self.assertRaisesRegex(RuntimeError, "change page failed"):
            store.get_kb_change_page("u1", since_revision=0)

        self.assertEqual(connection.rollbacks, 1)
        self.assertEqual(connection.closes, 1)

    def test_store_applies_v2_kb_mutation_and_persists_change_metadata(self):
        connection = FakeConnection()
        store = PostgresStore(connection_factory=lambda: connection)
        metadata = {"privacyMetadata": {"scope": "generationAllowed"}}
        store.apply_kb_mutation(
            "u1",
            {
                "people": [{"id": "shared", "name": "Person", **metadata}],
                "places": [{"id": "place-1", "name": "Garden", **metadata}],
                "events": [],
                "facts": [
                    {"id": "shared", "statement": "Old shared fact", **metadata},
                    {"id": "gone", "statement": "Delete me", **metadata},
                ],
            },
            operation_id="seed",
            base_revision=0,
        )
        mutation = {
            "upserts": {
                "events": [
                    {
                        "id": "event-1",
                        "title": "Garden visit",
                        "participantIds": ["shared"],
                        "locationId": "place-1",
                        **metadata,
                    }
                ],
                "facts": [{"id": "shared", "statement": "Recreated fact", **metadata}],
            },
            "tombstones": [
                {"entityType": "facts", "entityId": "shared", "deletedAt": "2026-07-11T00:00:00Z"},
                {"entityType": "facts", "entityId": "gone", "deletedAt": "2026-07-11T00:00:00Z"},
            ],
        }

        applied = store.apply_kb_mutation(
            "u1",
            None,
            operation_id="v2-op",
            base_revision=1,
            mutation=mutation,
        )
        repeated = store.apply_kb_mutation(
            "u1",
            None,
            operation_id="v2-op",
            base_revision=0,
            mutation=mutation,
        )

        self.assertEqual(applied["revision"], 2)
        self.assertFalse(applied["duplicate"])
        self.assertTrue(repeated["duplicate"])
        self.assertEqual(repeated["graph"], applied["graph"])
        self.assertEqual(repeated["mutation"], applied["mutation"])
        self.assertEqual([item["id"] for item in applied["graph"]["people"]], ["shared"])
        self.assertEqual([item["id"] for item in applied["graph"]["facts"]], ["shared"])
        self.assertEqual(applied["graph"]["events"][0]["participantIds"], ["shared"])
        self.assertEqual(applied["graph"]["events"][0]["locationId"], "place-1")
        change = store.list_kb_changes("u1", 1)[0]
        self.assertEqual(change["mutationSchemaVersion"], 2)
        self.assertEqual(change["mutation"], applied["mutation"])
        self.assertGreaterEqual(connection.commits, 3)
        self.assertEqual(connection.rollbacks, 0)
        self.assertGreaterEqual(connection.closes, 3)

    def test_postgres_v2_mutation_uses_one_canonical_source_ref_contract(self):
        connection = FakeConnection()
        store = PostgresStore(connection_factory=lambda: connection)
        raw_title = "RAW_SOURCE_TITLE_SENTINEL_POSTGRES"
        raw_mutation = {
            "upserts": {
                "facts": [
                    {
                        "id": "fact-1",
                        "statement": "Original body",
                        "relatedPersonIds": ["partial-person"],
                        "privacyMetadata": {
                            "scope": "generationAllowed",
                            "sourceRefs": [
                                {
                                    "kind": "conversationTurn",
                                    "id": "turn-1",
                                    "title": raw_title,
                                    "locator": "turn:1",
                                }
                            ],
                        },
                    }
                ]
            },
            "tombstones": [],
        }
        canonical_mutation = deepcopy(raw_mutation)
        canonical_mutation["upserts"]["facts"][0]["privacyMetadata"]["sourceRefs"][0][
            "title"
        ] = "对话来源"

        first = store.apply_kb_mutation(
            "canonical-user",
            None,
            operation_id="canonical-op",
            base_revision=0,
            mutation=raw_mutation,
        )
        replay = store.apply_kb_mutation(
            "canonical-user",
            None,
            operation_id="canonical-op",
            base_revision=999,
            mutation=canonical_mutation,
        )
        change = store.list_kb_changes("canonical-user", since_revision=0)[0]
        receipt = connection.kb_operation_receipts["canonical-user"]["canonical-op"]

        source_ref = first["mutation"]["upserts"]["facts"][0]["privacyMetadata"][
            "sourceRefs"
        ][0]
        self.assertEqual(
            source_ref,
            {
                "kind": "conversationTurn",
                "id": "turn-1",
                "title": "对话来源",
                "locator": "turn:1",
            },
        )
        self.assertEqual(
            first["mutation"]["upserts"]["facts"][0]["relatedPersonIds"],
            ["partial-person"],
        )
        self.assertEqual(
            raw_mutation["upserts"]["facts"][0]["privacyMetadata"]["sourceRefs"][0][
                "title"
            ],
            raw_title,
        )
        self.assertTrue(replay["duplicate"])
        self.assertTrue(replay["operationPayloadVerified"])
        self.assertEqual(replay["mutation"], first["mutation"])
        for surface in (first, change, receipt, replay):
            self.assertNotIn(raw_title, str(surface))

        changed_kind = deepcopy(canonical_mutation)
        changed_kind["upserts"]["facts"][0]["privacyMetadata"]["sourceRefs"][0][
            "kind"
        ] = "conversationPhoto"
        changed_id = deepcopy(canonical_mutation)
        changed_id["upserts"]["facts"][0]["privacyMetadata"]["sourceRefs"][0][
            "id"
        ] = "turn-2"
        changed_body = deepcopy(canonical_mutation)
        changed_body["upserts"]["facts"][0]["statement"] = "Changed body"
        for field, changed_mutation in (
            ("kind", changed_kind),
            ("id", changed_id),
            ("body", changed_body),
        ):
            with self.subTest(field=field):
                with self.assertRaises(KnowledgeOperationPayloadConflict):
                    store.apply_kb_mutation(
                        "canonical-user",
                        None,
                        operation_id="canonical-op",
                        base_revision=1,
                        mutation=changed_mutation,
                    )

    def test_change_feed_reads_historical_rows_without_mutation_metadata(self):
        connection = FakeConnection()
        connection.kb_changes["u1"] = [
            {
                "revision": 1,
                "operation_id": "historical-v1",
                "graph": {"facts": []},
                "created_at": "2026-07-10T00:00:00+00:00",
            }
        ]
        store = PostgresStore(connection_factory=lambda: connection)

        change = store.list_kb_changes("u1", 0)[0]

        self.assertEqual(change["operationId"], "historical-v1")
        self.assertEqual(change["mutationSchemaVersion"], 1)
        self.assertIsNone(change["mutation"])

    def test_postgres_receipts_are_compact_and_fallback_to_current_snapshot(self):
        connection = FakeConnection()
        store = PostgresStore(connection_factory=lambda: connection)
        mutation = {
            "upserts": {
                "facts": [
                    {
                        "id": "fact-1",
                        "statement": "private postgres receipt body",
                        "privacyMetadata": {"scope": "generationAllowed"},
                    }
                ]
            },
            "tombstones": [],
        }
        first = store.apply_kb_mutation(
            "compact-user",
            None,
            operation_id="compact-op",
            base_revision=0,
            mutation=mutation,
        )
        envelope = connection.kb_operation_receipts["compact-user"]["compact-op"][
            "result"
        ]

        self.assertEqual(envelope["receiptEnvelopeVersion"], 1)
        self.assertNotIn("userId", envelope)
        self.assertNotIn("operationId", envelope)
        self.assertNotIn("operationKind", envelope)
        self.assertNotIn("operationSchemaVersion", envelope)
        self.assertNotIn("graph", envelope)
        self.assertNotIn("mutation", envelope)
        self.assertNotIn("private postgres receipt body", str(envelope))

        from_change = store.apply_kb_mutation(
            "compact-user",
            None,
            operation_id="compact-op",
            base_revision=999,
            mutation=mutation,
        )
        self.assertEqual(from_change["graph"], first["graph"])
        self.assertEqual(from_change["mutation"], first["mutation"])
        self.assertTrue(from_change["receiptCompacted"])
        self.assertEqual(from_change["originalRevision"], 1)

        store.apply_kb_mutation(
            "compact-user",
            {"facts": [{"id": "current"}]},
            operation_id="newer-op",
            base_revision=1,
        )
        connection.kb_changes["compact-user"] = [
            change
            for change in connection.kb_changes["compact-user"]
            if change["operation_id"] != "compact-op"
        ]
        from_snapshot = store.apply_kb_mutation(
            "compact-user",
            None,
            operation_id="compact-op",
            base_revision=999,
            mutation=mutation,
        )

        self.assertTrue(from_snapshot["receiptCompacted"])
        self.assertEqual(from_snapshot["originalRevision"], 1)
        self.assertEqual(from_snapshot["revision"], 2)
        self.assertEqual(from_snapshot["graph"]["facts"][0]["id"], "current")
        self.assertEqual(from_snapshot["mutation"], {
            "upserts": {"people": [], "places": [], "events": [], "facts": []},
            "tombstones": [],
        })

    def test_postgres_reads_legacy_receipt_and_validates_before_compact_lookup(self):
        connection = FakeConnection()
        store = PostgresStore(connection_factory=lambda: connection)
        graph = {"facts": [{"id": "fact-1"}]}
        first = store.apply_kb_mutation(
            "receipt-user",
            graph,
            operation_id="receipt-op",
            base_revision=0,
        )
        receipt = connection.kb_operation_receipts["receipt-user"]["receipt-op"]
        compact_envelope = deepcopy(receipt["result"])
        receipt["result"] = deepcopy(first)

        legacy = store.apply_kb_mutation(
            "receipt-user",
            graph,
            operation_id="receipt-op",
            base_revision=999,
        )
        self.assertTrue(legacy["duplicate"])
        self.assertEqual(legacy["graph"], graph)

        receipt["result"] = compact_envelope
        connection.executed.clear()
        with self.assertRaises(KnowledgeOperationPayloadConflict):
            store.apply_kb_mutation(
                "receipt-user",
                {"facts": [{"id": "different"}]},
                operation_id="receipt-op",
                base_revision=999,
            )
        statements = [statement for statement, _ in connection.executed]
        receipt_index = next(
            index
            for index, statement in enumerate(statements)
            if "FROM kb_operation_receipts" in statement
        )
        self.assertFalse(
            any(
                "FROM kb_changes" in statement or "FROM kb_snapshots" in statement
                for statement in statements[receipt_index + 1 :]
            )
        )

    def test_legacy_change_replay_without_receipt_is_unverified(self):
        connection = FakeConnection()
        connection.kb_changes["u1"] = [
            {
                "revision": 1,
                "operation_id": "legacy-op",
                "graph": {"facts": []},
                "mutation": None,
                "created_at": "2026-07-10T00:00:00+00:00",
            }
        ]
        store = PostgresStore(connection_factory=lambda: connection)

        replay = store.apply_kb_mutation(
            "u1",
            {"facts": [{"id": "different"}]},
            operation_id="legacy-op",
            base_revision=999,
        )

        self.assertTrue(replay["duplicate"])
        self.assertFalse(replay["operationPayloadVerified"])

    def test_operation_id_rejects_cross_schema_replays(self):
        connection = FakeConnection()
        store = PostgresStore(connection_factory=lambda: connection)

        first_v1 = store.apply_kb_mutation(
            "v1-user",
            {"facts": []},
            operation_id="shared-op",
            base_revision=0,
        )
        with self.assertRaises(KnowledgeOperationPayloadConflict):
            store.apply_kb_mutation(
                "v1-user",
                None,
                operation_id="shared-op",
                base_revision=999,
                mutation={
                    "upserts": {
                        "facts": [
                            {
                                "id": "different-fact",
                                "statement": "different schema",
                                "privacyMetadata": {"scope": "generationAllowed"},
                            }
                        ]
                    },
                    "tombstones": [],
                },
            )
        first_v2 = store.apply_kb_mutation(
            "v2-user",
            None,
            operation_id="shared-op",
            base_revision=0,
            mutation={
                "upserts": {
                    "facts": [
                        {
                            "id": "replay-fact",
                            "statement": "schema replay",
                            "privacyMetadata": {"scope": "generationAllowed"},
                        }
                    ]
                },
                "tombstones": [],
            },
        )
        with self.assertRaises(KnowledgeOperationPayloadConflict):
            store.apply_kb_mutation(
                "v2-user",
                {"facts": [{"id": "must-not-apply"}]},
                operation_id="shared-op",
                base_revision=999,
            )

        self.assertEqual(first_v1["mutationSchemaVersion"], 1)
        self.assertEqual(first_v2["mutationSchemaVersion"], 2)

    def test_kb_mutations_and_reads_do_not_keep_a_shared_connection(self):
        barrier = Barrier(2)
        connections = []

        def connection_factory():
            connection = FakeConnection(advisory_barrier=barrier)
            connections.append(connection)
            return connection

        store = PostgresStore(connection_factory=connection_factory)
        self.assertIsNone(store.get_kb_snapshot("cache-warmup"))
        self.assertIsNone(store.get_kb_snapshot("cache-reuse"))
        read_connections = connections[:2]
        self.assertEqual(len(read_connections), 2)
        self.assertEqual([connection.closes for connection in read_connections], [1, 1])

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    store.apply_kb_mutation,
                    f"u{index}",
                    {"facts": [{"id": f"f{index}"}]},
                    operation_id=f"op-{index}",
                    base_revision=0,
                )
                for index in (1, 2)
            ]
            results = [future.result(timeout=3) for future in futures]

        mutation_connections = connections[2:]
        self.assertEqual([result["revision"] for result in results], [1, 1])
        self.assertEqual(len(mutation_connections), 2)
        self.assertIsNot(mutation_connections[0], mutation_connections[1])
        self.assertFalse(hasattr(store, "_connection"))
        self.assertEqual([connection.commits for connection in mutation_connections], [1, 1])
        self.assertEqual([connection.rollbacks for connection in mutation_connections], [0, 0])
        self.assertEqual([connection.closes for connection in mutation_connections], [1, 1])
        self.assertTrue(
            all(
                any("pg_advisory_xact_lock" in sql for sql, _ in connection.executed)
                for connection in mutation_connections
            )
        )

    def test_failed_kb_mutation_rolls_back_and_closes_exclusive_connection(self):
        connection = FailingConnection(RuntimeError("mutation failed"))
        store = PostgresStore(connection_factory=lambda: connection)

        with self.assertRaisesRegex(RuntimeError, "mutation failed"):
            store.apply_kb_mutation(
                "u1",
                {"facts": []},
                operation_id="op-failed",
                base_revision=0,
            )

        self.assertEqual(connection.rollbacks, 1)
        self.assertEqual(connection.closes, 1)
        self.assertFalse(hasattr(store, "_connection"))

    def test_postgres_store_rejects_legacy_zero_base_without_losing_newer_snapshot(self):
        connection = FakeConnection()
        store = PostgresStore(connection_factory=lambda: connection)
        store.apply_kb_mutation(
            "u1",
            {"facts": [{"id": "f1", "statement": "initial"}]},
            operation_id="initial",
            base_revision=0,
        )
        store.apply_kb_mutation(
            "u1",
            {"facts": [{"id": "f1", "statement": "newest"}]},
            operation_id="newest",
            base_revision=1,
        )

        with self.assertRaises(KnowledgeRevisionConflict):
            store.apply_kb_mutation(
                "u1",
                {"facts": [{"id": "f1", "statement": "stale legacy"}]},
                operation_id="legacy-stale",
                base_revision=0,
            )

        snapshot = store.get_kb_snapshot_record("u1")
        self.assertEqual(snapshot["revision"], 2)
        self.assertEqual(snapshot["graph"]["facts"][0]["statement"], "newest")
        self.assertEqual(
            [change["operationId"] for change in store.list_kb_changes("u1", 0)],
            ["initial", "newest"],
        )

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

    def test_generic_resource_upserts_never_transfer_owner_on_id_collision(self):
        connection = FakeConnection()
        store = PostgresStore(connection_factory=lambda: connection)
        writers = (
            (store.add_memory, {"id": "shared-memory", "title": "owner memory"}),
            (store.add_family_member, {"id": "shared-family", "name": "owner family"}),
            (store.add_mailbox_letter, {"id": "shared-mailbox", "title": "owner mailbox"}),
            (
                store.add_echo_delayed_reply,
                {"id": "shared-echo", "delayedReplyId": "shared-echo", "deliveryState": "scheduled"},
            ),
            (
                store.save_push_device_token,
                {"id": "shared-push", "deviceTokenId": "shared-push"},
            ),
        )

        for writer, payload in writers:
            with self.subTest(resource_id=payload["id"]):
                owner_item = writer("u1", payload)
                with self.assertRaisesRegex(ResourceOwnershipConflict, "another owner"):
                    writer("u2", {**payload, "title": "takeover"})
                self.assertEqual(owner_item["userId"], "u1")

    def test_resource_authority_resolver_returns_canonical_database_owner(self):
        connection = FakeConnection()
        store = PostgresStore(connection_factory=lambda: connection)
        store.add_archive_item("u1", {"id": "archive-authority", "kind": "photo"})

        authority = store.resolve_resource_authority("archiveItem", "archive-authority")
        missing = store.resolve_resource_authority("archiveItem", "missing")

        self.assertEqual(
            authority,
            {
                "resourceType": "archiveItem",
                "resourceId": "archive-authority",
                "vaultId": "u1",
                "ownerSubjectId": "u1",
                "rowVersion": 1,
                "authorityState": "active",
            },
        )
        self.assertIsNone(missing)

    def test_store_marks_mailbox_letters_read_and_archived(self):
        connection = FakeConnection()
        store = PostgresStore(connection_factory=lambda: connection)

        store.add_mailbox_letter("u1", {"id": "letter_1", "title": "想说的话", "status": "unread"})
        read = store.mark_mailbox_letter_read("u1", "letter_1", "2026-07-02T12:00:00Z")
        archived = store.archive_mailbox_letter("u1", "letter_1", "2026-07-02T12:05:00Z")
        missing = store.archive_mailbox_letter("u2", "letter_1", "2026-07-02T12:05:00Z")
        listed = store.list_mailbox_letters("u1")

        self.assertEqual(read["status"], "read")
        self.assertEqual(read["readAt"], "2026-07-02T12:00:00Z")
        self.assertEqual(archived["status"], "archived")
        self.assertEqual(archived["archivedAt"], "2026-07-02T12:05:00Z")
        self.assertIsNone(missing)
        self.assertEqual(listed[0]["status"], "archived")

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

    def test_combined_archive_delete_applies_kb_mutation_in_one_transaction(self):
        connection = FakeConnection()
        item = seed_archive_delete_state(connection)
        store = PostgresStore(connection_factory=lambda: connection)

        result = store.delete_archive_item_with_kb_mutation(
            "u1",
            "archive-1",
            operation_id="delete-1",
            base_revision=1,
            mutation=archive_delete_mutation(),
        )

        self.assertEqual(
            set(result),
            {
                "item",
                "duplicate",
                "operationPayloadVerified",
                "revision",
                "graph",
                "mutationSchemaVersion",
                "mutation",
            },
        )
        self.assertEqual(result["item"], item)
        self.assertFalse(result["duplicate"])
        self.assertEqual(result["revision"], 2)
        self.assertEqual(result["graph"]["facts"], [])
        self.assertEqual(result["mutationSchemaVersion"], 2)
        self.assertEqual(connection.archive_items["u1"], [])
        self.assertEqual(connection.kb_snapshot_revisions["u1"], 2)
        self.assertEqual(len(connection.kb_changes["u1"]), 2)
        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)
        self.assertEqual(connection.closes, 1)
        sql = [statement for statement, _ in connection.executed]
        self.assertLess(
            next(i for i, statement in enumerate(sql) if "pg_advisory_xact_lock" in statement),
            next(i for i, statement in enumerate(sql) if "FROM archive_items" in statement),
        )
        self.assertTrue(any("FROM kb_snapshots" in statement and "FOR UPDATE" in statement for statement in sql))

    def test_combined_archive_delete_without_mutation_keeps_revision(self):
        connection = FakeConnection()
        item = seed_archive_delete_state(connection)
        store = PostgresStore(connection_factory=lambda: connection)

        result = store.delete_archive_item_with_kb_mutation(
            "u1",
            "archive-1",
            operation_id="delete-only",
            base_revision=1,
        )

        self.assertEqual(result["item"], item)
        self.assertEqual(result["revision"], 1)
        self.assertEqual(result["graph"], ARCHIVE_DELETE_GRAPH)
        self.assertIsNone(result["mutationSchemaVersion"])
        self.assertIsNone(result["mutation"])
        self.assertEqual(connection.kb_snapshot_revisions["u1"], 1)
        self.assertEqual(len(connection.kb_changes["u1"]), 1)
        self.assertTrue(
            any(
                statement.startswith("SELECT revision, graph, mutation, created_at FROM kb_changes")
                for statement, _ in connection.executed
            ),
            "combined delete must detect a previously recorded operation before touching the archive",
        )

    def test_combined_archive_delete_revision_conflict_rolls_back(self):
        connection = FakeConnection()
        item = seed_archive_delete_state(connection)
        store = PostgresStore(connection_factory=lambda: connection)

        with self.assertRaises(KnowledgeRevisionConflict):
            store.delete_archive_item_with_kb_mutation(
                "u1",
                "archive-1",
                operation_id="stale-delete",
                base_revision=0,
                mutation=archive_delete_mutation(),
            )

        self.assertEqual(connection.archive_items["u1"], [item])
        self.assertEqual(connection.kb_snapshot_revisions["u1"], 1)
        self.assertEqual(len(connection.kb_changes["u1"]), 1)
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)
        self.assertEqual(connection.closes, 1)

    def test_combined_archive_delete_rejects_sealed_time_letter(self):
        connection = FakeConnection()
        item = seed_archive_delete_state(
            connection,
            item={
                "id": "archive-1",
                "userId": "u1",
                "kind": "timeLetter",
                "metadata": {"deliveryState": "sealed"},
            },
        )
        store = PostgresStore(connection_factory=lambda: connection)

        with self.assertRaisesRegex(
            ArchiveItemDeletionForbidden,
            "sealed timeLetter cannot be deleted",
        ):
            store.delete_archive_item_with_kb_mutation(
                "u1",
                "archive-1",
                operation_id="sealed-delete",
                base_revision=1,
            )

        self.assertEqual(connection.archive_items["u1"], [item])
        self.assertEqual(connection.rollbacks, 1)

    def test_combined_archive_delete_raises_for_missing_item(self):
        connection = FakeConnection()
        seed_archive_delete_state(connection)
        connection.archive_items["u1"] = []
        store = PostgresStore(connection_factory=lambda: connection)

        with self.assertRaisesRegex(ArchiveItemNotFound, "archive item not found"):
            store.delete_archive_item_with_kb_mutation(
                "u1",
                "missing",
                operation_id="missing-delete",
                base_revision=1,
            )

        self.assertEqual(connection.rollbacks, 1)

    def test_combined_archive_delete_duplicate_returns_stored_mutation(self):
        connection = FakeConnection()
        seed_archive_delete_state(connection)
        store = PostgresStore(connection_factory=lambda: connection)
        first = store.delete_archive_item_with_kb_mutation(
            "u1",
            "archive-1",
            operation_id="duplicate-delete",
            base_revision=1,
            mutation=archive_delete_mutation(),
        )

        repeated = store.delete_archive_item_with_kb_mutation(
            "u1",
            "archive-1",
            operation_id="duplicate-delete",
            base_revision=0,
            mutation={"upserts": {}, "tombstones": []},
        )

        self.assertTrue(repeated["duplicate"])
        self.assertIsNone(repeated["item"])
        self.assertEqual(repeated["revision"], first["revision"])
        self.assertEqual(repeated["graph"], first["graph"])
        self.assertEqual(repeated["mutation"], first["mutation"])
        self.assertEqual(connection.kb_snapshot_revisions["u1"], 2)
        self.assertEqual(len(connection.kb_changes["u1"]), 2)
        self.assertEqual(connection.commits, 2)

    def test_combined_archive_delete_sql_failure_rolls_back_all_changes(self):
        connection = TransactionalFailingConnection()
        item = seed_archive_delete_state(connection)
        connection._capture_committed_state()
        connection.fail_archive_delete = True
        store = PostgresStore(connection_factory=lambda: connection)

        with self.assertRaisesRegex(RuntimeError, "archive delete failed"):
            store.delete_archive_item_with_kb_mutation(
                "u1",
                "archive-1",
                operation_id="failed-delete",
                base_revision=1,
                mutation=archive_delete_mutation(),
            )

        self.assertEqual(connection.archive_items["u1"], [item])
        self.assertEqual(connection.kb_snapshot_revisions["u1"], 1)
        self.assertEqual(connection.kb_snapshots["u1"], ARCHIVE_DELETE_GRAPH)
        self.assertEqual(len(connection.kb_changes["u1"]), 1)
        self.assertEqual(connection.kb_operation_receipts.get("u1", {}), {})
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)
        self.assertEqual(connection.closes, 1)

    def test_store_rejects_archive_id_reuse_by_another_owner(self):
        connection = FakeConnection()
        store = PostgresStore(connection_factory=lambda: connection)
        original = store.add_archive_item(
            "u1",
            {"id": "shared-archive-id", "title": "u1 source"},
        )

        with self.assertRaisesRegex(ArchiveItemOwnershipConflict, "another owner"):
            store.add_archive_item(
                "u2",
                {"id": "shared-archive-id", "title": "u2 takeover"},
            )

        self.assertEqual(store.list_archive_items("u1"), [original])
        self.assertEqual(store.list_archive_items("u2"), [])
        self.assertGreaterEqual(connection.commits, 2)

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

    def test_store_does_not_dispatch_time_letter_when_concurrent_worker_already_delivered_it(self):
        connection = FakeConnection()
        store = PostgresStore(connection_factory=lambda: connection)

        store.add_archive_item(
            "u1",
            {
                "id": "time-letter-race",
                "kind": "timeLetter",
                "deliveryState": "sealed",
                "deliveryStatus": "scheduled",
                "openAt": "2026-07-02T08:00:00Z",
                "metadata": {
                    "deliveryState": "sealed",
                    "deliveryStatus": "scheduled",
                    "deliveryExecutionState": "scheduled",
                    "openAt": "2026-07-02T08:00:00Z",
                },
            },
        )
        connection.deliver_archive_item_before_next_update = True

        dispatched = store.mark_due_time_letters_delivered(
            cutoff_iso="2026-07-02T09:00:00Z",
            delivered_at_iso="2026-07-02T09:00:00Z",
            limit=10,
        )

        self.assertEqual(dispatched, [])
        listed = {item["id"]: item for item in store.list_archive_items("u1")}
        self.assertEqual(listed["time-letter-race"]["deliveryStatus"], "delivered")

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

        with self.assertRaises(ValueError):
            store.save_voice_profile(
                "u2",
                {
                    "voiceProfileId": "voice_profile_1",
                    "sampleStatus": "pending",
                    "authorizationConfirmed": True,
                },
            )

        self.assertEqual(profile["voiceProfileId"], "voice_profile_1")
        self.assertEqual(disabled["sampleStatus"], "disabled")
        self.assertEqual(deleted["sampleStatus"], "deleted")
        self.assertEqual(deleted["deletionState"], "deleted")
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["sampleStatus"], "deleted")
        self.assertEqual(fetched["voiceProfileId"], "voice_profile_1")

    def test_in_memory_store_allocates_voice_clone_slots_exclusively(self):
        store = InMemoryStore()
        provider_ids = ["S_slot_001", "S_slot_002"]

        first = store.allocate_voice_clone_slot(
            provider_ids,
            user_id="u1",
            voice_profile_id="vp_1",
            persona_scope="personal",
            digital_human_id="u1",
        )
        repeated = store.allocate_voice_clone_slot(
            provider_ids,
            user_id="u1",
            voice_profile_id="vp_1",
            persona_scope="personal",
            digital_human_id="u1",
        )
        second = store.allocate_voice_clone_slot(
            provider_ids,
            user_id="u2",
            voice_profile_id="vp_2",
            persona_scope="family",
            digital_human_id="family_2",
        )
        exhausted = store.allocate_voice_clone_slot(
            provider_ids,
            user_id="u3",
            voice_profile_id="vp_3",
            persona_scope="personal",
            digital_human_id="u3",
        )

        self.assertEqual(first["providerSpeakerId"], repeated["providerSpeakerId"])
        self.assertNotEqual(first["providerSpeakerId"], second["providerSpeakerId"])
        self.assertIsNone(exhausted)

        retired = store.update_voice_clone_slot("vp_1", status="retired")
        still_exhausted = store.allocate_voice_clone_slot(
            provider_ids,
            user_id="u3",
            voice_profile_id="vp_3",
            persona_scope="personal",
            digital_human_id="u3",
        )
        self.assertEqual(retired["status"], "retired")
        self.assertIsNone(still_exhausted)

    def test_postgres_store_allocates_voice_clone_slots_with_locking_contract(self):
        connection = FakeConnection()
        store = PostgresStore(connection_factory=lambda: connection)
        provider_ids = ["S_slot_001", "S_slot_002"]

        first = store.allocate_voice_clone_slot(
            provider_ids,
            user_id="u1",
            voice_profile_id="vp_1",
            persona_scope="personal",
            digital_human_id="u1",
        )
        repeated = store.allocate_voice_clone_slot(
            provider_ids,
            user_id="u1",
            voice_profile_id="vp_1",
            persona_scope="personal",
            digital_human_id="u1",
        )
        second = store.allocate_voice_clone_slot(
            provider_ids,
            user_id="u2",
            voice_profile_id="vp_2",
            persona_scope="family",
            digital_human_id="family_2",
        )
        exhausted = store.allocate_voice_clone_slot(
            provider_ids,
            user_id="u3",
            voice_profile_id="vp_3",
            persona_scope="personal",
            digital_human_id="u3",
        )

        self.assertEqual(first["providerSpeakerId"], repeated["providerSpeakerId"])
        self.assertNotEqual(first["providerSpeakerId"], second["providerSpeakerId"])
        self.assertIsNone(exhausted)
        allocation_sql = "\n".join(sql for sql, _ in connection.executed if sql.startswith("WITH candidate AS"))
        self.assertIn("FOR UPDATE SKIP LOCKED", allocation_sql)
        self.assertIn("voice_profile_id = %s AND user_id = %s", allocation_sql)

        training = store.update_voice_clone_slot("vp_1", status="training", increment_training_attempts=True)
        retired = store.update_voice_clone_slot("vp_1", status="retired")
        self.assertEqual(training["trainingAttempts"], 1)
        self.assertEqual(retired["status"], "retired")

    def test_account_purge_retires_voice_clone_slots_without_recycling_them(self):
        postgres_connection = FakeConnection()
        stores = [
            InMemoryStore(),
            PostgresStore(connection_factory=lambda: postgres_connection),
        ]

        for store in stores:
            with self.subTest(store=type(store).__name__):
                user = store.upsert_user("13800138000", "测试用户")
                store.allocate_voice_clone_slot(
                    ["S_slot_001"],
                    user_id=user["id"],
                    voice_profile_id="vp_purged",
                    persona_scope="personal",
                    digital_human_id=user["id"],
                )
                store.soft_delete_user(
                    user["id"],
                    phone="13800138000",
                    requested_at_iso="2026-01-01T00:00:00+00:00",
                )

                purged = store.purge_expired_deleted_users("2026-02-01T00:00:01+00:00")
                slot = store.get_voice_clone_slot("vp_purged")
                replacement = store.allocate_voice_clone_slot(
                    ["S_slot_001"],
                    user_id="replacement",
                    voice_profile_id="vp_replacement",
                    persona_scope="personal",
                    digital_human_id="replacement",
                )

                self.assertEqual(len(purged), 1)
                self.assertEqual(slot["status"], "retired")
                self.assertIsNone(replacement)

    def test_postgres_account_purge_removes_operation_receipts(self):
        connection = FakeConnection()
        connection.users["u1"] = {
            "id": "u1",
            "deletionState": "softDeleted",
            "restoreDeadline": "2026-01-01T00:00:00+00:00",
        }
        connection.kb_operation_receipts["u1"] = {
            "purge-op": {
                "operation_kind": "kb.sync",
                "schema_version": 1,
                "payload_hash": "fingerprint",
                "result": {},
            }
        }
        store = PostgresStore(connection_factory=lambda: connection)

        purged = store.purge_expired_deleted_users("2026-01-02T00:00:00+00:00")

        self.assertEqual(len(purged), 1)
        self.assertNotIn("u1", connection.kb_operation_receipts)
        self.assertTrue(
            any(
                sql.startswith("DELETE FROM kb_operation_receipts")
                for sql, _ in connection.executed
            )
        )
        sql = [statement for statement, _ in connection.executed]
        user_lock_index = next(
            index
            for index, statement in enumerate(sql)
            if "pg_advisory_xact_lock" in statement
        )
        change_delete_index = next(
            index
            for index, statement in enumerate(sql)
            if statement.startswith("DELETE FROM kb_changes")
        )
        self.assertLess(user_lock_index, change_delete_index)
        payloadless_purge_tables = {
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
        }
        account_table_deletes = [
            statement
            for statement, _ in connection.executed
            if any(
                statement.startswith(f"DELETE FROM {table} ")
                for table in payloadless_purge_tables
            )
        ]
        self.assertEqual(len(account_table_deletes), len(payloadless_purge_tables))
        self.assertTrue(
            all("RETURNING payload" not in statement for statement in account_table_deletes)
        )

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
