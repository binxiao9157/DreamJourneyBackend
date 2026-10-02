"""Versioned, durable Live recovery coordinates. This module stores no transcript.

The existing conversation ledger remains the source of truth for message bodies.
Only explicitly enrolled sessions may use this protocol; an immutable snapshot
is a received-range statement, never a successful legacy end/ACK receipt.
"""
from __future__ import annotations
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from typing import Any, Mapping, Sequence
from uuid import UUID, uuid5

PROTOCOL = "live-recovery-v1"
_NAMESPACE = UUID("e02ff043-a5f0-4c6e-9de9-c89a6e5ea0aa")

class LiveRecoveryConflict(ValueError):
    pass

@dataclass(frozen=True)
class RecoveryPolicy:
    heartbeat_seconds: int = 20
    inactivity_seconds: int = 90
    disconnect_seconds: int = 60
    repair_seconds: int = 60
    draft_seconds: int = 60
    message_attempts: int = 3
    maximum_sequence: int = 10_000

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in asdict(self).values()):
            raise LiveRecoveryConflict("invalidRecoveryPolicy")


def _date(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise LiveRecoveryConflict("timezoneRequired")
    return result


def _hash(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def ranges(values: Sequence[int]) -> list[list[int]]:
    result: list[list[int]] = []
    for value in sorted(set(values)):
        if result and result[-1][1] + 1 == value:
            result[-1][1] = value
        else:
            result.append([value, value])
    return result


def enroll(*, session_id: str, owner_id: str, vault_id: str, generation: int,
           authority_epoch: int, now: datetime, policy: RecoveryPolicy | None = None) -> dict:
    policy = policy or RecoveryPolicy()
    if not session_id or not owner_id or not vault_id or type(generation) is not int or generation < 1:
        raise LiveRecoveryConflict("invalidRecoveryIdentity")
    if type(authority_epoch) is not int or authority_epoch < 0 or now.tzinfo is None:
        raise LiveRecoveryConflict("invalidRecoveryAuthority")
    return dict(protocol=PROTOCOL, sessionId=session_id, ownerId=owner_id, vaultId=vault_id,
                generation=generation, authorityEpoch=authority_epoch, policy=asdict(policy),
                state="collecting", version=1, lastActivityAt=now.isoformat(),
                draftDeadline=(now+timedelta(seconds=policy.draft_seconds)).isoformat(),
                leaseDeadline=(now+timedelta(seconds=policy.inactivity_seconds)).isoformat(),
                closeRequestedAt=None, finalSequence=None, repairDeadline=None,
                messages={}, snapshots=[], pendingSnapshot=False)


def progress(record: Mapping[str, Any], *, range_offset: int = 0, expected_version: int | None = None) -> dict:
    if type(range_offset) is not int or range_offset < 0 or range_offset > 10_000 or range_offset % 256:
        raise LiveRecoveryConflict("invalidRangeOffset")
    if expected_version is not None and (type(expected_version) is not int or expected_version != record["version"]):
        raise LiveRecoveryConflict("progressVersionChanged")
    if range_offset and expected_version is None:
        raise LiveRecoveryConflict("continuationVersionRequired")
    sequences = sorted(int(key) for key in record["messages"])
    received = ranges(sequences)
    contiguous = received[0][1] if received and received[0][0] == 1 else 0
    final = record["finalSequence"]
    upper = final if final is not None else max(sequences, default=0)
    missing = ranges([index for index in range(1, upper+1) if str(index) not in record["messages"]])
    # Range pages are bounded. Clients use the continuation offset when needed.
    return dict(protocol=PROTOCOL, sessionId=record["sessionId"], generation=record["generation"],
                version=record["version"], state=record["state"], continuousSequence=contiguous,
                highestSeenSequence=max(sequences, default=0), finalSequence=final,
                endPositionKnown=final is not None, receivedRanges=received[range_offset:range_offset+256],
                missingRanges=missing[range_offset:range_offset+256],
                rangeOffset=range_offset,
                nextRangeOffset=(range_offset+256 if max(len(received),len(missing))>range_offset+256 else None),
                rangePageTruncated=max(len(received),len(missing))>range_offset+256,
                snapshotRevision=len(record["snapshots"]), repairDeadline=record["repairDeadline"],
                observationBudget=dict(serverTime=datetime.now(timezone.utc).isoformat(),
                    leaseDeadline=record["leaseDeadline"], repairSeconds=record["policy"]["repair_seconds"],
                    heartbeatSeconds=record["policy"]["heartbeat_seconds"],
                    disconnectSeconds=record["policy"]["disconnect_seconds"]))


def transition(record: Mapping[str, Any], *, action: str, generation: int, now: datetime,
               payload: Mapping[str, Any] | None = None) -> dict:
    """Pure transition used under a PG row lock. All deadlines survive restarts."""
    result = deepcopy(dict(record)); payload = dict(payload or {})
    if result["protocol"] != PROTOCOL or type(generation) is not int or generation != result["generation"]:
        raise LiveRecoveryConflict("recoveryGenerationMismatch")
    if now.tzinfo is None:
        raise LiveRecoveryConflict("timezoneRequired")
    policy = RecoveryPolicy(**result["policy"])
    final = result["finalSequence"]
    if action == "heartbeat":
        if result["state"] not in {"collecting", "disconnected"}:
            raise LiveRecoveryConflict("closedGenerationCannotRenew")
        # A heartbeat arriving after its lease cannot resurrect the old Live.
        if now >= _date(result["leaseDeadline"]):
            raise LiveRecoveryConflict("activityLeaseExpired")
        result["state"] = "collecting"
        result["lastActivityAt"] = now.isoformat()
        result["leaseDeadline"] = (now+timedelta(seconds=policy.inactivity_seconds)).isoformat()
    elif action == "disconnect":
        if result["state"] == "collecting":
            result["state"] = "disconnected"
            result["leaseDeadline"] = min(_date(result["leaseDeadline"]), now+timedelta(seconds=policy.disconnect_seconds)).isoformat()
    elif action == "close":
        requested = payload.get("finalSequence")
        if requested is not None and (type(requested) is not int or not 0 <= requested <= policy.maximum_sequence):
            raise LiveRecoveryConflict("invalidFinalSequence")
        if final is not None and requested is not None and final != requested:
            raise LiveRecoveryConflict("immutableFinalSequence")
        if requested is not None and requested < max((int(k) for k in result["messages"]), default=0):
            raise LiveRecoveryConflict("finalSequenceBeforeReceivedBody")
        if result["closeRequestedAt"] is None:
            result["closeRequestedAt"] = now.isoformat()
            result["repairDeadline"] = (now+timedelta(seconds=policy.repair_seconds)).isoformat()
        if requested is not None and final is None:
            result["finalSequence"] = requested
            # Metadata-only completion also gets a new immutable snapshot.
            if result["snapshots"]:
                result["pendingSnapshot"] = True
        if result["state"] in {"collecting", "disconnected"}:
            result["state"] = "repairing"
    elif action == "receipt":
        seq = payload.get("sequence")
        if type(seq) is not int or not 1 <= seq <= policy.maximum_sequence:
            raise LiveRecoveryConflict("invalidClientSequence")
        message = {key: payload.get(key) for key in ("messageId", "commandId", "contentHash", "role")}
        if any(not isinstance(value, str) or not value for value in message.values()) or message["role"] not in {"owner", "assistant"}:
            raise LiveRecoveryConflict("invalidMessageIdentity")
        if len(message["contentHash"]) != 64 or any(c not in "0123456789abcdef" for c in message["contentHash"]):
            raise LiveRecoveryConflict("invalidContentHash")
        old = result["messages"].get(str(seq))
        if old is not None:
            if old != message:
                raise LiveRecoveryConflict("immutableMessageConflict")
            return result  # Replayed ACK is not new activity and does not renew deadlines.
        if any(v["messageId"] == message["messageId"] or v["commandId"] == message["commandId"] for v in result["messages"].values()):
            raise LiveRecoveryConflict("messageSequenceConflict")
        if final is not None and seq > final:
            raise LiveRecoveryConflict("messageOutsideFinalBoundary")
        result["messages"][str(seq)] = message
        if result["snapshots"]:
            result["pendingSnapshot"] = True
        elif result["state"] == "collecting" and now < _date(result["leaseDeadline"]):
            result["lastActivityAt"] = now.isoformat()
            result["leaseDeadline"] = (now+timedelta(seconds=policy.inactivity_seconds)).isoformat()
    elif action == "tick":
        if result["state"] in {"collecting", "disconnected"} and now >= _date(result["leaseDeadline"]):
            result["state"] = "repairing"
            result["closeRequestedAt"] = result["leaseDeadline"]
            # Anchored to persisted lease, not the time the worker restarted.
            result["repairDeadline"] = (_date(result["leaseDeadline"])+timedelta(seconds=policy.repair_seconds)).isoformat()
        p = progress(result)
        ready = result["state"] == "repairing" and (
            (p["endPositionKnown"] and not p["missingRanges"])
            or now >= _date(result["repairDeadline"]))
        if ready or (result["state"] == "frozen" and result["pendingSnapshot"]):
            revision = len(result["snapshots"])+1
            snapshot = dict(revision=revision, endPositionKnown=p["endPositionKnown"],
                            finalSequence=p["finalSequence"], receivedRanges=ranges([int(k) for k in result["messages"]]),
                            missingRanges=ranges([i for i in range(1, (p["finalSequence"] or p["highestSeenSequence"])+1) if str(i) not in result["messages"]]),
                            messages=deepcopy(result["messages"]), frozenAt=now.isoformat())
            snapshot["hash"] = _hash(snapshot)
            snapshot["snapshotId"] = str(uuid5(_NAMESPACE, f'{result["vaultId"]}:{result["sessionId"]}:{generation}:{revision}:{snapshot["hash"]}'))
            result["snapshots"].append(snapshot)
            result["state"] = "frozen"
            result["pendingSnapshot"] = False
    else:
        raise LiveRecoveryConflict("unsupportedRecoveryAction")
    if result != record:
        result["version"] += 1
    return result

class PostgresLiveRecoveryRepository:
    """Caller owns the transaction; message + receipt + coordinates commit together."""
    def __init__(self, connection):
        self.connection = connection

    def _cursor(self):
        from psycopg.rows import dict_row
        return self.connection.cursor(row_factory=dict_row)

    @staticmethod
    def _assert_owner(context):
        if context.actor_subject_id != context.owner_subject_id:
            raise LiveRecoveryConflict('recoveryOwnerRequired')

    def enroll(self, *, session_id, context, authority_epoch, generation=1, now=None):
        from psycopg.types.json import Jsonb
        self._assert_owner(context)
        now = now or datetime.now(timezone.utc)
        record = enroll(session_id=session_id, owner_id=context.owner_subject_id,
                        vault_id=context.vault_id, generation=generation,
                        authority_epoch=authority_epoch, now=now)
        with self._cursor() as cur:
            cur.execute("""SELECT 1 FROM owner_truth.interview_sessions s
                JOIN owner_truth.vaults v ON v.vault_id=s.vault_id
                JOIN owner_truth.conversation_threads t ON t.id=s.current_thread_id
                WHERE s.id=%s AND s.vault_id=%s AND s.owner_subject_id=%s
                  AND s.authority_epoch=%s AND v.authority_epoch=s.authority_epoch
                  AND v.owner_subject_id=s.owner_subject_id AND v.status='active'
                  AND t.entry_mode='live' FOR SHARE OF s,v,t""",
                (session_id,context.vault_id,context.owner_subject_id,authority_epoch))
            if cur.fetchone() is None:
                raise LiveRecoveryConflict('recoveryIdentityOrAuthorityMismatch')
            cur.execute("""INSERT INTO owner_truth.live_recovery_sessions
                (session_id,vault_id,owner_subject_id,authority_epoch,protocol,coordinates,next_scan_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(session_id) DO NOTHING""",
                (session_id, context.vault_id, context.owner_subject_id, authority_epoch,
                 PROTOCOL, Jsonb(record), now+timedelta(seconds=record['policy']['draft_seconds'])))
            existing = self._read(cur, session_id, context, authority_epoch)
            if existing['generation'] != generation:
                raise LiveRecoveryConflict('recoveryGenerationMismatch')
            return progress(existing)

    def _read(self, cur, session_id, context, authority_epoch):
        self._assert_owner(context)
        cur.execute("""SELECT r.* FROM owner_truth.live_recovery_sessions r
            JOIN owner_truth.vaults v ON v.vault_id=r.vault_id
            WHERE r.session_id=%s AND r.vault_id=%s AND r.owner_subject_id=%s
              AND v.owner_subject_id=r.owner_subject_id AND v.authority_epoch=r.authority_epoch AND v.status='active'
            FOR UPDATE OF r""", (session_id, context.vault_id, context.owner_subject_id))
        row = cur.fetchone()
        if row is None or int(row['authority_epoch']) != authority_epoch:
            raise LiveRecoveryConflict('recoveryIdentityOrAuthorityMismatch')
        return row['coordinates']

    def read(self, *, session_id, context, authority_epoch, range_offset=0, expected_version=None):
        with self._cursor() as cur:
            result=progress(self._read(cur, session_id, context, authority_epoch),
                            range_offset=range_offset, expected_version=expected_version)
            return self._with_publication(cur,session_id,result)

    def contains(self, *, session_id):
        with self._cursor() as cur:
            cur.execute('SELECT 1 FROM owner_truth.live_recovery_sessions WHERE session_id=%s', (session_id,))
            return cur.fetchone() is not None

    def apply(self, *, session_id, context, authority_epoch, generation, action, payload=None, now=None):
        now = now or datetime.now(timezone.utc)
        with self._cursor() as cur:
            record = self._read(cur, session_id, context, authority_epoch)
            updated = transition(record, action=action, generation=generation, now=now, payload=payload)
            self._persist(cur, updated, now)
            return self._with_publication(cur,session_id,progress(updated))

    @staticmethod
    def _with_publication(cur,session_id,result):
        cur.execute("""SELECT revision,state,publication_manifest FROM owner_truth.live_recovery_snapshots
            WHERE session_id=%s ORDER BY revision DESC LIMIT 1""",(session_id,))
        row=cur.fetchone()
        if row is not None:
            result['publication']={'snapshotRevision':int(row['revision']),'state':row['state'],
                'themeCount':len((row['publication_manifest'] or {}).get('themes',[])),
                'isPartial':(row['publication_manifest'] or {}).get('completeness')=='partial'}
        cur.execute("""SELECT
                COUNT(*) FILTER(WHERE u.state IN ('planned','running')) AS pending,
                COUNT(*) FILTER(WHERE u.state='completed') AS completed,
                COALESCE(MAX(EXTRACT(EPOCH FROM NOW()-u.created_at)) FILTER(WHERE u.state IN ('planned','running')),0) AS age,
                COALESCE(BOOL_OR(r.provider_request_count >= (r.budget_policy_snapshot->>'maximum_provider_requests')::integer
                    OR r.reserved_input_tokens >= (r.budget_policy_snapshot->>'maximum_reserved_input_tokens')::bigint
                    OR r.reserved_output_tokens >= (r.budget_policy_snapshot->>'maximum_reserved_output_tokens')::bigint),false) AS exhausted
            FROM owner_truth.interview_sessions i
            JOIN owner_truth.live_recovery_sessions s ON s.session_id=i.id
            JOIN owner_truth.live_memory_runs r ON r.product_session_id=i.product_session_id
                AND r.owner_subject_id=i.owner_subject_id AND r.vault_id=i.vault_id
                AND r.authority_epoch=s.authority_epoch AND r.capture_generation=(s.coordinates->>'generation')::integer
            LEFT JOIN owner_truth.live_memory_work_units u ON u.run_id=r.id AND u.kind='atomExtraction'
            WHERE i.id=%s""",(session_id,))
        backlog=cur.fetchone()
        result['backlog']=dict(pendingUnits=int(backlog['pending']),completedUnits=int(backlog['completed']),
            oldestAgeSeconds=max(0,int(backlog['age'])),budgetExhausted=bool(backlog['exhausted']))
        # Read-only observation metadata from this run's frozen policy, never
        # current defaults. It cannot extend any product deadline or reservation.
        cur.execute("""SELECT r.budget_policy_snapshot,r.budget_policy_hash,r.provider_request_count,
                r.organization_started_at,r.last_progress_at
            FROM owner_truth.interview_sessions i
            JOIN owner_truth.live_recovery_sessions s ON s.session_id=i.id
            JOIN owner_truth.live_memory_runs r ON r.product_session_id=i.product_session_id
                AND r.owner_subject_id=i.owner_subject_id AND r.vault_id=i.vault_id
                AND r.authority_epoch=s.authority_epoch AND r.capture_generation=(s.coordinates->>'generation')::integer
            WHERE i.id=%s ORDER BY r.created_at DESC LIMIT 1""",(session_id,))
        budget_run=cur.fetchone()
        if budget_run is not None:
            from app.services.owner_truth_live_long_memory import LiveLongMemoryBudgetPolicy
            policy=LiveLongMemoryBudgetPolicy.from_snapshot(budget_run['budget_policy_snapshot'],budget_run['budget_policy_hash'])
            now=datetime.now(timezone.utc)
            age=max(0,(now-budget_run['organization_started_at']).total_seconds()) if budget_run['organization_started_at'] else 0
            idle=max(0,(now-budget_run['last_progress_at']).total_seconds()) if budget_run['last_progress_at'] else 0
            remaining=max(0,policy.maximum_provider_requests-int(budget_run['provider_request_count']))
            result['observationBudget'].update(requestDeadlineSeconds=policy.request_deadline_seconds,
                unitExtraRequests=policy.maximum_unit_extra_requests,remainingProviderRequests=remaining,
                organizationAbsoluteRemainingSeconds=max(0,policy.absolute_deadline_seconds-age),
                organizationInactivityRemainingSeconds=max(0,policy.inactivity_deadline_seconds-idle),
                policyHash=str(budget_run['budget_policy_hash']))
        return result

    def record_planner_failure(self, *, session_id, context, authority_epoch, code):
        if code not in {'LiveLongMemoryError','LiveLongMemoryConflict','LiveLongMemoryBudgetExhausted','LiveLongMemoryManifestIncomplete'}:
            raise LiveRecoveryConflict('invalidPlannerFailureCode')
        now=datetime.now(timezone.utc)
        with self._cursor() as cur:
            record=self._read(cur,session_id,context,authority_epoch)
            if 'firstPlannerFailure' not in record:
                record['firstPlannerFailure']={'code':code,'at':now.isoformat()}
                record['version']+=1
                self._persist(cur,record,now)

    def authorize_publication(self, *, session_id, context, authority_epoch):
        """Separate Candidate permission; natural-input consent is insufficient."""
        from psycopg.types.json import Jsonb
        capture = context.authorization_capture
        if capture is None or capture.feature != 'ownerTruthCandidateReview':
            raise LiveRecoveryConflict('candidatePublicationAuthorizationRequired')
        now = datetime.now(timezone.utc)
        if _date(capture.expires_at) <= now:
            raise LiveRecoveryConflict('candidatePublicationAuthorizationExpired')
        with self._cursor() as cur:
            record = self._read(cur, session_id, context, authority_epoch)
            cur.execute("""UPDATE owner_truth.live_recovery_sessions
                SET publication_authorization=%s,publication_authorized_at=%s
                WHERE session_id=%s""", (Jsonb(capture.value_minimized_payload()),now,session_id))
            return {**progress(record), 'publicationAuthorized':True}

    def prepare_snapshot_source(self, *, snapshot_id, context, authority_epoch):
        """Build an immutable Source command from ledger rows, never request text.

        The caller creates the Source and binds its id within this same UoW.
        This does not enqueue the legacy extraction worker or publish candidates.
        """
        from app.domain.owner_truth.source_commands import CreateTextSourceCommand
        from app.domain.owner_truth.contracts import SourceKind
        self._assert_owner(context)
        with self._cursor() as cur:
            cur.execute('SELECT session_id FROM owner_truth.live_recovery_snapshots WHERE id=%s',(snapshot_id,))
            target=cur.fetchone()
            if target is None:
                raise LiveRecoveryConflict('snapshotUnavailable')
            record=self._read(cur,str(target['session_id']),context,authority_epoch)
            cur.execute("""SELECT p.*,r.publication_authorization,s.product_session_id
                FROM owner_truth.live_recovery_snapshots p
                JOIN owner_truth.live_recovery_sessions r ON r.session_id=p.session_id
                JOIN owner_truth.interview_sessions s ON s.id=p.session_id
                WHERE p.id=%s FOR UPDATE OF p""",(snapshot_id,))
            row=cur.fetchone()
            authorization=row['publication_authorization']
            if not authorization or authorization.get('feature')!='ownerTruthCandidateReview':
                raise LiveRecoveryConflict('candidatePublicationAuthorizationRequired')
            snap=row['snapshot']; turns=[]
            cur.execute("""SELECT id,author,content_payload,client_sequence_number
                FROM owner_truth.conversation_messages
                WHERE id=ANY(%s::uuid[]) AND session_id=%s AND vault_id=%s
                  AND owner_subject_id=%s AND authority_epoch=%s""",
                ([item['messageId'] for item in snap['messages'].values()],record['sessionId'],
                 context.vault_id,context.owner_subject_id,authority_epoch))
            messages={str(item['id']):item for item in cur.fetchall()}
            for key,identity in sorted(snap['messages'].items(),key=lambda pair:int(pair[0])):
                message=messages.get(identity['messageId'])
                if (message is None or int(message['client_sequence_number'])!=int(key)
                    or str(message['author'])!=identity['role']
                    or sha256(message['content_payload']['text'].encode()).hexdigest()!=identity['contentHash']):
                    raise LiveRecoveryConflict('snapshotMessageIdentityMismatch')
                turns.append(dict(index=int(key),messageId=str(message['id']),
                    role='user' if identity['role']=='owner' else 'assistant',
                    text=message['content_payload']['text'],captureMode='live',contentHash=identity['contentHash']))
            owner_text='\n'.join(turn['text'] for turn in turns if turn['role']=='user')
            if not owner_text.strip():
                raise LiveRecoveryConflict('snapshotHasNoOwnerText')
            source_id=str(uuid5(_NAMESPACE,'source:'+str(snapshot_id)))
            metadata=dict(origin='liveRecoverySnapshot',recoveryProtocol=PROTOCOL,
                captureMode='live',sourcePolicy='userEvidenceOnly',sessionId=record['sessionId'],
                productSessionId=str(row['product_session_id'] or record['sessionId']),
                productCaptureGeneration=record['generation'],snapshotId=str(snapshot_id),
                snapshotRevision=snap['revision'],snapshotHash=snap['hash'],
                endPositionKnown=snap['endPositionKnown'],finalSequence=snap['finalSequence'],
                receivedRanges=snap['receivedRanges'],missingRanges=snap['missingRanges'],
                conversationTurns=turns,
                completeness=('receivedComplete' if snap['endPositionKnown'] and not snap['missingRanges'] else 'partial'))
            return CreateTextSourceCommand(command_id='live-recovery-source:'+str(snapshot_id),
                source_id=source_id,expected_version=0,text=owner_text,metadata=metadata,
                source_kind=SourceKind.CONVERSATION,expected_authority_epoch=authority_epoch,
                trusted_live_capacity=True)

    def bind_snapshot_source(self, *, snapshot_id, source_id, context, authority_epoch):
        self._assert_owner(context)
        with self._cursor() as cur:
            cur.execute('SELECT session_id FROM owner_truth.live_recovery_snapshots WHERE id=%s',(snapshot_id,))
            target=cur.fetchone()
            if target is None: raise LiveRecoveryConflict('snapshotUnavailable')
            self._read(cur,str(target['session_id']),context,authority_epoch)
            cur.execute("""SELECT id FROM owner_truth.sources
                WHERE id=%s AND vault_id=%s AND owner_subject_id=%s AND authority_epoch=%s
                  AND state='active' AND metadata->>'snapshotId'=%s FOR SHARE""",
                (source_id,context.vault_id,context.owner_subject_id,authority_epoch,str(snapshot_id)))
            if cur.fetchone() is None: raise LiveRecoveryConflict('snapshotSourceMismatch')
            cur.execute("""UPDATE owner_truth.live_recovery_snapshots SET source_id=%s
                WHERE id=%s AND (source_id IS NULL OR source_id=%s)""",(source_id,snapshot_id,source_id))
            if cur.rowcount!=1: raise LiveRecoveryConflict('immutableSnapshotSource')

    def _persist(self, cur, record, now):
        from psycopg.types.json import Jsonb
        refs = []
        for snap in record['snapshots']:
            if 'messages' in snap:
                cur.execute("""INSERT INTO owner_truth.live_recovery_snapshots
                    (id,session_id,revision,snapshot_hash,snapshot)
                    VALUES (%s,%s,%s,%s,%s) ON CONFLICT (session_id,revision) DO NOTHING""",
                    (snap['snapshotId'],record['sessionId'],snap['revision'],snap['hash'],Jsonb(snap)))
                cur.execute('SELECT snapshot_hash FROM owner_truth.live_recovery_snapshots WHERE session_id=%s AND revision=%s',
                            (record['sessionId'],snap['revision']))
                if cur.fetchone()['snapshot_hash'] != snap['hash']:
                    raise LiveRecoveryConflict('immutableSnapshotConflict')
            refs.append({key:snap[key] for key in ('snapshotId','revision','hash')})
        record = {**record, 'snapshots':refs}
        next_scan = _date(record['draftDeadline'])
        # A complete close or newly repaired frozen snapshot is ready now.
        # draftDeadline belongs to open-scene batching, not end-of-scene latency.
        p = progress(record)
        immediately_ready = (record['state']=='repairing' and p['endPositionKnown'] and not p['missingRanges']) or (
            record['state']=='frozen' and record['pendingSnapshot'])
        if immediately_ready:next_scan = min(next_scan, now)
        if record['state'] in {'collecting','disconnected'}:
            next_scan = min(next_scan, _date(record['leaseDeadline']))
        elif record['state'] == 'repairing':
            next_scan = min(next_scan, _date(record['repairDeadline']))
        cur.execute("""UPDATE owner_truth.live_recovery_sessions SET coordinates=%s,
            next_scan_at=%s,updated_at=%s WHERE session_id=%s""",
            (Jsonb(record),next_scan,now,record['sessionId']))

    def scan(self, *, now=None, limit=16):
        """Bounded default-worker sweep. Rechecks authority; never contacts a model."""
        from app.services.owner_truth_live_long_memory import (
            LiveLongMemoryBudgetPolicy, LiveLongMemoryRunIdentity, PostgresLiveLongMemoryRepository,
            LiveLongMemoryError,
        )
        now = now or datetime.now(timezone.utc)
        if type(limit) is not int or not 1 <= limit <= 64:
            raise LiveRecoveryConflict('invalidScanLimit')
        with self._cursor() as cur:
            cur.execute("""SELECT r.*, s.product_session_id FROM owner_truth.live_recovery_sessions r
                JOIN owner_truth.vaults v ON v.vault_id=r.vault_id
                JOIN owner_truth.interview_sessions s ON s.id=r.session_id
                WHERE r.next_scan_at <= %s AND v.authority_epoch=r.authority_epoch
                  AND v.owner_subject_id=r.owner_subject_id AND v.status='active'
                ORDER BY r.next_scan_at,r.session_id FOR UPDATE OF r SKIP LOCKED LIMIT %s""", (now,limit))
            rows = cur.fetchall()
            changes = []
            for row in rows:
                record = row['coordinates']
                updated = transition(record, action='tick', generation=record['generation'], now=now)
                updated['draftDeadline'] = (now+timedelta(seconds=record['policy']['draft_seconds'])).isoformat()
                identity = LiveLongMemoryRunIdentity(
                    owner_subject_id=row['owner_subject_id'], vault_id=row['vault_id'],
                    product_session_id=str(row['product_session_id'] or row['session_id']),
                    capture_generation=record['generation'], authority_epoch=row['authority_epoch'])
                ledger = PostgresLiveLongMemoryRepository(self.connection)
                unit = None
                # A terminal draft planner must not undo the durable close/freeze.
                # The nested transaction is a PG savepoint, not a separate commit.
                try:
                    with self.connection.transaction():
                        run = ledger.begin_or_load(identity, LiveLongMemoryBudgetPolicy())
                        if run.get('state') in {'failed', 'cancelled', 'published', 'readyToPublish'}:
                            raise LiveLongMemoryError('plannerRunTerminal')
                        unit = ledger.finalize_open_unit(run_id=identity.run_id, policy=LiveLongMemoryBudgetPolicy())
                except LiveLongMemoryError as error:
                    # Persist only a stable type, never exception text or transcript.
                    updated.setdefault('firstPlannerFailure', {
                        'code':type(error).__name__, 'at':now.isoformat()})
                if updated != record and updated['version'] == record['version']:
                    updated['version'] += 1
                self._persist(cur, updated, now)
                changes.append({'sessionId':str(row['session_id']), 'snapshotRevision':len(updated['snapshots']),
                                'plannedTail':unit is not None, 'state':updated['state'],
                                'plannerFailure':updated.get('firstPlannerFailure', {}).get('code')})
            return changes


LIVE_RECOVERY_SNAPSHOT_JOB = "ownerTruth.live.recoverySnapshot"

class LiveRecoverySnapshotScheduler:
    """Freeze Source and durable new-protocol job in the same short transaction.

    Old workers only claim their old job type. This lane cannot silently fall
    back to atomic-candidate publication when a theme-capable worker is absent.
    """
    def __init__(self, store):
        self.store = store

    def run_once(self):
        from dataclasses import replace
        from app.domain.owner_truth.source_commands import (
            OwnerTruthCommandContext, OwnerTruthCommandAuthorizationCapture)
        from app.services.owner_truth_source import build_source_created_effect_intent
        with self.store.request_unit_of_work(correlation_id='live-snapshot-schedule',command_id='live-snapshot-schedule'):
            repo=self.store.owner_truth_live_recovery_repository()
            with repo._cursor() as cur:
                # Lock recovery first, matching append/scan lock order. A session
                # with newer snapshots still has its revisions processed in order.
                cur.execute("""SELECT r.session_id,r.vault_id,r.owner_subject_id,r.authority_epoch,
                        r.publication_authorization
                    FROM owner_truth.live_recovery_sessions r
                    JOIN owner_truth.vaults v ON v.vault_id=r.vault_id
                    WHERE r.publication_authorization IS NOT NULL
                      AND v.status='active' AND v.owner_subject_id=r.owner_subject_id
                      AND v.authority_epoch=r.authority_epoch
                      AND EXISTS(SELECT 1 FROM owner_truth.live_recovery_snapshots p
                         WHERE p.session_id=r.session_id AND p.source_id IS NULL AND p.state='pending')
                    ORDER BY r.next_scan_at,r.session_id FOR UPDATE OF r SKIP LOCKED LIMIT 1""")
                row=cur.fetchone()
                if row is None:return {'status':'idle'}
                cur.execute("""SELECT id FROM owner_truth.live_recovery_snapshots
                    WHERE session_id=%s AND source_id IS NULL AND state='pending'
                    ORDER BY revision LIMIT 1 FOR UPDATE""",(row['session_id'],))
                snapshot_id=str(cur.fetchone()['id'])
                context=OwnerTruthCommandContext(vault_id=row['vault_id'],owner_subject_id=row['owner_subject_id'],
                    actor_subject_id=row['owner_subject_id'],authorization_capture=
                    OwnerTruthCommandAuthorizationCapture.from_value_minimized_payload(row['publication_authorization']))
                try:
                    command=repo.prepare_snapshot_source(snapshot_id=snapshot_id,context=context,authority_epoch=row['authority_epoch'])
                except LiveRecoveryConflict as error:
                    if str(error) != 'snapshotHasNoOwnerText':raise
                    # Empty/assistant-only input is explicit noChange, not an
                    # invented successful end or a candidate extraction failure.
                    cur.execute("""UPDATE owner_truth.live_recovery_snapshots SET state='noChange',failure_code='noOwnerText'
                        WHERE id=%s""",(snapshot_id,))
                    return {'status':'noChange','reason':'noOwnerText'}
                record=command.write_record(context=context)
                source=self.store.create_owner_truth_source(record)
                repo.bind_snapshot_source(snapshot_id=snapshot_id,source_id=source.source_id,context=context,authority_epoch=row['authority_epoch'])
                intent=replace(build_source_created_effect_intent(record=record,source=source),job_type=LIVE_RECOVERY_SNAPSHOT_JOB)
                self.store.effect_kernel_repository().accept(intent)
                # Recovery Sources are immutable revisions, not the one legacy
                # final Source binding. Start the run clock once without inventing
                # a final watermark or rebinding that legacy Source identity.
                cur.execute('''UPDATE owner_truth.live_memory_runs run SET
                    organization_started_at=COALESCE(run.organization_started_at,NOW()),
                    last_progress_at=COALESCE(run.last_progress_at,NOW()),
                    state=CASE WHEN run.state='collecting' THEN 'organizing' ELSE run.state END
                    FROM owner_truth.interview_sessions i JOIN owner_truth.live_recovery_sessions r ON r.session_id=i.id
                    WHERE i.id=%s AND run.product_session_id=i.product_session_id
                      AND run.vault_id=r.vault_id AND run.owner_subject_id=r.owner_subject_id
                      AND run.authority_epoch=r.authority_epoch
                      AND run.capture_generation=(r.coordinates->>'generation')::integer''',(row['session_id'],))
                if cur.rowcount!=1:raise LiveRecoveryConflict('recoveryRunUnavailable')
                return {'status':'scheduled','snapshotId':snapshot_id,'sourceId':source.source_id,'jobType':intent.job_type}
