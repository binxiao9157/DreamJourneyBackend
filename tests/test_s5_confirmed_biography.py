"""The device's complete synthetic facts, not a hand-simplified green fixture."""
from pathlib import Path
from copy import deepcopy
import inspect
import json
import unittest
from app.domain.owner_truth import person_biography as domain
from app.domain.owner_truth.person_memory_model import _normalize_entry
from test_s4_biography import fact

FIXTURE = Path(__file__).parent/'fixtures/s5_confirmed_baking.json'

def fixture():
    return json.loads(FIXTURE.read_text())

def binding(record, topic='new-topic', title='手工体验'):
    return dict(memoryId=record['memoryId'], memoryVersionId=record['memoryVersionId'],
                contentHash=record['contentHash'], sourceId=record['citation'].get('sourceId'),
                topicId=topic, topicVersion=1, proposalHash='a'*64,
                candidateId='synthetic-candidate', title=title)

def device_bindings(data):
    by_candidate={m['candidate_id']:m for m in data['members']}
    topics={x['id']:x for x in data['topics']}
    records={x['memoryId']:x for x in data['records']}
    result={}
    for f in data['formal']:
        v=f['version'];member=by_candidate[v['payload']['candidateId']]
        topic=topics[member['topic_id']]
        b=binding(records[f['memory']['id']],topic['id'],topic['payload']['theme']['title'])
        b.update(topicVersion=member['version'],proposalHash=topic['proposal_hash'],candidateId=member['candidate_id'])
        result[b['memoryId']]=b
    return result

def build(records, bindings, previous=None):
    # Same business assertion also executes the original production function:
    # before the repair, that function has no binding input and yields 33.
    kw={'bindings':bindings} if 'bindings' in inspect.signature(domain.build_biography).parameters else {}
    return domain.build_biography(records,'test-source',previous,**kw)

def blocks(doc):
    return [b for s in doc['sections'] for b in s['blocks']]

