from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import unittest
from app.services.owner_truth_live_recovery import (
    LiveRecoveryConflict, RecoveryPolicy, enroll, transition, progress,
)

class LiveRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026,9,29,tzinfo=timezone.utc)
        self.record = enroll(session_id='session',owner_id='owner',vault_id='vault',
                             generation=1,authority_epoch=0,now=self.now)

    def apply(self, action, seconds=0, **payload):
        self.record = transition(self.record,action=action,generation=1,
                                 now=self.now+timedelta(seconds=seconds),payload=payload)
        return self.record

    def receipt(self, seq, seconds=0, text='same text', **extra):
        return self.apply('receipt',seconds,sequence=seq,messageId=f'm{seq}',commandId=f'c{seq}',
                          contentHash=sha256(text.encode()).hexdigest(),role='owner',**extra)

    def test_gap_is_not_maximum_sequence(self):
        for seq in (*range(1,63),64): self.receipt(seq)
        self.apply('close',finalSequence=64)
        p=progress(self.record)
        self.assertEqual(p['continuousSequence'],62)
        self.assertEqual(p['missingRanges'],[[63,63]])
        self.receipt(63)
        self.apply('tick')
        self.assertEqual(self.record['snapshots'][0]['missingRanges'],[])

    def test_close_precedes_bodies_and_does_not_reset_deadline(self):
        self.apply('close',finalSequence=4)
        self.apply('close',50,finalSequence=4)
        self.receipt(1,51); self.receipt(2,51)
        self.apply('tick',60)
        snap=self.record['snapshots'][0]
        self.assertEqual(snap['receivedRanges'],[[1,2]])
        self.assertEqual(snap['missingRanges'],[[3,4]])
        self.assertTrue(snap['endPositionKnown'])

    def test_abnormal_short_does_not_need_end(self):
        for seq in range(1,5): self.receipt(seq)
        self.apply('tick',90)
        self.assertEqual(self.record['state'],'repairing')
        self.apply('tick',150)
        self.assertEqual(self.record['state'],'frozen')
        self.assertFalse(self.record['snapshots'][0]['endPositionKnown'])

    def test_restart_does_not_extend_lease_or_repair_budget(self):
        self.receipt(1)
        self.record = deepcopy(self.record)  # Serialized coordinates are the full recovery state.
        self.apply('tick',500)
        self.assertEqual(self.record['state'],'frozen')
        self.assertEqual(self.record['repairDeadline'],(self.now+timedelta(seconds=150)).isoformat())

    def test_silence_with_heartbeat_is_live(self):
        self.receipt(1)
        for second in range(20,400,20):
            self.apply('heartbeat',second); self.apply('tick',second)
        self.assertEqual(self.record['state'],'collecting')
        self.assertEqual(self.record['snapshots'],[])

    def test_disconnect_recovers_before_deadline_only(self):
        self.apply('disconnect',10); self.apply('heartbeat',69)
        self.assertEqual(self.record['state'],'collecting')
        self.apply('disconnect',70)
        with self.assertRaisesRegex(LiveRecoveryConflict,'activityLeaseExpired'):
            self.apply('heartbeat',130)

    def test_replayed_receipt_preserves_identity_count_and_deadline(self):
        self.receipt(1); old=deepcopy(self.record)
        self.receipt(1,20)
        self.assertEqual(self.record,old)
        with self.assertRaisesRegex(LiveRecoveryConflict,'immutableMessageConflict'):
            self.receipt(1,text='changed')
        self.receipt(2) # Same body is a different legitimate message.
        self.assertEqual(len(self.record['messages']),2)

    def test_generation_and_final_boundary_are_fenced(self):
        with self.assertRaises(LiveRecoveryConflict):
            transition(self.record,action='heartbeat',generation=2,now=self.now)
        self.receipt(1); self.apply('close',finalSequence=2)
        with self.assertRaisesRegex(LiveRecoveryConflict,'immutableFinalSequence'):
            self.apply('close',finalSequence=3)
        with self.assertRaisesRegex(LiveRecoveryConflict,'messageOutsideFinalBoundary'):
            self.receipt(3)

    def test_late_body_is_new_revision_not_snapshot_mutation(self):
        self.receipt(1);self.apply('close',finalSequence=2);self.apply('tick',60)
        old=deepcopy(self.record['snapshots'][0])
        self.receipt(2,70);self.apply('tick',70);self.apply('tick',71)
        self.assertEqual(self.record['snapshots'][0],old)
        self.assertEqual(len(self.record['snapshots']),2)
        self.assertEqual(self.record['snapshots'][1]['missingRanges'],[])
        with self.assertRaisesRegex(LiveRecoveryConflict,'closedGenerationCannotRenew'):
            self.apply('heartbeat',71)

    def test_late_final_position_creates_metadata_revision(self):
        self.receipt(1);self.apply('tick',151)
        self.apply('close',152,finalSequence=1);self.apply('tick',152)
        self.assertFalse(self.record['snapshots'][0]['endPositionKnown'])
        self.assertTrue(self.record['snapshots'][1]['endPositionKnown'])

    def test_invalid_capacity_and_sequence_do_not_mutate(self):
        old=deepcopy(self.record)
        with self.assertRaises(LiveRecoveryConflict): self.receipt(10001)
        with self.assertRaises(LiveRecoveryConflict): self.apply('close',finalSequence=True)
        with self.assertRaises(LiveRecoveryConflict): RecoveryPolicy(repair_seconds=0)
        self.assertEqual(self.record,old)

if __name__=='__main__': unittest.main()

class LiveRecoveryPaginationTests(unittest.TestCase):
    def test_range_pages_are_complete_and_bound_to_one_progress_version(self):
        now=datetime(2026,9,29,tzinfo=timezone.utc)
        record=enroll(session_id='s',owner_id='o',vault_id='v',generation=1,authority_epoch=0,now=now)
        for seq in range(1,1100,2):
            record=transition(record,action='receipt',generation=1,now=now,payload=dict(
                sequence=seq,messageId=f'm{seq}',commandId=f'c{seq}',
                contentHash=sha256(str(seq).encode()).hexdigest(),role='owner'))
        record=transition(record,action='close',generation=1,now=now,payload={'finalSequence':1100})
        received=[];missing=[];offset=0
        while True:
            page=progress(record,range_offset=offset,expected_version=record['version'])
            self.assertLessEqual(len(page['receivedRanges']),256)
            self.assertLessEqual(len(page['missingRanges']),256)
            received+=page['receivedRanges'];missing+=page['missingRanges']
            offset=page['nextRangeOffset']
            if offset is None:break
        self.assertEqual(received,[[i,i] for i in range(1,1100,2)])
        self.assertEqual(missing,[[i,i] for i in range(2,1101,2)])
        with self.assertRaisesRegex(LiveRecoveryConflict,'continuationVersionRequired'):
            progress(record,range_offset=256)
        changed=transition(record,action='receipt',generation=1,now=now,payload=dict(
            sequence=2,messageId='m2',commandId='c2',contentHash=sha256(b'2').hexdigest(),role='owner'))
        with self.assertRaisesRegex(LiveRecoveryConflict,'progressVersionChanged'):
            progress(changed,range_offset=256,expected_version=record['version'])
