"""Real PG boundary checks. Seed with scripts/backend-live-recovery-run-terminal-smoke.py.

Set TEST_LIVE_RECOVERY_DSN to that disposable local database. No production DSN.
Controlled fault fixtures are rolled back or restored; no model is invoked here.
"""
import os
from copy import deepcopy
from dataclasses import replace
from uuid import uuid4
from urllib.parse import urlparse

import unittest
from contextlib import contextmanager
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_live_recovery import PostgresLiveRecoveryRepository, LiveRecoveryConflict


@contextmanager
def lane():
    dsn = os.environ.get('TEST_LIVE_RECOVERY_DSN')
    if not dsn:
        raise unittest.SkipTest('requires disposable local PG smoke database')
    parsed = urlparse(dsn)
    assert parsed.hostname in {'localhost', '127.0.0.1'}
    assert parsed.path.startswith('/dj_live_recovery_')
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        run = conn.execute("SELECT * FROM owner_truth.live_memory_runs WHERE product_session_id='short-A'").fetchone()
        assert run is not None, 'run the local smoke script first'
        snap = conn.execute('''SELECT s.* FROM owner_truth.live_recovery_snapshots s
            JOIN owner_truth.interview_sessions i ON i.id=s.session_id WHERE i.product_session_id='short-A' ''').fetchone()
        context = OwnerTruthCommandContext(vault_id=run['vault_id'], owner_subject_id=run['owner_subject_id'], actor_subject_id=run['owner_subject_id'])
        args = dict(snapshot_id=str(snap['id']), source_id=str(snap['source_id']), manifest=snap['publication_manifest'], context=context, authority_epoch=run['authority_epoch'])
        repo = PostgresLiveRecoveryRepository(conn)
        try:
            yield conn, repo, run, snap, args
        finally:
            conn.rollback()


def state(conn, run):
    return conn.execute('SELECT state FROM owner_truth.live_memory_runs WHERE id=%s', (run['id'],)).fetchone()['state']


def organizing(conn, run):
    conn.execute("UPDATE owner_truth.live_memory_runs SET state='organizing' WHERE id=%s", (run['id'],))


def _check_publication_settles_and_replay_keeps_budget(lane):
    conn, repo, run, snap, args = lane
    organizing(conn, run)
    repo.publish_snapshot(**args)
    repo.publish_snapshot(**args)
    result = conn.execute('SELECT * FROM owner_truth.live_memory_runs WHERE id=%s', (run['id'],)).fetchone()
    assert result['state'] == 'published'
    for field in ('provider_request_count', 'reserved_input_tokens', 'reserved_output_tokens', 'organization_started_at', 'last_progress_at', 'budget_policy_hash', 'failure_code'):
        assert result[field] == run[field]


def _check_ineligible_run_is_not_finished(lane, guard):
    conn, repo, run, snap, args = lane
    organizing(conn, run)
    if guard == 'unfrozen-body':
        conn.execute("UPDATE owner_truth.live_recovery_sessions SET coordinates=jsonb_set(coordinates,'{pendingSnapshot}','true') WHERE session_id=%s", (snap['session_id'],))
    elif guard == 'newer-snapshot':
        conn.execute('''INSERT INTO owner_truth.live_recovery_snapshots (id,session_id,revision,snapshot_hash,snapshot)
            VALUES (%s,%s,%s,%s,%s)''', (uuid4(), snap['session_id'], snap['revision']+1, snap['snapshot_hash'], Jsonb(snap['snapshot'])))
    elif guard == 'pending-atom':
        conn.execute("UPDATE owner_truth.live_memory_work_units SET state='planned' WHERE run_id=%s AND kind='atomExtraction'", (run['id'],))
    else:
        conn.execute("UPDATE owner_truth.live_memory_runs SET state='failed',failure_code='controlled-original-fault' WHERE id=%s", (run['id'],))
    repo.publish_snapshot(**args)
    assert state(conn, run) == ('failed' if guard == 'failed-run' else 'organizing')


def _check_cross_identity_rejected(lane, invalid):
    conn, repo, run, snap, args = lane
    bad = deepcopy(args)
    if invalid == 'source': bad['source_id'] = str(uuid4())
    elif invalid == 'hash': bad['manifest']['hash'] = '0'*64
    elif invalid == 'owner': bad['context'] = replace(args['context'], owner_subject_id='other', actor_subject_id='other')
    else: bad['authority_epoch'] += 1
    organizing(conn, run)
    with unittest.TestCase().assertRaises(LiveRecoveryConflict):
        repo.publish_snapshot(**bad)
    assert state(conn, run) == 'organizing'
    assert conn.execute('SELECT publication_manifest FROM owner_truth.live_recovery_snapshots WHERE id=%s',(snap['id'],)).fetchone()['publication_manifest'] == snap['publication_manifest']


