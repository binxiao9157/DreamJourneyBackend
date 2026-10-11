from copy import deepcopy
import unittest
from unittest.mock import patch
from test_owner_truth_person_memory_model import _entry
from app.domain.owner_truth.person_memory_model import build_person_memory_model as _build, build_person_memory_model_incremental as _incremental

def build_person_memory_model(entries):
    return _build(entries, biography_topics=True)
def build_person_memory_model_incremental(**kwargs):
    return _incremental(**kwargs, biography_topics=True)
def fact(n,text):
    return _entry(suffix=n,kind='experience',content={'summary':text})
def blocks(model):
    return [b for s in model['biographyProjection']['sections'] for b in s['blocks']]

class S4BiographyTests(unittest.TestCase):
    def test_concrete_topics_not_three_fact_buckets(self):
        result=blocks(build_person_memory_model([fact(1,'大学时我喝咖啡提神。'),fact(2,'我出生于浙江台州。'),fact(3,'我打算整理照片。'),fact(4,'最近两年工作忙，我偶尔喝咖啡。')]))
        coffee=[b for b in result if '咖啡' in b['text']]
        self.assertEqual(len(coffee),1)
        self.assertNotIn('出生',coffee[0]['text']);self.assertNotIn('照片',coffee[0]['text'])
        self.assertTrue(all(not b['text'].startswith(('后来','在我的人生记录里')) for b in result))
        self.assertIn('偶尔',coffee[0]['text'])
        self.assertIn('打算',''.join(b['text'] for b in result))
    def test_topic_identity_survives_new_fact_and_unrelated_block_is_exact(self):
        a=[fact(1,'我大学时喝咖啡。'),fact(2,'我喜欢整理照片。')]
        before=build_person_memory_model(a)
        after=build_person_memory_model_incremental(previous_model=before,entries=a+[fact(3,'我工作时偶尔喝咖啡。')])
        old=next(b for b in blocks(before) if '咖啡' in b['text']);new=next(b for b in blocks(after) if '咖啡' in b['text'])
        self.assertEqual(old['blockId'],new['blockId'])
        self.assertEqual(next(b for b in blocks(before) if '照片' in b['text']),next(b for b in blocks(after) if '照片' in b['text']))
    def test_sentence_evidence_exact_and_no_invented_transition(self):
        a=[fact(1,'我偶尔喝咖啡。'),fact(2,'我打算学习摄影。')]
        model=build_person_memory_model(a)
        versions={e['memoryVersionId']:e for e in a}
        seen=set()
        for b in blocks(model):
            for sentence in b['sentences']:
                for ref in sentence['evidence']:
                    self.assertEqual(ref['contentHash'],versions[ref['memoryVersionId']]['contentHash']);seen.add(ref['memoryVersionId'])
            self.assertNotIn('后来',b['text']);self.assertNotIn('又',b['text'])
        self.assertEqual(seen,set(versions))
    def test_algorithm_upgrade_cannot_reuse_legacy_sections(self):
        a=[fact(1,'我喜欢咖啡。')];old=build_person_memory_model(a)
        old['algorithmVersion']='old'
        old['biographyProjection']['sections'][0]['blocks'][0]['text']='后来，错误旧缓存。'
        rebuilt=build_person_memory_model_incremental(previous_model=old,entries=a)
        self.assertNotIn('错误旧缓存',str(rebuilt))
    def test_deletion_and_independent_subjects(self):
        a=[fact(1,'我喜欢咖啡。'),fact(2,'室友喜欢咖啡。')]
        model=build_person_memory_model(a)
        self.assertEqual(len(blocks(model)),2)
        after=build_person_memory_model_incremental(previous_model=model,entries=a[1:])
        self.assertNotIn(a[0]['memoryVersionId'],str(after['biographyProjection']))

    def test_body_strategy_does_not_change_retrieval_or_other_projections(self):
        entries=[fact(1,'我偶尔喝咖啡。'),fact(2,'我打算整理照片。')]
        old=_build(entries);new=_build(entries,biography_topics=True)
        for key in old:
            if key not in {'biographyProjection','modelVersion'}:
                self.assertEqual(old[key],new[key],key)
