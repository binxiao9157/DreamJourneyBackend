"""Photo categories must not override the verified topic of confirmed facts."""
from copy import deepcopy
from pathlib import Path
import json
import unittest
from unittest.mock import patch
from app.domain.owner_truth import person_biography as domain
from app.domain.owner_truth.person_memory_model import _normalize_entry, build_person_memory_model
from test_s4_biography import fact
from test_s5_confirmed_biography import binding, blocks


def fixture():
    return json.loads((Path(__file__).parent/'fixtures/biography_photo_scope.json').read_text())


def build(data, previous=None):
    return domain.build_biography(data['records'], 'photo-scope-test', previous, bindings=data['bindings'])


class PhotoScopeTests(unittest.TestCase):
    def test_actual_five_facts_follow_two_verified_topics(self):
        data=fixture();out=blocks(build(data))
        expected={frozenset(r['memoryId'] for r in data['records'] if data['bindings'][r['memoryId']]['topicId']==t) for t in {b['topicId'] for b in data['bindings'].values()}}
        actual={frozenset(e['memoryId'] for e in b['evidence']) for b in out}
        self.assertEqual(actual,expected)
        self.assertEqual(sorted(len(b['sentences']) for b in out),[2,3])
        self.assertCountEqual([e for b in out for e in b['evidence']],[r['citation'] for r in data['records']])

    def test_category_without_binding_is_not_object_identity(self):
        data=fixture();data['records']=[r for r in data['records'] if '照片' in r['narrative']];data['bindings']={}
        self.assertEqual(len(blocks(build(data))),len(data['records']))

    def test_invalid_binding_cannot_join_other_photos(self):
        for field in ['contentHash','sourceId','memoryVersionId']:
            data=fixture();data['records']=[r for r in data['records'] if '照片' in r['narrative']]
            for b in data['bindings'].values():b[field]='invalid'
            self.assertTrue(all(len(b['evidence'])==1 for b in blocks(build(data))),field)

    def test_same_title_and_source_do_not_merge_different_topics(self):
        rs=[_normalize_entry(fact(i+1,t)) for i,t in enumerate(['贝壳照片放在甲相册。','邮票照片放在乙相册。'])]
        for r in rs:r['citation']['sourceId']='shared-source'
        bs={r['memoryId']:binding(r,'topic-'+str(i),'照片') for i,r in enumerate(rs)}
        self.assertEqual(len(blocks(build({'records':rs,'bindings':bs}))),2)

    def test_same_verified_topic_keeps_non_keyword_context(self):
        data=fixture();data['records']=[r for r in data['records'] if '邮票' in data['bindings'][r['memoryId']]['title']]
        self.assertEqual(len(blocks(build(data))),1)
        self.assertEqual(len(blocks(build(data))[0]['evidence']),3)

    def test_other_people_never_join_even_with_same_topic(self):
        rs=[_normalize_entry(fact(i+1,t)) for i,t in enumerate(['我整理照片。','我整理相册。'])]
        rs[1]['claimSubjectId']='another-person';bs={r['memoryId']:binding(r) for r in rs}
        self.assertEqual(len(blocks(build({'records':rs,'bindings':bs}))),2)

    def test_order_incremental_and_input_immutability(self):
        data=fixture();saved=deepcopy(data);full=build(data);previous=build({'records':data['records'][:2],'bindings':data['bindings']})
        self.assertEqual(full,build(data,previous))
        data['records'].reverse();self.assertEqual(full,build(data));data['records'].reverse();self.assertEqual(data,saved)

    def test_old_mixed_category_block_not_reused(self):
        data=fixture()
        with patch.object(domain,'identity',side_effect=lambda r,b=None:('old-mixed','照片与摄影','interests','兴趣与生活')):
            old=build(data)
        for b in blocks(old):b['text']='STALE MIXED PROSE';b['qualityState']='polished'
        rebuilt=build(data,old)
        self.assertNotIn('STALE MIXED PROSE',str(rebuilt))
        self.assertEqual(len(blocks(rebuilt)),2)

    def test_nonphoto_blocks_and_versions_are_exact(self):
        rs=[_normalize_entry(fact(i+1,t)) for i,t in enumerate(['我偶尔喝咖啡。','我出生于台州。','我种薄荷。'])]
        data={'records':rs,'bindings':{}};before=blocks(build(data));photo=fixture();photo['records']+=rs;after=blocks(build(photo))
        self.assertEqual(before,[b for b in after if b['topicId'] in {x['topicId'] for x in before}])

    def test_formal_and_retrieval_projections_unchanged(self):
        entries=[fact(1,'我整理贝壳照片。'),fact(2,'我整理邮票照片。')]
        old=build_person_memory_model(entries);new=build_person_memory_model(entries,biography_topics=True)
        for k in old:
            if k not in {'biographyProjection','modelVersion'}:self.assertEqual(old[k],new[k],k)

    def test_cached_profile_rebuilds_for_photo_rule_only(self):
        from test_owner_truth_person_memory_profile import OwnerTruthPersonMemoryProfileTests, _facets
        from app.domain.owner_truth.contracts import MemoryKind
        from app.services.owner_truth_person_memory_profile import OwnerTruthPersonMemoryProfileService
        h=OwnerTruthPersonMemoryProfileTests();h.setUp()
        h._activate(kind=MemoryKind.EXPERIENCE,content={'summary':'我整理照片。','facets':_facets()},created_at='2026-01-01T00:00:00Z')
        service=OwnerTruthPersonMemoryProfileService(h.store,biography_topics=True,include_composition=False)
        with patch.object(domain,'PHOTO_GROUPING_VERSION','old-rule',create=True):before=service.read(context=h.context)
        after=service.read(context=h.context);again=service.read(context=h.context)
        self.assertNotEqual(before.source_fingerprint,after.source_fingerprint)
        self.assertEqual(again.rebuild_mode,'unchanged')
        self.assertEqual(after.memory_model['formalMemories'],before.memory_model['formalMemories'])

if __name__=='__main__':unittest.main()
