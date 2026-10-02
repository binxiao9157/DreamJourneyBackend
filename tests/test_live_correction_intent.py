from copy import deepcopy
import json
import unittest
from app.domain.owner_truth.live_topics import digest
from app.domain.owner_truth.live_theme_relations import validate_relation, bind_terminal_relation_display
from app.services.owner_truth_live_theme_provider import DeepSeekLiveThemeProvider
from app.core.config import Settings


def fixture(text='更正一下，我之前说错了，读书角叫星桥乙，不是星桥甲。'):
    def atom(aid,body,eid):
        return dict(atomId=aid,content=dict(memoryKind='experience',summary=body),evidenceIds=[eid],
                    evidence=[dict(evidenceId=eid,text=body)],supportState='supported',dimensions=['dailyLife'])
    old=atom('old','我的读书角叫星桥甲。','old-e');new=atom('new',text,'new-e')
    target=dict(topicId='topic',version=1,proposalHash=digest('old'),state='accepted',atoms=[old])
    material=dict(atoms=[new],targets=[target],theme=dict(title='读书角',summary='我的读书角叫星桥乙。'))
    proposal=dict(schemaVersion='owner-truth-live-theme-relation-v1',inputHash=digest(material),relation='correction',
        targetTopicId='topic',targetVersion=1,targetHash=target['proposalHash'],replaces={'new':'old'},duplicateAtomIds=[],title='读书角',summary='我的读书角叫星桥乙。',
        correctionEvidence={'new':dict(evidenceId='new-e',quote=text)})
    review=dict(schemaVersion='owner-truth-live-theme-relation-support-v1',inputHash=digest(material),proposalHash=digest(proposal),
        verdict='supported',sameSubjectEvent=True,compatibleTime=True,correctionsResolved=True,summarySupported=True,correctionIntentSupported=True)
    return dict(material=material,proposal=proposal,review=review)


def rebind(f):
    f['proposal']['inputHash']=digest(f['material']);f['review']['inputHash']=digest(f['material']);f['review']['proposalHash']=digest(f['proposal'])
    return f

class CorrectionIntentTests(unittest.TestCase):
    def test_legacy_true_predicates_without_user_intent_cannot_correct(self):
        f=fixture('我给自己的读书角取名叫星桥乙。我每周六上午在那里读书。')
        f['proposal'].pop('correctionEvidence');f['review'].pop('correctionIntentSupported');rebind(f)
        self.assertIsNone(validate_relation(**f))

    def test_wrong_old_other_atom_and_nonverbatim_evidence_are_denied(self):
        for proof in ({}, {'new':{'evidenceId':'old-e','quote':'我的读书角叫星桥甲。'}},
            {'new':{'evidenceId':'new-e','quote':'我改名了'}}, {'new':{'evidenceId':'new-e','quote':''}},
            {'other':{'evidenceId':'new-e','quote':'更正一下'}}, {'new':{'evidenceId':'new-e','quote':True}}):
            with self.subTest(proof=proof):
                f=fixture();f['proposal']['correctionEvidence']=proof;rebind(f)
                self.assertIsNone(validate_relation(**f))

    def test_independent_intent_verdict_must_be_true_boolean(self):
        for value in (False,None,1,0,'true'):
            f=fixture();f['review']['correctionIntentSupported']=value
            self.assertIsNone(validate_relation(**f))

    def test_clear_bound_correction_remains_supported(self):
        self.assertIsNotNone(validate_relation(**fixture()))

    def test_real_quote_does_not_override_uncertain_semantics(self):
        f=fixture('我给自己的读书角取名叫星桥乙。');f['review']['correctionIntentSupported']=False
        self.assertIsNone(validate_relation(**f))
        f['review'].update(correctionIntentSupported=True,verdict='uncertain');self.assertIsNone(validate_relation(**f))

    def test_each_mapping_requires_its_own_new_fact_evidence(self):
        f=fixture();old=deepcopy(f['material']['targets'][0]['atoms'][0]);old['atomId']='old2';f['material']['targets'][0]['atoms'].append(old)
        new=deepcopy(f['material']['atoms'][0]);new.update(atomId='new2',evidenceIds=['new-e2'],evidence=[dict(evidenceId='new-e2',text='更正桌子颜色为蓝色。')]);f['material']['atoms'].append(new)
        f['proposal']['replaces']['new2']='old2';rebind(f);self.assertIsNone(validate_relation(**f))
        f['proposal']['correctionEvidence']['new2']=dict(evidenceId='new-e',quote=f['material']['atoms'][0]['evidence'][0]['text']);rebind(f);self.assertIsNone(validate_relation(**f))
        f['proposal']['correctionEvidence']['new2']=dict(evidenceId='new-e2',quote='更正桌子颜色为蓝色。');rebind(f);self.assertIsNotNone(validate_relation(**f))

    def test_noncorrection_predicates_do_not_require_correction_intent(self):
        f=fixture();f['proposal'].update(relation='supplement',replaces={},correctionEvidence={});f['review'].pop('correctionIntentSupported');rebind(f)
        self.assertIsNotNone(validate_relation(**f))

    def test_terminal_relation_cannot_reintroduce_old_only_facts_even_if_model_approves(self):
        for state in ('accepted','rejected'):
            f=fixture();f['material']['targets'][0]['state']=state
            f['proposal']['summary']+='我每周六读书，桌子是蓝色的。';rebind(f)
            self.assertIsNone(validate_relation(**f))
            f['proposal']['summary']=f['material']['theme']['summary'];rebind(f)
            self.assertIsNotNone(validate_relation(**f))

    def test_terminal_binding_preserves_raw_model_proof_and_reviews_new_theme_only(self):
        f=fixture();raw=deepcopy(f['proposal']);raw['summary']+='旧场所还有蓝桌。'
        bound=bind_terminal_relation_display(material=f['material'],proposal=raw)
        self.assertIn('蓝桌',raw['summary']);self.assertNotIn('蓝桌',bound['summary'])
        self.assertEqual(bound['correctionEvidence'],raw['correctionEvidence'])
        f['proposal']=bound;rebind(f);self.assertIsNotNone(validate_relation(**f))
        raw['targetHash']='wrong'
        from app.domain.owner_truth.live_topics import LiveThemeConflict
        with self.assertRaises(LiveThemeConflict):bind_terminal_relation_display(material=f['material'],proposal=raw)

    def test_prompt_requires_original_user_intent_not_summary_difference(self):
        f=fixture();p=DeepSeekLiveThemeProvider(Settings(deepseek_api_key='local'))
        _,request=p.prepare_relation(material=f['material']);instruction=request.payload()['messages'][0]['content']
        self.assertIn('correctionEvidence',instruction)
        _,request=p.prepare_relation(material=f['material'],proposal=f['proposal']);instruction=request.payload()['messages'][0]['content']
        self.assertIn('correctionIntentSupported',instruction)
        self.assertIn('普通新陈述',instruction)

if __name__=='__main__':unittest.main()
