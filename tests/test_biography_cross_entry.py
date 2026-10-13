"""Cross-entry business assertions use the real device's distinct input shapes."""
from copy import deepcopy
from pathlib import Path
import json
import unittest
from app.domain.owner_truth.person_memory_model import _normalize_entry, build_person_memory_model, build_person_memory_model_incremental
from app.domain.owner_truth.person_biography import build_biography, input_within_budget
from test_owner_truth_person_memory_model import _entry
from test_s4_biography import fact
from test_s5_confirmed_biography import binding, blocks


def scenario():
    data=json.loads((Path(__file__).parent/'fixtures/cross_entry_object.json').read_text())
    entries=[_entry(suffix=i+1,kind=k,content=c,schema_version='owner-truth-v5') for i,(c,k) in enumerate(zip(data['contents'],data['kinds']))]
    records=[_normalize_entry(e) for e in entries]
    bindings={r['memoryId']:binding(r,'live-root','手摇音乐盒：位置与颜色') for r in records[:2]}
    return entries,records,bindings


def build(records, bindings, previous=None):
    return build_biography(records,'synthetic-snapshot',previous,bindings=bindings)


class CrossEntryTests(unittest.TestCase):
    def test_device_live_then_archive_joins_original_paragraph(self):
        _,rs,bs=scenario();before=blocks(build(rs[:2],bs));out=blocks(build(rs,bs))
        self.assertEqual(len(out),1)
        self.assertEqual(out[0]['topicId'],before[0]['topicId'])
        self.assertEqual(len(out[0]['sentences']),3)
        self.assertCountEqual(out[0]['evidence'],[r['citation'] for r in rs])
        self.assertNotEqual(out[0]['sourceRevision'],before[0]['sourceRevision'])

    def test_archive_then_live_and_order_independent(self):
        _,rs,bs=scenario();before=build(rs[2:],bs)
        after=build(rs,bs,before)
        self.assertEqual(len(blocks(after)),1)
        self.assertEqual(after,build(list(reversed(rs)),bs))
        self.assertEqual(after,build(rs,bs))

    def test_full_and_incremental_model_keep_other_projections(self):
        entries,rs,bs=scenario();old=build_person_memory_model(entries[:2],biography_topics=True,biography_bindings=bs)
        full=build_person_memory_model(entries,biography_topics=True,biography_bindings=bs)
        inc=build_person_memory_model_incremental(previous_model=old,entries=entries,biography_topics=True,biography_bindings=bs)
        self.assertEqual(full['biographyProjection'],inc['biographyProjection'])
        self.assertEqual(len(blocks(full['biographyProjection'])),1)
        legacy=build_person_memory_model(entries)
        for key in legacy:
            if key not in {'biographyProjection','modelVersion'}:self.assertEqual(full[key],legacy[key],key)

    def test_unrelated_block_and_input_are_unchanged(self):
        _,rs,bs=scenario();other=_normalize_entry(fact(99,'我偶尔喝咖啡。'))
        frozen=deepcopy((rs,bs));before=blocks(build(rs[:2]+[other],bs));after=blocks(build(rs+[other],bs))
        self.assertEqual(next(b for b in before if '咖啡' in b['text']),next(b for b in after if '咖啡' in b['text']))
        self.assertEqual((rs,bs),frozen)

    def test_another_object_and_text_input_not_test_word_specific(self):
        _,rs,bs=scenario()
        for r in rs:
            r['narrative']=r['narrative'].replace('手摇音乐盒','陶瓷茶壶').replace('深蓝色','浅绿色')
            if r.get('object'):r['object']['label']='浅绿色陶瓷茶壶'
        self.assertEqual(len(blocks(build(rs,bs))),1)
        bs[rs[2]['memoryId']]=binding(rs[2],'text-followup','茶壶的补充')
        self.assertEqual(len(blocks(build(rs,bs))),1)

    def test_different_subject_or_entity_or_place_or_attribute_stays_separate(self):
        for field in ['subject','entity','place','color','another','new']:
            with self.subTest(field=field):
                _,rs,bs=scenario()
                if field=='subject':rs[2]['claimSubjectId']='other-owner'
                if field=='entity':
                    rs[0]['object']={'label':'手摇音乐盒','entityId':'box-A'};rs[2]['object']['entityId']='box-B'
                if field=='place':rs[2]['narrative']=rs[2]['narrative'].replace('玄关木柜上','书房木柜上')
                if field=='color':rs[2]['narrative']=rs[2]['narrative'].replace('深蓝色','红色');rs[2]['object']['label']='红色手摇音乐盒'
                if field=='another':rs[2]['narrative']=rs[2]['narrative'].replace('那个','另一个')
                if field=='new':rs[2]['narrative']=rs[2]['narrative'].replace('那个','新买的')
                self.assertEqual(len(blocks(build(rs,bs))),2)

    def test_two_possible_roots_do_not_choose_one(self):
        _,rs,bs=scenario();extra=deepcopy(rs[:2])
        for i,r in enumerate(extra):
            r['memoryId']='other-'+str(i);r['memoryVersionId']='other-v-'+str(i)
            r['citation'].update(memoryId=r['memoryId'],memoryVersionId=r['memoryVersionId'])
            bs[r['memoryId']]=binding(r,'other-root','另一件音乐盒')
        self.assertEqual(len(blocks(build(rs+extra,bs))),3)

    def test_invalid_binding_and_mixed_topic_not_used_as_target(self):
        _,rs,bs=scenario();bad=deepcopy(bs);bad[rs[1]['memoryId']]['contentHash']='invalid'
        self.assertEqual(len(blocks(build(rs,bad))),3)
        other=_normalize_entry(fact(99,'我把一个相框放在书桌上。'))
        bs[other['memoryId']]=binding(other,'live-root','手摇音乐盒：位置与颜色')
        self.assertEqual(len(blocks(build(rs+[other],bs))),2)

    def test_unsupported_reference_never_groups_by_shared_title_or_word(self):
        for text in ['我也喜欢手摇音乐盒。','它的声音很好听。','补充那个深蓝色手摇音乐盒：我每周日听音乐。','补充玄关木柜上的那个深蓝色手摇音乐盒和相框：我每周日整理它们。']:
            _,rs,bs=scenario();rs[2]['narrative']=text
            self.assertEqual(len(blocks(build(rs,bs))),2,text)

    def test_changed_or_removed_support_invalidates_and_keeps_exact_evidence(self):
        _,rs,bs=scenario();old=build(rs,bs);self.assertEqual(len(blocks(old)),1)
        for remaining in [rs[1:],rs[:1]+rs[2:],rs[:2]]:
            after=build(remaining,bs,old)
            self.assertCountEqual([e for b in blocks(after) for e in b['evidence']],[r['citation'] for r in remaining])
            self.assertNotIn(blocks(old)[0]['sourceRevision'],[b['sourceRevision'] for b in blocks(after)])
        changed=deepcopy(rs);changed[2]['memoryVersionId']='new-version';changed[2]['contentHash']='b'*64
        changed[2]['citation'].update(memoryVersionId='new-version',contentHash='b'*64)
        self.assertNotEqual(blocks(build(changed,bs,old))[0]['sourceRevision'],blocks(old)[0]['sourceRevision'])

    def test_many_additions_still_partition_with_exact_coverage(self):
        _,rs,bs=scenario();all_rs=rs[:2]
        for i in range(41):
            r=deepcopy(rs[2]);r['memoryId']='addition-'+str(i);r['memoryVersionId']='version-'+str(i)
            r['citation'].update(memoryId=r['memoryId'],memoryVersionId=r['memoryVersionId'])
            all_rs.append(r)
        out=blocks(build(all_rs,bs))
        self.assertEqual(len({b['groupId'] for b in out}),1)
        self.assertTrue(all(input_within_budget(b) for b in out))
        self.assertCountEqual([e for b in out for e in b['evidence']],[r['citation'] for r in all_rs])

    def test_large_ambiguous_target_bucket_is_bounded_and_faithful(self):
        _,rs,bs=scenario();all_rs=[rs[2]]
        for i in range(40):
            for original in rs[:2]:
                r=deepcopy(original);r['memoryId']+=str(i);r['memoryVersionId']+=str(i)
                r['citation'].update(memoryId=r['memoryId'],memoryVersionId=r['memoryVersionId'])
                bs[r['memoryId']]=binding(r,'root-'+str(i),'同名物件');all_rs.append(r)
        self.assertEqual(len(blocks(build(all_rs,bs))),41)

    def test_conflicting_identities_in_additions_are_not_collapsed(self):
        _,rs,bs=scenario()
        other=deepcopy(rs[2]);other['memoryId']='other-ref';other['memoryVersionId']='other-ref-version'
        other['citation'].update(memoryId=other['memoryId'],memoryVersionId=other['memoryVersionId'])
        rs[2]['object']['entityId']='object-A';other['object']['entityId']='object-B'
        bs[other['memoryId']]=binding(other,'separate-reference','补充')
        self.assertEqual(len(blocks(build(rs+[other],bs))),3)

    def test_reference_location_metadata_conflict_stays_separate(self):
        _,rs,bs=scenario();rs[2]['qualifiers']['place']['label']='书房木柜上'
        self.assertEqual(len(blocks(build(rs,bs))),2)