def _check_later_transaction_fault_rolls_back_snapshot_and_run(lane):
    conn, repo, run, snap, args = lane
    organizing(conn, run)
    conn.execute("UPDATE owner_truth.live_recovery_snapshots SET state='pending' WHERE id=%s", (snap['id'],))
    with unittest.TestCase().assertRaisesRegex(RuntimeError, 'after publication'):
        with conn.transaction():
            repo.publish_snapshot(**args)
            assert state(conn, run) == 'published'
            raise RuntimeError('after publication')
    assert state(conn, run) == 'organizing'
    assert conn.execute('SELECT state FROM owner_truth.live_recovery_snapshots WHERE id=%s',(snap['id'],)).fetchone()['state'] == 'pending'


def _check_empty_snapshot_no_change_finishes(lane):
    conn, repo, run, snap, args = lane
    organizing(conn, run)
    empty = deepcopy(snap['snapshot']); empty['messages'] = {}; empty['revision'] += 1
    new_id = uuid4()
    conn.execute('''INSERT INTO owner_truth.live_recovery_snapshots (id,session_id,revision,snapshot_hash,snapshot)
        VALUES (%s,%s,%s,%s,%s)''',(new_id,snap['session_id'],empty['revision'],snap['snapshot_hash'],Jsonb(empty)))
    repo.publish_snapshot(**{**args,'snapshot_id':str(new_id),'source_id':None,'manifest':None})
    assert state(conn,run) == 'published'
    assert conn.execute('SELECT state,failure_code FROM owner_truth.live_recovery_snapshots WHERE id=%s',(new_id,)).fetchone() == dict(state='noChange',failure_code='noOwnerText')


class RecoveryRunTerminalPostgresTests(unittest.TestCase):
    def test_settles_and_replay_keeps_budget(self):
        with lane() as values: _check_publication_settles_and_replay_keeps_budget(values)

    def test_ineligible_guards(self):
        for guard in ('unfrozen-body', 'newer-snapshot', 'pending-atom', 'failed-run'):
            with self.subTest(guard=guard), lane() as values:
                _check_ineligible_run_is_not_finished(values, guard)

    def test_identity_guards(self):
        for invalid in ('source', 'hash', 'owner', 'epoch'):
            with self.subTest(invalid=invalid), lane() as values:
                _check_cross_identity_rejected(values, invalid)

    def test_transaction_abort(self):
        with lane() as values: _check_later_transaction_fault_rolls_back_snapshot_and_run(values)

    def test_empty_snapshot(self):
        with lane() as values: _check_empty_snapshot_no_change_finishes(values)

    def test_concurrent_late_coordinate_wins_over_old_publication(self):
        # Repository-level race, separate from the HTTP/body/default-Worker
        # late repair smoke. Hold the recovery row, apply a controlled receipt,
        # then prove the old publisher waits and cannot finish newer work.
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        import time
        with lane() as values:
            conn, repo, run, snap, args = values
            original = conn.execute('SELECT coordinates FROM owner_truth.live_recovery_sessions WHERE session_id=%s FOR UPDATE', (snap['session_id'],)).fetchone()['coordinates']
            altered = deepcopy(original); altered['finalSequence'] = None
            conn.execute('UPDATE owner_truth.live_recovery_sessions SET coordinates=%s WHERE session_id=%s', (Jsonb(altered),snap['session_id']))
            started = Event(); pid = []
            def old_publisher():
                with psycopg.connect(os.environ['TEST_LIVE_RECOVERY_DSN']) as other:
                    other.execute("SET lock_timeout='5s'")
                    pid.append(other.info.backend_pid); started.set()
                    PostgresLiveRecoveryRepository(other).publish_snapshot(**args)
            try:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(old_publisher)
                    try:
                        self.assertTrue(started.wait(2))
                        until = time.monotonic()+2
                        blocked = False
                        while time.monotonic()<until:
                            blocked = conn.execute('SELECT %s=ANY(pg_blocking_pids(%s)) AS blocked',(conn.info.backend_pid,pid[0])).fetchone()['blocked']
                            if blocked: break
                            time.sleep(.01)
                        self.assertTrue(blocked, 'publisher did not wait on recovery row')
                        repo.apply(session_id=str(snap['session_id']),context=args['context'],authority_epoch=args['authority_epoch'],generation=1,action='receipt',payload=dict(sequence=3,messageId=str(uuid4()),commandId=str(uuid4()),contentHash='a'*64,role='owner'))
                        self.assertEqual(state(conn,run),'organizing')
                        conn.commit()
                    finally:
                        conn.rollback()  # release row even if the race assertion fails
                    future.result(timeout=6)
                self.assertEqual(state(conn,run),'organizing')
                pending = conn.execute('SELECT coordinates FROM owner_truth.live_recovery_sessions WHERE session_id=%s',(snap['session_id'],)).fetchone()['coordinates']
                self.assertTrue(pending['pendingSnapshot'])
            finally:
                conn.rollback()
                conn.execute('UPDATE owner_truth.live_recovery_sessions SET coordinates=%s WHERE session_id=%s',(Jsonb(original),snap['session_id']))
                conn.execute('UPDATE owner_truth.live_memory_runs SET state=%s,updated_at=%s WHERE id=%s',(run['state'],run['updated_at'],run['id']))
                conn.commit()
