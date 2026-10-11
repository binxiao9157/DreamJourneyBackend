from copy import deepcopy
import unittest
from app.domain.owner_truth.person_biography import apply_composition
from test_s4_biography import fact, blocks, build_person_memory_model


def approved(block, text):
    ids=[s['sentenceId'] for s in block['sentences']]
    return {'sentences':[{'text':text,'sourceSentenceIds':ids}],
            'audit':{'verdict':'supported','facts':[{'sentenceId':x,'covered':True,'qualifiersPreserved':True} for x in ids],
                     'sentences':[{'index':0,'supported':True}],'issues':[]}}

class PolishTests(unittest.TestCase):
    def setUp(self):
        self.block=blocks(build_person_memory_model([
            fact(1,'我偶尔在上午喝咖啡。'),fact(2,'我打算周末喝咖啡。')]))[0]
        self.response=approved(self.block,'我偶尔在上午喝咖啡，也打算在周末喝。')

    def test_natural_merge_has_exact_current_evidence(self):
        result=apply_composition(self.block,self.response)
        self.assertEqual(result['text'],self.response['sentences'][0]['text'])
        self.assertEqual(result['qualityState'],'polished')
        self.assertEqual(result['sentences'][0]['evidence'],self.block['evidence'])
        self.assertEqual(result['blockId'],self.block['blockId'])

    def test_audit_cannot_override_lost_frequency_plan_or_invented_year(self):
        for text in ('我每天在上午喝咖啡，也打算在周末喝。','我偶尔在上午喝咖啡，周末也喝。',
                     '2026年我偶尔在上午喝咖啡，也打算在周末喝。'):
            with self.subTest(text=text),self.assertRaises(ValueError):
                apply_composition(self.block,approved(self.block,text))

    def test_unsupported_causality_subject_and_again_are_rejected_by_audit(self):
        for text in ('因为焦虑，我又爱上了咖啡。','我的室友偶尔在上午喝咖啡。'):
            response=approved(self.block,text);response['audit']['verdict']='rejected'
            response['audit']['issues']=['invented cause or changed subject']
            with self.assertRaises(ValueError):apply_composition(self.block,response)

    def test_unknown_review_is_fallback_not_approval(self):
        self.response['audit']['verdict']='uncertain'
        with self.assertRaises(ValueError):apply_composition(self.block,self.response)

    def test_fabricated_reference_or_incomplete_audit_rejected(self):
        bad=deepcopy(self.response);bad['sentences'][0]['sourceSentenceIds']=['fake']
        with self.assertRaises(ValueError):apply_composition(self.block,bad)
        bad=deepcopy(self.response);bad['audit']['facts'].pop()
        with self.assertRaises(ValueError):apply_composition(self.block,bad)

    def test_relative_time_never_becomes_current_or_absolute(self):
        block=blocks(build_person_memory_model([fact(1,'最近两年我偶尔在上午喝咖啡。')]))[0]
        with self.assertRaises(ValueError):apply_composition(block,approved(block,'最近两年我偶尔在上午喝咖啡。'))
        with self.assertRaises(ValueError):apply_composition(block,approved(block,'从2024年起我偶尔在上午喝咖啡。'))
        good='那次谈起咖啡时，我提到最近两年偶尔在上午喝。'
        self.assertEqual(apply_composition(block,approved(block,good))['text'],good)

    def test_negation_cannot_disappear_despite_positive_audit(self):
        block=blocks(build_person_memory_model([fact(1,'我不喝咖啡。')]))[0]
        with self.assertRaises(ValueError):apply_composition(block,approved(block,'我喝咖啡。'))

    def test_split_source_qualifiers_remain_bound_to_whole_source(self):
        response=deepcopy(self.response)
        response['sentences']=[{'text':'我偶尔在上午喝咖啡。','sourceSentenceIds':[s['sentenceId'] for s in self.block['sentences']]},
                               {'text':'我也打算在周末喝。','sourceSentenceIds':[s['sentenceId'] for s in self.block['sentences']]}]
        response['audit']['sentences']=[{'index':i,'supported':True} for i in range(2)]
        self.assertEqual(apply_composition(self.block,response)['qualityState'],'polished')
