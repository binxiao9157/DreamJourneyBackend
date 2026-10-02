"""Versioned, read-only Source proof. Shared by SSH and disposable local PG tests."""
import hashlib
import json
import re
import uuid


class BindingError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(ok, code):
    if not ok:
        raise BindingError(code)


def validate_request(q):
    require(isinstance(q, dict) and q.get('schema') == 2, 'bindingSchema')
    for key in ('sessionID', 'requestID'):
        try:
            require(str(uuid.UUID(q[key])) == q[key], 'bindingIdentity')
        except (KeyError, TypeError, ValueError):
            raise BindingError('bindingIdentity') from None
    require(isinstance(q.get('runID'), str) and bool(re.fullmatch(r'lab-[0-9a-f]{32}', q['runID'])), 'bindingIdentity')
    require(isinstance(q.get('productSessionID'), str) and bool(re.fullmatch(r'(echo_live_|recovery-four-)[A-Za-z0-9_-]{1,160}', q['productSessionID'])), 'bindingIdentity')
    for key in ('accountHash', 'buildIdentity'):
        require(isinstance(q.get(key), str) and bool(re.fullmatch(r'[0-9a-f]{64}', q[key])), 'bindingIdentity')
    require(bool(q.get('launchID')) and bool(q.get('deviceID')), 'bindingIdentity')
    require(type(q.get('snapshotRevision')) is int and q['snapshotRevision'] > 0, 'bindingRevision')
    rows = q.get('messages')
    require(isinstance(rows, list) and 0 < len(rows) <= 2000, 'bindingMessages')
    ids, canonical = set(), set()
    for row in rows:
        require(isinstance(row, dict), 'bindingMessageIdentity')
        try:
            mid = str(uuid.UUID(row['messageID']))
        except (KeyError, TypeError, ValueError):
            raise BindingError('bindingMessageIdentity') from None
        require(mid == row['messageID'] and mid not in ids, 'bindingDuplicateMessage')
        require(isinstance(row.get('canonicalID'), str) and 0 < len(row['canonicalID']) <= 1024
                and row['canonicalID'] not in canonical, 'bindingCanonicalIdentity')
        require(row.get('role') in ('owner', 'assistant'), 'bindingRole')
        require(isinstance(row.get('sha256'), str) and bool(re.fullmatch(r'[0-9a-f]{64}', row['sha256'])), 'bindingHash')
        ids.add(mid); canonical.add(row['canonicalID'])
    return q


def verify_rows(q, session, snap, source, messages):
    """No text is returned. Counts are derived from exact message identities, never playback."""
    validate_request(q)
    require(session is not None and snap is not None and source is not None, 'bindingMissing')
    require(str(session['id']) == q['sessionID'] and
            session['metadata'].get('productSessionId') == q['productSessionID'], 'bindingSession')
    account = hashlib.sha256((str(session['owner_subject_id'])+'|'+str(session['vault_id'])).encode()).hexdigest()
    require(account == q['accountHash'], 'bindingAccount')
    require(snap['revision'] == q['snapshotRevision'], 'bindingRevision')
    require(snap['state'] == 'published', 'bindingNotPublished')
    require(bool(re.fullmatch('[0-9a-f]{64}', snap['snapshot_hash'])), 'bindingSnapshotHash')
    snapshot = snap['snapshot']
    # Partial publication is observed but not auto-confirmed until exact partial membership
    # is exposed by a stable client contract. Never turn partial into a complete proof.
    require(snapshot.get('endPositionKnown') is True and not snapshot.get('missingRanges'), 'bindingPartialUnsupported')
    hashed = {k:v for k,v in snapshot.items() if k not in ('hash','snapshotId')}
    digest = hashlib.sha256(json.dumps(hashed,sort_keys=True,separators=(',', ':'),ensure_ascii=False).encode()).hexdigest()
    require(digest == snap['snapshot_hash'], 'bindingSnapshotHash')
    require(snapshot.get('hash') == snap['snapshot_hash'] and snapshot.get('revision') == q['snapshotRevision'], 'bindingSnapshotHash')
    require(source.get('snapshotHash') == snap['snapshot_hash'] and source.get('snapshotRevision') == q['snapshotRevision'], 'bindingSnapshotHash')
    require(source.get('sessionId') == q['sessionID'] and
            source.get('productSessionId') == q['productSessionID'], 'bindingSource')
    expected = q['messages']; turns = source.get('conversationTurns', [])
    require(len(expected) == len(messages) == len(turns) == snapshot.get('finalSequence'), 'bindingMessageCount')
    require([m['client_sequence_number'] for m in messages] == list(range(1, len(messages)+1)), 'bindingSequence')
    require(len({t['messageId'] for t in turns}) == len(turns), 'bindingDuplicateSource')
    by_id = {t['messageId']: t for t in turns}
    proof = []
    for local, message in zip(expected, messages):
        mid = str(message['id']); turn = by_id.get(mid)
        require(turn is not None and mid == local['messageID'], 'bindingMessageIdentity')
        text = message['content_payload']['text']; digest = hashlib.sha256(text.encode()).hexdigest()
        require(local['role'] == message['author'] and
                turn['role'] == ('user' if message['author'] == 'owner' else 'assistant'), 'bindingRole')
        require(turn['index'] == message['client_sequence_number'], 'bindingSequence')
        require(turn['contentHash'] == digest == local['sha256'] and turn['text'] == text, 'bindingHash')
        member = snapshot.get('messages', {}).get(str(message['client_sequence_number']))
        require(member is not None and member.get('messageId') == mid and member.get('role') == local['role']
                and member.get('contentHash') == digest, 'bindingSnapshotHash')
        proof.append(dict(local, sequence=message['client_sequence_number']))
    return dict(q, status='VERIFIED', sourceID=str(snap['source_id']), snapshotHash=snap['snapshot_hash'],
                messageCount=len(proof), messageBindings=proof, readOnly=True)


def read_binding(connection, q):
    validate_request(q)
    # Repeatable-read prevents assembling different revisions across these SELECTs.
    connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    connection.execute("SET LOCAL statement_timeout='5s'")
    session = connection.execute('SELECT id,vault_id,owner_subject_id,metadata FROM owner_truth.interview_sessions WHERE id=%s',
                                 (q['sessionID'],)).fetchone()
    require(session is not None, 'bindingSession')
    snap = connection.execute('SELECT source_id,state,revision,snapshot_hash,snapshot FROM owner_truth.live_recovery_snapshots WHERE session_id=%s AND revision=%s',
                              (q['sessionID'], q['snapshotRevision'])).fetchone()
    require(snap is not None, 'bindingMissing')
    row = connection.execute('SELECT metadata FROM owner_truth.sources WHERE id=%s AND vault_id=%s',
                             (snap['source_id'],session['vault_id'])).fetchone()
    require(row is not None, 'bindingSource')
    messages = connection.execute('SELECT id,client_sequence_number,author,content_payload FROM owner_truth.conversation_messages WHERE session_id=%s ORDER BY client_sequence_number',
                                  (q['sessionID'],)).fetchall()
    return verify_rows(q, session, snap, row['metadata'], messages)
