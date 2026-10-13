import copy
import json
import unittest
from unittest.mock import patch
from app.core.config import Settings
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_context_materialization import OwnerTruthContextMaterializationService as Material
from app.services.formal_memory_conversation_snapshot import FormalMemoryConversationSnapshotService, bind_provider_role_text
from app.services.deepseek import DeepSeekEchoAnswerProxy


def entry(text='我每周五傍晚用软布擦拭它', label='它', version='v-fragment', source='source-wind', span=None):
    return dict(memoryVersionId=version, memoryKind='experience', contentSchemaVersion='owner-truth-v4',
        sourceId=source, sourceVersion=1,
        content=dict(statement=text,event=text,object=dict(label=label),memorySubjectId='owner',claimSubjectId='owner'),
        evidenceRefs=[dict(sourceId=source,sourceVersion=1,span=span or dict(start=0,end=31))])

class Store:
    def __init__(self, entries):self.entries=entries
    def owner_truth_memory_projection_repository(self):return self
    def read(self, *, context):return dict(state='ready',rightsState='active',authorityEpoch=0,memoryRevision=1,checkpoint='fixture',entries=self.entries,personMemoryModel={})

class BusinessBoundaryTests(unittest.TestCase):
    def test_typed_keeps_object_boundary_for_orphan_fragment(self):
        rendered=Material._render_entry(entry=entry(),citation=dict(memoryVersionId='v-fragment'))
        self.assertIn('referenceContext',rendered)
        self.assertIn('referential',rendered)
        self.assertIn('我每周五傍晚用软布擦拭它',rendered)

    def test_typed_has_evidence_scope_without_reading_source(self):
        rendered=Material._render_entry(entry=entry(),citation=dict(memoryVersionId='v-fragment'))
        self.assertIn('scopes',rendered)

    def test_live_carries_the_same_reference_boundary(self):
        service=FormalMemoryConversationSnapshotService(Store([entry()]))
        result=service.build(context=OwnerTruthCommandContext(vault_id='vault',owner_subject_id='owner',actor_subject_id='owner'))
        self.assertIn('referenceContext',result['coreFacts'][0])
        bound=bind_provider_role_text(result,system_role='你是助手',speaking_style='自然')
        self.assertIn('referenceContext',bound['providerRoleText'])

    def test_semantic_group_keeps_reference_boundary(self):
        rendered=Material._render_semantic_group(group=dict(memoryKind='experience',narrative='我每周五傍晚用软布擦拭它'),group_id='test-group')
        self.assertIn('referenceContext',rendered)

    def test_merged_group_keeps_each_objects_evidence_separate(self):
        wind = entry()
        glass = entry('我每周日早上擦拭沙漏', '沙漏', 'v-glass', 'source-glass')
        rendered = Material._render_semantic_group(
            group=dict(memoryKind='experience', narrative='周五擦拭它，周日擦拭沙漏'),
            group_id='two-objects', reference_entries=[wind, glass])
        rows = [json.loads(line) for line in rendered.splitlines() if line.startswith('{')]
        self.assertEqual([row['statement'] for row in rows], [wind['content']['statement'], glass['content']['statement']])
        self.assertNotEqual(rows[0]['referenceContext']['scopes'], rows[1]['referenceContext']['scopes'])
        self.assertEqual(rows[0]['referenceContext']['objectLabel'], '它')
        self.assertEqual(rows[1]['referenceContext']['objectLabel'], '沙漏')

    def test_typed_rule_blocks_cross_record_pronoun_transfer(self):
        req=DeepSeekEchoAnswerProxy(Settings()).build_request(query='我什么时候擦拭沙漏？',generation_context=Material._render_entry(entry=entry(),citation=dict(memoryVersionId='v-fragment')),persona_scope='personal')
        self.assertIn('不能把孤立代词',req['json']['messages'][0]['content'])

    def test_live_rule_survives_dynamic_fact_filter(self):
        result=FormalMemoryConversationSnapshotService(Store([entry(),entry('补充浅绿色风铃','浅绿色风铃','v-anchor')])).build(context=OwnerTruthCommandContext(vault_id='vault',owner_subject_id='owner',actor_subject_id='owner'))
        result['coreFacts']=[f for f in result['coreFacts'] if f['sourceMemoryVersionIds']==['v-fragment']]
        bound=bind_provider_role_text(result,system_role='助手',speaking_style='自然')
        self.assertIn('不能把孤立代词',bound['providerRoleText'])
        self.assertNotIn('浅绿色风铃',bound['providerRoleText'])


