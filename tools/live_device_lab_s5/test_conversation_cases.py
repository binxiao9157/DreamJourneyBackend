import unittest
from conversation_cases import natural_case,semantic_case,expected_for_completed

class ConversationCaseTests(unittest.TestCase):
    def test_deterministic_and_varied_scenarios(self):
        self.assertEqual(natural_case('10m','a'),natural_case('10m','a'))
        self.assertEqual(len({natural_case('10m',str(i))['scenario'] for i in range(20)}),3)
    def test_natural_content_not_looped_and_terms_exist(self):
        for profile,n in [('short',2),('10m',32)]:
            for seed in range(10):
                c=natural_case(profile,str(seed));self.assertEqual(len(c['turns']),n)
                self.assertEqual(len({t['text'] for t in c['turns']}),n)
                for t in c['turns']:
                    for term in t['requiredASRTerms']:self.assertIn(term,t['text'])
                for term in c['requiredMemoryTerms']:self.assertTrue(any(term in t['text'] for t in c['turns'][:2]))
    def test_completed_scope_never_requires_unsent_rounds(self):
        c=natural_case('10m','test')
        for n in [0,2,24,32]:
            e=expected_for_completed(c,n);self.assertEqual(len(e['facts']),n)
            self.assertEqual(e['semanticValidation'],'NOT_RUN')
            self.assertTrue(all(t['ordinal']<=n for t in e['facts']))
        for n in [-1,33,True,None]:
            with self.assertRaises(ValueError):expected_for_completed(c,n)
    def test_semantics_is_separate_and_targets_exist(self):
        c=semantic_case('test');seen=set()
        for t in c['turns']:
            if t['targetFactKey']:self.assertIn(t['targetFactKey'],seen)
            seen.add(t['factKey'])
        self.assertEqual(c['semanticValidation'],'NOT_RUN')
        self.assertIn('contradiction-review-required',[t['expectedAction'] for t in c['turns']])
        self.assertEqual(c['turns'][0]['text'],c['turns'][1]['text'])
