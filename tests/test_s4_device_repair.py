"""Actual S4 payloads, preserving provider field differences and negative boundaries."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json
import unittest
from test_s4_biography import fact, blocks, build_person_memory_model
from test_s4_biography_polish import approved
from app.domain.owner_truth.person_biography import apply_composition
from app.domain.owner_truth.fact_representation import explicit_year, formal_representation_match, coalesce_source_memories
from app.domain.owner_truth.memory_changeset import build_memory_changeset, OwnerTruthMemoryChangeOperationKind as Kind
from app.domain.owner_truth.contracts import MemoryKind
from tests import test_owner_truth_memory_changeset as h

DATA=json.loads((Path(__file__).parent/'fixtures/s4_device_representation.json').read_text())

class DeviceProseTests(unittest.TestCase):
    def test_actual_untyped_origami_facts_share_one_topic(self):
        entries=[fact(i+1,x['content']['statement']) for i,x in enumerate(DATA['live-short'])]
        result=blocks(build_person_memory_model(entries))
        self.assertEqual(len(result),1)
        self.assertEqual(len(result[0]['evidence']),2)

    def test_verbatim_or_reordered_copy_is_not_polished(self):
        b=blocks(build_person_memory_model([fact(1,'2017年，我喜欢喝咖啡。'),fact(2,'2024年，我喜欢喝咖啡。')]))[0]
        for text in [''.join(s['text'] for s in b['sentences']), ''.join(s['text'] for s in reversed(b['sentences']))]:
            with self.assertRaisesRegex(ValueError,'compositionNotIntegrated'):
                apply_composition(b,approved(b,text))

    def test_integrated_discrete_years_keep_both_identities(self):
        b=blocks(build_person_memory_model([fact(1,'2017年，我喜欢喝咖啡。'),fact(2,'2024年，我喜欢喝咖啡。')]))[0]
        result=apply_composition(b,approved(b,'2017年和2024年，我都喜欢喝咖啡。'))
        self.assertEqual(result['qualityState'],'polished');self.assertEqual(len(result['sentences'][0]['evidence']),2)

    def test_unrelated_objects_people_and_ambiguous_phrases_stay_separate(self):
        for texts in [
            ['我练习折纸。','我练习书法。'],
            ['我练习折纸。','室友练习折纸。'],
            ['我把折纸放在蓝色收纳盒里。','我把陶笛放在蓝色收纳盒里。'],
            ['我练习折纸。','我练习折纸和书法。'],
            ['我练习东西。','我把东西放在盒子里。'],
            ['我练习折纸。','我把它放在盒子里。'],
            ['我练习折纸。','张三练习折纸。'],
            ['我练习折纸。','我练习折纸，也喜欢烘焙。'],
        ]:
            self.assertEqual(len(blocks(build_person_memory_model([fact(i+1,t) for i,t in enumerate(texts)]))),2,texts)

    def test_nonpreset_topic_stable_and_photo_unchanged(self):
        for noun in ['折纸','书法','陶艺']:
            before=blocks(build_person_memory_model([fact(1,'我练习'+noun+'。'),fact(3,'我打算整理照片。')]))
            after=blocks(build_person_memory_model([fact(1,'我练习'+noun+'。'),fact(2,'我把'+noun+'放在盒子里。'),fact(3,'我打算整理照片。')]))
            self.assertEqual(len(after),2)
            self.assertEqual(next(b for b in before if '照片' in b['text']),next(b for b in after if '照片' in b['text']))
            self.assertEqual(next(b for b in before if noun in b['text'])['topicId'],next(b for b in after if noun in b['text'])['topicId'])

class DevicePeriodTests(unittest.TestCase):
    setUp=h.OwnerTruthMemoryChangeSetTests.setUp
    _content=h.OwnerTruthMemoryChangeSetTests._content
    _candidate=h.OwnerTruthMemoryChangeSetTests._candidate
    _current=h.OwnerTruthMemoryChangeSetTests._current

    def test_actual_archive_text_payloads_link_period_in_both_directions(self):
        for a,b in [('coffee-2017','coffee-2024'),('coffee-2024','coffee-2017')]:
            old,new=deepcopy(DATA[a][0]),deepcopy(DATA[b][0]);saved=deepcopy(old)
            current=replace(self._current(content=self._content(),source_id='00000000-0000-4000-8000-000000000011'),memory_kind=MemoryKind(old['memoryKind']),content=old['content'])
            base=self._candidate(content=self._content())
            candidate=replace(base,memory_kind=MemoryKind(new['memoryKind']),content_hash=h._hash(new['content']),payload={**base.payload,'content':new['content']})
            result=build_memory_changeset(candidate=candidate,current_memories=[current],base_memory_revision=3)
            self.assertEqual(result.operation.kind,Kind.TEMPORAL_CHANGE)
            self.assertEqual(old,saved)

    def test_year_start_is_only_redundant_when_exact_year(self):
        c=deepcopy(DATA['coffee-2024'][0]['content'])
        self.assertEqual(explicit_year(c),'2024年')
        for start in ['2023','2024-01','2024-01-01']:
            c['qualifiers']['validTime']['start']=start;self.assertIsNone(explicit_year(c))

    def test_disjoint_years_never_coalesce(self):
        self.assertEqual(len(coalesce_source_memories([deepcopy(DATA[n][0]) for n in ['coffee-2017','coffee-2024']])),2)

    def test_scope_subject_strength_object_and_extra_details_not_erased(self):
        a=deepcopy(DATA['coffee-2017'][0]['content']);b=deepcopy(DATA['coffee-2024'][0]['content'])
        changes=[('claimSubjectId','other'),('object',{'label':'茶'}),('exceptions',['晚上不喝']),('predicate','disliked')]
        for field,value in changes:
            c=deepcopy(b);c[field]=value
            self.assertFalse(formal_representation_match(a,c,different_years=True),field)
        for field,value in [('strengthExpression','最喜欢'),('scenario','工作时'),('polarity','negative'),('superlativeAsserted',True)]:
            c=deepcopy(b);c['qualifiers'][field]=value
            self.assertFalse(formal_representation_match(a,c,different_years=True),field)