class ConfirmedBiographyTests(unittest.TestCase):
    def test_complete_device_33_facts_keep_groups_and_separate_sister(self):
        d=fixture();out=blocks(build(d['records'],device_bindings(d)))
        self.assertEqual(len(out),8)
        actual=[e for b in out for e in b['evidence']]
        self.assertCountEqual(actual,[r['citation'] for r in d['records']])
        self.assertEqual(sorted(len(b['sentences']) for b in out),[1,2,2,4,4,5,5,10])
        self.assertTrue(all(b['title']!='记忆片段' for b in out))
        sister=next(b for b in out if '我姐姐喜欢' in b['text'])
        self.assertEqual(len(sister['sentences']),1)
        self.assertNotIn('我自己更喜欢',sister['text'])

    def test_unlisted_activity_uses_verified_members_not_new_keywords(self):
        for texts in [('我用红线装订手工书。','封面由我用硬纸板制作。'),
                      ('我用小锉刀制作木制风铃。','风铃完成后，我打算送给朋友。')]:
            with self.subTest(texts=texts):
                rs=[_normalize_entry(fact(i+1,t)) for i,t in enumerate(texts)]
                bs={r['memoryId']:binding(r) for r in rs}
                self.assertEqual(len(blocks(build(rs,bs))),1)

    def test_invalid_binding_does_not_group_or_lose_facts(self):
        rs=[_normalize_entry(fact(i+1,t)) for i,t in enumerate(['我用红线装订手工书。','封面由我用硬纸板制作。'])]
        for key,value in [('memoryId','wrong'),('memoryVersionId','old'),('contentHash','wrong'),
                          ('sourceId','foreign'),('topicVersion',True),('proposalHash','bad'),
                          ('title',''),('title','x'*81)]:
            with self.subTest(key=key):
                bs={r['memoryId']:binding(r) for r in rs}
                bs[rs[1]['memoryId']][key]=value
                out=blocks(build(rs,bs))
                self.assertEqual(len(out),2)
                self.assertCountEqual([x for b in out for x in b['evidence']],[r['citation'] for r in rs])

    def test_equal_titles_do_not_join_different_confirmed_topics(self):
        rs=[_normalize_entry(fact(i+1,t)) for i,t in enumerate(['我用红线装订手工书。','封面由我用硬纸板制作。'])]
        self.assertEqual(len(blocks(build(rs,{r['memoryId']:binding(r,str(i)) for i,r in enumerate(rs)}))),2)

    def test_people_are_isolated_but_mentions_do_not_change_subject(self):
        texts=['我喜欢酥脆点心。','我想请姐姐评价点心。','我姐姐喜欢松软点心。']
        rs=[_normalize_entry(fact(i+1,t)) for i,t in enumerate(texts)]
        out=blocks(build(rs,{r['memoryId']:binding(r) for r in rs}))
        self.assertEqual(sorted(len(b['sentences']) for b in out),[1,2])
        rs[1]['claimSubjectId']='other-person'
        self.assertEqual(len(blocks(build(rs,{r['memoryId']:binding(r) for r in rs}))),3)

    def test_budget_partition_is_exact_stable_and_not_a_one_fact_split(self):
        rs=[_normalize_entry(fact(i+1,'我为手工书准备第'+str(i+1)+'页。')) for i in range(41)]
        bs={r['memoryId']:binding(r) for r in rs}
        doc=build(rs,bs);out=blocks(doc)
        self.assertGreater(len(out),1);self.assertLess(len(out),len(rs))
        self.assertTrue(all(domain.input_within_budget(b) for b in out))
        self.assertCountEqual([e for b in out for e in b['evidence']],[r['citation'] for r in rs])
        self.assertEqual(doc,build(list(reversed(rs)),bs))
        self.assertEqual(doc,build(rs,bs,doc))
        self.assertEqual({b['groupId'] for b in out},{out[0]['groupId']})

    def test_serialized_budget_counts_evidence_and_keeps_one_oversized_fact(self):
        rs=[_normalize_entry(fact(i+1,'我用红线装订第'+str(i+1)+'册手工书。')) for i in range(3)]
        for r in rs:r['citation']['sourceId']='x'*3000
        out=blocks(build(rs,{r['memoryId']:binding(r) for r in rs}))
        self.assertEqual(len(out),3)
        self.assertTrue(all(domain.input_within_budget(b) for b in out))
        rs[0]['narrative']='内容'*9000
        out=blocks(build(rs,{r['memoryId']:binding(r) for r in rs}))
        oversized=next(b for b in out if not domain.input_within_budget(b))
        self.assertEqual(len(oversized['sentences']),1)
        self.assertEqual(oversized['sentences'][0]['text'],rs[0]['narrative'])

    def test_binding_only_change_invalidates_local_block_not_other_topic(self):
        d=fixture();rs=d['records'];bs=device_bindings(d);old=build(rs,bs)
        one=next(iter(bs));target=bs[one]['topicId']
        new_bs=deepcopy(bs)
        for b in new_bs.values():
            if b['topicId']==target:b['topicVersion']+=1;b['proposalHash']='b'*64
        changed=build(rs,new_bs,old)
        affected={b['topicId'] for b in blocks(old) if any(e['memoryId']==one for e in b['evidence'])}
        before={b['topicId']:b for b in blocks(old)}
        for b in blocks(changed):
            if b['topicId'] in affected:self.assertNotEqual(b['sourceRevision'],before[b['topicId']]['sourceRevision'])
            elif not any(bs[e['memoryId']]['topicId']==target for e in b['evidence']):self.assertEqual(b,before[b['topicId']])
        self.assertEqual(changed,build(rs,new_bs))

    def test_binding_input_is_biography_only_and_incremental_matches_full(self):
        from app.domain.owner_truth.person_memory_model import build_person_memory_model,build_person_memory_model_incremental
        entries=[fact(1,'我用红线装订手工书。'),fact(2,'封面由我用硬纸板制作。')]
        rs=[_normalize_entry(x) for x in entries];bs={r['memoryId']:binding(r) for r in rs}
        before=build_person_memory_model(entries,biography_topics=True)
        after=build_person_memory_model(entries,biography_topics=True,biography_bindings=bs)
        inc=build_person_memory_model_incremental(previous_model=before,entries=entries,biography_topics=True,biography_bindings=bs)
        self.assertEqual(after['biographyProjection'],inc['biographyProjection'])
        for key in before:
            if key not in {'biographyProjection','modelVersion'}:self.assertEqual(before[key],after[key],key)

    def test_removing_member_or_binding_never_reuses_its_old_text(self):
        d=fixture();rs=d['records'];bs=device_bindings(d);old=build(rs,bs)
        after=build(rs[1:],bs,old)
        self.assertNotIn(rs[0]['memoryVersionId'],json.dumps(after))
        one=rs[1];wrong=deepcopy(bs);wrong[one['memoryId']]['memoryVersionId']='old'
        out=blocks(build(rs[1:],wrong,after))
        self.assertTrue(any(len(b['sentences'])==1 and b['evidence'][0]['memoryId']==one['memoryId'] for b in out))

if __name__=='__main__':unittest.main()
