from copy import deepcopy
from dataclasses import replace
import unittest
from uuid import uuid4
from app.domain.owner_truth.live_topics import (
    SCHEMA, LiveThemeConflict, digest, validate_theme_review, next_theme_revision)

class LiveThemeContractTests(unittest.TestCase):
    def setUp(self):
        self.atoms=[dict(atomId=f'a{i}',evidenceIds=[f'e{i}'],contentHash=digest(str(i)),
            dimensions=['experience','emotion'],supportState='supported') for i in range(4)]
        self.proposal=dict(schemaVersion=SCHEMA,inputHash=digest(self.atoms),themes=[dict(
            key='杭州亲子游',title='杭州亲子游',summary='我和孩子去了杭州，也很开心。',
            atomIds=['a0','a1'],evidenceIds=['e0','e1'],dimensions=['experience','emotion'])],
            omittedAtomIds=['a2','a3'])
        self.review=dict(schemaVersion='owner-truth-live-theme-support-v1',inputHash=digest(self.atoms),
            proposalHash=digest(self.proposal),themes=[dict(key='杭州亲子游',atomIds=['a0','a1'],
            evidenceIds=['e0','e1'],verdict='supported',sameSubjectEvent=True,compatibleTime=True,correctionsResolved=True)])

    def validate(self):
        return validate_theme_review(atoms=self.atoms,proposal=self.proposal,review=self.review)

    def rebind(self):
        self.proposal['inputHash']=digest(self.atoms)
        self.review['inputHash']=digest(self.atoms)
        self.review['proposalHash']=digest(self.proposal)

    def test_multi_dimension_one_theme_and_explicit_omissions(self):
        result=self.validate()
        self.assertEqual(len(result.themes),1)
        self.assertEqual(result.themes[0].dimensions,('experience','emotion'))
        self.assertEqual(result.omitted_atom_ids,('a2','a3'))

    def test_independent_topic_survives_conflict_in_another(self):
        second=dict(key='work',title='工作',summary='我换了工作。',atomIds=['a2'],evidenceIds=['e2'],dimensions=['experience'])
        self.proposal['themes'].append(second);self.proposal['omittedAtomIds']=['a3']
        self.review['themes'].append(dict(key='work',atomIds=['a2'],evidenceIds=['e2'],verdict='uncertain',
            sameSubjectEvent=True,compatibleTime=True,correctionsResolved=False))
        self.rebind();result=self.validate()
        self.assertEqual(len(result.themes),1);self.assertEqual(result.omitted_atom_ids,('a2','a3'))
        self.assertEqual(result.blocked[0]['key'],'work')

    def test_wrong_source_evidence_rejected(self):
        self.proposal['themes'][0]['evidenceIds']=['e3'];self.rebind()
        with self.assertRaisesRegex(LiveThemeConflict,'themeEvidenceMismatch'):self.validate()

    def test_unsupported_time_or_event_does_not_force_merge(self):
        for field in ('sameSubjectEvent','compatibleTime','correctionsResolved'):
            with self.subTest(field=field):
                old=self.review['themes'][0][field];self.review['themes'][0][field]=False
                result=self.validate();self.assertEqual(result.themes,())
                self.review['themes'][0][field]=old

    def test_stale_support_does_not_validate_new_summary(self):
        self.proposal['themes'][0]['summary']='无原文支持的新情绪'
        with self.assertRaisesRegex(LiveThemeConflict,'themeReviewBindingMismatch'):self.validate()

    def test_support_cannot_promote_superseded_atom(self):
        self.atoms[0]['supportState']='superseded';self.rebind()
        self.assertEqual(self.validate().themes,())

    def test_omissions_cannot_silently_disappear(self):
        self.proposal['omittedAtomIds']=[];self.rebind()
        with self.assertRaisesRegex(LiveThemeConflict,'themeOmissionManifestMismatch'):self.validate()

    def test_atom_cannot_be_published_in_two_cards(self):
        second=deepcopy(self.proposal['themes'][0]);second['key']='other'
        self.proposal['themes'].append(second);self.rebind()
        with self.assertRaisesRegex(LiveThemeConflict,'wrongOrRepeatedAtom'):self.validate()

    def test_unknown_dimensions_are_not_inferred(self):
        self.proposal['themes'][0]['dimensions'].append('health');self.rebind()
        with self.assertRaisesRegex(LiveThemeConflict,'inventedThemeDimension'):self.validate()

    def args(self):
        theme=self.validate().themes[0];source=str(uuid4())
        return dict(previous=None,theme=theme,owner='o',vault='v',epoch=1,stable_key='event',
            source_id=source,snapshot_id=str(uuid4()),expected_version=0,
            member_bindings={aid:dict(candidateId=str(uuid4()),sourceId=source,
                proposalHash=digest(aid),factHash=digest(aid)) for aid in theme.atom_ids})

    def test_pending_revision_keeps_topic_identity_without_mutating_old(self):
        args=self.args();first=next_theme_revision(**args);old=deepcopy(first)
        second=next_theme_revision(**{**args,'previous':first,'expected_version':1,'snapshot_id':str(uuid4()),
            'theme':replace(args['theme'],summary='合法补充后的归纳')})
        self.assertEqual(second['topicId'],first['topicId']);self.assertEqual(second['version'],2)
        self.assertNotEqual(second['proposalHash'],first['proposalHash']);self.assertEqual(first,old)

    def test_same_snapshot_retry_is_idempotent_and_changed_content_rejected(self):
        args=self.args();first=next_theme_revision(**args)
        self.assertEqual(next_theme_revision(**{**args,'previous':first,'expected_version':1}),first)
        with self.assertRaisesRegex(LiveThemeConflict,'immutableThemeRevision'):
            next_theme_revision(**{**args,'previous':first,'expected_version':1,'theme':replace(args['theme'],summary='changed')})

    def test_stale_page_cannot_confirm_a_new_version(self):
        args=self.args();first=next_theme_revision(**args)
        with self.assertRaisesRegex(LiveThemeConflict,'themeVersionChanged'):
            next_theme_revision(**{**args,'previous':first,'expected_version':0,'snapshot_id':str(uuid4())})

    def test_scope_does_not_cross_account(self):
        args=self.args();first=next_theme_revision(**args)
        for key,value in [('owner','other'),('vault','other'),('epoch',2)]:
            with self.assertRaisesRegex(LiveThemeConflict,'themeOwnerMismatch'):
                next_theme_revision(**{**args,'previous':first,'expected_version':1,key:value})

    def test_rejected_or_formal_same_facts_do_not_resurrect(self):
        args=self.args();first=next_theme_revision(**args)
        for state in ('accepted','rejected'):
            result=next_theme_revision(**{**args,'previous':{**first,'state':state},'expected_version':1,
                'snapshot_id':str(uuid4())})
            self.assertEqual(result['status'],'noChange')

    def test_formal_supplement_is_new_related_pending_proposal(self):
        args=self.args();first=next_theme_revision(**args);formal={**first,'state':'accepted'}
        changed=deepcopy(args['member_bindings']);changed['a1']['factHash']=digest('new detail')
        result=next_theme_revision(**{**args,'previous':formal,'expected_version':1,
            'snapshot_id':str(uuid4()),'member_bindings':changed,'change_reason':'supplement'})
        self.assertNotEqual(result['topicId'],first['topicId']);self.assertEqual(result['linkedTopicId'],first['topicId'])
        self.assertEqual(result['state'],'pending');self.assertEqual(formal['state'],'accepted')

if __name__=='__main__':unittest.main()