class ReferenceScopeTests(unittest.TestCase):
    def scope(self,e):
        from app.services.echo_reference_grounding import reference_context
        return reference_context(e)

    def test_same_evidence_supports_shared_scope_without_rewriting(self):
        a=entry(); b=entry('补充浅绿色风铃','浅绿色风铃','anchor');before=copy.deepcopy(a)
        self.assertEqual(self.scope(a)['scopes'],self.scope(b)['scopes'])
        self.assertEqual(a,before)
        self.assertEqual(self.scope(a)['objectLabel'],'它')

    def test_different_sources_versions_spans_turns_subjects_do_not_share(self):
        a=entry();expected=self.scope(a)['scopes']
        for kind in ['source','version','span','subject','turn']:
            b=copy.deepcopy(a)
            if kind=='source':b['evidenceRefs'][0]['sourceId']='other'
            elif kind=='version':b['evidenceRefs'][0]['sourceVersion']=2
            elif kind=='span':b['evidenceRefs'][0]['span']['end']=32
            elif kind=='subject':b['content']['claimSubjectId']='another-person'
            else:b['evidenceRefs'][0].pop('span');b['evidenceRefs'][0]['turnId']='turn-3'
            self.assertNotEqual(expected,self.scope(b)['scopes'],kind)

    def test_missing_or_invalid_locator_is_never_a_source_wide_scope(self):
        for refs in [[],None,[{'sourceId':'x','sourceVersion':1}], [{'sourceId':'x','sourceVersion':True,'span':{'start':0,'end':31}}], [{'sourceId':'x','sourceVersion':1,'span':{'start':3,'end':2}}]]:
            a=entry();a['evidenceRefs']=refs
            self.assertEqual(self.scope(a)['scopes'],[])
        a=entry();a['evidenceRefs']*=5;self.assertEqual(self.scope(a)['scopes'],[])

    def test_multi_object_source_does_not_assign_antecedent(self):
        a=entry('我把风铃和沙漏放在一起，周五擦拭它们','它们')
        self.assertEqual(self.scope(a)['objectLabel'],'它们')
        self.assertNotIn('resolvedObject',self.scope(a))

    def test_byte_budget_still_preserves_whole_qualified_facts(self):
        rows=[entry('我每周五傍晚擦拭它，不用水。','它','v-'+str(i),'s-'+str(i)) for i in range(150)]
        snapshot=FormalMemoryConversationSnapshotService(Store(rows)).build(context=OwnerTruthCommandContext(vault_id='vault',owner_subject_id='owner',actor_subject_id='owner'))
        bound=bind_provider_role_text(snapshot,system_role='助手',speaking_style='自然',max_bytes=12000)
        self.assertLessEqual(len(bound['providerRoleText'].encode()),12000)
        self.assertTrue(0 < len(bound['coreFacts']) < 150)
        for f in bound['coreFacts']:
            self.assertEqual(f['statement'],'我每周五傍晚擦拭它，不用水。')
            self.assertTrue(f['referenceContext']['referential'])
        self.assertEqual(len(snapshot['coreFacts']),150)

class RecordedV5MaterializationTests(unittest.TestCase):
    def fixture(self):
        from pathlib import Path
        return json.loads((Path(__file__).parent/'fixtures/echo_cross_object.json').read_text())

    def test_all_eight_actual_v5_facts_keep_original_citations_and_boundaries(self):
        x=self.fixture();before=copy.deepcopy(x)
        cites,result=Material(None)._materialize_selected_context(projection=x['projection'],selected_context=x['selected'])
        self.assertEqual(len(cites),8)
        self.assertEqual({v['memoryVersionId'] for v in cites},{v['memoryVersionId'] for v in x['selected']})
        self.assertIn('我每周五傍晚用软布擦拭它',result['text'])
        self.assertIn('"objectLabel":"它"',result['text'])
        self.assertNotIn('浅绿色陶瓷风铃',result['text']) # unselected anchor must not be read implicitly
        self.assertEqual(x,before)

    def test_budget_never_cites_unsent_groups(self):
        x=self.fixture()
        with patch('app.services.owner_truth_context_materialization.OWNER_TRUTH_CONTEXT_MATERIALIZATION_MAX_CHARS',650):
            cites,result=Material(None)._materialize_selected_context(projection=x['projection'],selected_context=x['selected'])
        self.assertTrue(result['truncated'])
        self.assertTrue(0<len(cites)<8)
        self.assertEqual(len(cites),result['sourceCount']) # fixture contains single-member semantic groups
        self.assertLessEqual(len(result['text']),650)

    def test_nonreferential_answer_does_not_gain_extra_instruction(self):
        e=entry('我每周二晚上练口琴。','口琴')
        text=Material._render_entry(entry=e,citation={'memoryVersionId':'v'})
        req=DeepSeekEchoAnswerProxy(Settings()).build_request(query='什么时候练口琴？',generation_context=text,persona_scope='personal')
        self.assertNotIn('不能把孤立代词',req['json']['messages'][0]['content'])

class OrdinaryContextCompatibilityTests(unittest.TestCase):
    def test_actual_materialization_without_dependency_keeps_old_wire_text(self):
        from pathlib import Path
        x=json.loads((Path(__file__).parent/'fixtures/echo_cross_object.json').read_text())
        # The sandbox fixture's sandglass habit is explicit and has no orphan pronoun.
        target=next(e for e in x['projection']['entries'] if e['content']['statement'].startswith('我每周日早上'))
        # This one includes 那个: use the same valid group identity with plain explicit text.
        target['content']['statement']='我每周二练习口琴。';target['content']['event']='我每周二练习口琴。'
        target['content']['semantic']['narrative']='我每周二练习口琴。'
        from app.domain.owner_truth.person_memory_model import build_person_memory_model
        x['projection']['entries']=[target];x['projection']['personMemoryModel']=build_person_memory_model([target])
        selected=[s for s in x['selected'] if s['memoryVersionId']==target['memoryVersionId']]
        cites,result=Material(None)._materialize_selected_context(projection=x['projection'],selected_context=selected)
        self.assertNotIn('referenceContext',result['text'])
        self.assertEqual(len(cites),1)
        req=DeepSeekEchoAnswerProxy(Settings()).build_request(query='我什么时候练口琴？',generation_context=result['text'],persona_scope='personal')
        self.assertNotIn('不能把孤立代词',req['json']['messages'][0]['content'])

    def test_live_plain_facts_keep_old_wire_column_count(self):
        s=FormalMemoryConversationSnapshotService(Store([entry('沙漏是琥珀色的。','沙漏')])).build(context=OwnerTruthCommandContext(vault_id='vault',owner_subject_id='owner',actor_subject_id='owner'))
        role=bind_provider_role_text(s,system_role='助手',speaking_style='自然')['providerRoleText']
        self.assertNotIn('referenceContext',role)
        self.assertNotIn('不能把孤立代词',role)

if __name__ == '__main__':
    unittest.main()
