from copy import deepcopy
import unittest
from app.domain.owner_truth.live_topics import digest,LiveThemeConflict
from app.domain.owner_truth.live_theme_relations import validate_relation,SCHEMA

class RelationEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.old={'atomId':'old','content':{'memoryKind':'knowledge','claim':'我在杭州读书。','sourceTurnIndices':[1]}}
        self.new={'atomId':'new','content':{'memoryKind':'knowledge','claim':'我在杭州读书。','sourceTurnIndices':[9]}}
        self.material={'atoms':[self.new],'targets':[{'topicId':'topic','version':2,'proposalHash':digest('v2'),'atoms':[self.old]}]}
        self.proposal={'schemaVersion':SCHEMA,'inputHash':digest(self.material),'relation':'duplicate',
            'targetTopicId':'topic','targetVersion':2,'targetHash':digest('v2'),'duplicateAtomIds':['new'],'replaces':{},'title':'求学','summary':'我在杭州读书。'}
    def checked(self):return validate_relation(material=self.material,proposal=self.proposal)
    def test_duplicate_needs_same_fact_not_same_keyword(self):
        self.assertIsNotNone(self.checked())
        self.new['content']['claim']='我的同学在杭州读书。';self.proposal['inputHash']=digest(self.material)
        with self.assertRaisesRegex(LiveThemeConflict,'unprovenThemeDuplicate'):self.checked()
    def test_wrong_or_stale_target_rejected(self):
        self.proposal['targetVersion']=1
        with self.assertRaisesRegex(LiveThemeConflict,'themeRelationTargetMismatch'):self.checked()
    def test_correction_requires_exact_old_and_new_member_binding(self):
        self.proposal.update(relation='correction',duplicateAtomIds=[],replaces={'new':'invented'})
        with self.assertRaisesRegex(LiveThemeConflict,'themeRelationMemberMismatch'):self.checked()
        self.proposal['replaces']={'new':'old'};self.assertIsNotNone(self.checked())
    def test_independent_uncertain_review_blocks_relation(self):
        review={'schemaVersion':'owner-truth-live-theme-relation-support-v1','inputHash':digest(self.material),
            'proposalHash':digest(self.proposal),'verdict':'supported','sameSubjectEvent':True,'compatibleTime':True,
            'correctionsResolved':True,'summarySupported':False}
        self.assertIsNone(validate_relation(material=self.material,proposal=self.proposal,review=review))
        review['summarySupported']=True
        self.assertIsNotNone(validate_relation(material=self.material,proposal=self.proposal,review=review))
        self.proposal['summary']='无依据情绪'
        with self.assertRaisesRegex(LiveThemeConflict,'themeRelationReviewBindingMismatch'):
            validate_relation(material=self.material,proposal=self.proposal,review=review)
