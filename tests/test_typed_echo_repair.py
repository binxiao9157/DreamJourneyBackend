from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
import unittest
from app.services.owner_truth_live_memory_support import validate_live_memory_support
from app.services.owner_truth_live_memory_contract_errors import LiveMemoryContractFailure
from app.domain.owner_truth.ontology import reextract_owner_corrected_memory_payload,validate_memory_payload
from app.domain.owner_truth.contracts import MemoryKind
from tests.test_owner_truth_live_long_memory_pipeline import _typed_memory

FACT='我最近开始每周日做水彩临摹。'
THANKS='谢谢，我没有其他内容了。'
def review(acts,memories):
 return dict(schemaVersion='owner-truth-live-memory-support-v1',turnAssessments=[dict(turnIndex=i,speechAct=a) for i,a in acts],memoryAssessments=[dict(memoryIndex=i,verdict='supported',supportingTurnIndices=m['sourceTurnIndices']) for i,m in enumerate(memories)],omittedFactBearingTurnIndices=[])
class SupportTests(unittest.TestCase):
 def setUp(self):
  self.turns=[dict(index=1,role='user',text=FACT),dict(index=2,role='assistant',text='好的'),dict(index=3,role='user',text=THANKS)]
  self.memories=[_typed_memory(FACT,[1])]
 def test_fact_and_conversation_control_preserve_exact_fact(self):
  self.assertEqual(validate_live_memory_support(turns=self.turns,memories=self.memories,review=review([(1,'assertion'),(3,'conversationControl')],self.memories)),self.memories)
 def test_pure_control_has_no_fact_requirement(self):
  self.assertEqual(validate_live_memory_support(turns=[self.turns[2]],memories=[],review=review([(3,'conversationControl')],[])),[])
 def test_real_fact_omission_still_rejected(self):
  self.turns[2]['text']='我的水彩老师姓林。'
  with self.assertRaisesRegex(LiveMemoryContractFailure,'factWithoutFinalDraft'):
   validate_live_memory_support(turns=self.turns,memories=self.memories,review=review([(1,'assertion'),(3,'assertion')],self.memories))
 def test_control_cannot_support_invented_fact(self):
  self.memories[0]['sourceTurnIndices']=[3]
  with self.assertRaises(LiveMemoryContractFailure):validate_live_memory_support(turns=self.turns,memories=self.memories,review=review([(1,'query'),(3,'conversationControl')],self.memories))
 def test_missing_turn_and_wrong_evidence_still_rejected(self):
  for rev in [review([(1,'assertion')],self.memories),review([(1,'assertion'),(3,'conversationControl')],self.memories)]:
   rev['memoryAssessments'][0]['supportingTurnIndices']=[2]
   with self.assertRaises(LiveMemoryContractFailure):validate_live_memory_support(turns=self.turns,memories=self.memories,review=rev)
 def test_mixed_control_correction_emotion_remains_fact(self):
  self.turns[2]['text']='谢谢，更正一下，我现在每周五练水彩，练完很开心。'
  self.memories.append(_typed_memory(self.turns[2]['text'],[3]))
  assessment=review([(1,'assertion'),(3,'correction')],self.memories)
  assessment['memoryAssessments'][0].update(verdict='superseded',supportingTurnIndices=[])
  self.assertEqual(validate_live_memory_support(turns=self.turns,memories=self.memories,review=assessment),[self.memories[1]])

class ThemeCommandTests(unittest.TestCase):
 def check(self,kind,key):
  import app.main as main
  from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
  from app.domain.owner_truth.live_topics import LiveThemeConflict
  from app.domain.owner_truth.ontology import enrich_memory_payload_v5
  old='我每周二参加模型拼装。';new='我每周五参加模型拼装。'
  primary={'experience':'event','knowledge':'statement','emotion':'expression'}[kind]
  source={primary:old}
  if kind=='experience':source.update(time=None,location=None,participants=[],actions=[],outcome=None)
  if kind=='knowledge':source.update(knowledgeType='personal_fact',domains=[])
  if kind=='emotion':source.update(label='开心',intensity=None,trigger=None)
  content=enrich_memory_payload_v5(kind=MemoryKind(kind),payload=source,provenance={'mode':'selfReport'},memory_subject_id='owner',claim_subject_id='owner')
  current=dict(topicId='b81e342d-210a-4ab0-9a93-440f88a5c7e1',version=1,proposalHash='a'*64,members={'atom':dict(candidateId='a81e342d-210a-4ab0-9a93-440f88a5c7e1',proposalHash='b'*64)})
  class Topics:
   def lock_visible_revision(self,**kw):return current
   def prepare_primary_edits(self,*,revision,edits):return {a:{primary:t.strip()} for a,t in edits.items()}
  class Store:
   @contextmanager
   def request_unit_of_work(self,**kw):yield
   def owner_truth_live_topic_repository(self):return Topics()
  context=OwnerTruthCommandContext(vault_id='vault',owner_subject_id='owner',actor_subject_id='owner')
  with patch.object(main,'store',Store()),patch.object(main,'settings',SimpleNamespace(owner_truth_live_recovery_enabled=True)),patch.object(main,'_owner_truth_direct_candidate_review_context',return_value=context):
   _,command=main._live_theme_group_command(None,'vault','b81e342d-210a-4ab0-9a93-440f88a5c7e1',dict(commandId='edit',version=1,proposalHash='a'*64,primaryEdits={'atom':new}),confirmation=False)
  value=command.selections[0].corrected_value
  repaired=reextract_owner_corrected_memory_payload(kind=MemoryKind(kind),source_payload=content,corrected_payload=value)
  self.assertTrue(validate_memory_payload(kind=MemoryKind(kind),payload=repaired,schema_version='owner-truth-v5').accepted,kind)
  self.assertEqual(value,{key:new})
 def test_experience_edit(self):self.check('experience','event')
 def test_knowledge_edit(self):self.check('knowledge','statement')
 def test_emotion_edit(self):self.check('emotion','expression')



class ThemeEditBindingTests(unittest.TestCase):
    def prepare(self, kind, text, *, binding_hash='b' * 64, content=None):
        from app.services.owner_truth_live_topics import PostgresLiveTopicRepository
        from app.domain.owner_truth.ontology import enrich_memory_payload_v5
        candidate_id = 'a81e342d-210a-4ab0-9a93-440f88a5c7e1'
        primary = {'experience': 'event', 'knowledge': 'statement', 'emotion': 'expression'}.get(kind, 'statement')
        if content is None:
            content = enrich_memory_payload_v5(
                kind=MemoryKind(kind), payload={primary: '我每周二练习。', 'label': '开心'},
                provenance={'mode': 'selfReport'}, memory_subject_id='owner', claim_subject_id='owner',
            )
        row = {'id': candidate_id, 'payload': {
            'candidateKind': kind, 'proposalHash': 'b' * 64, 'content': content,
        }}
        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def execute(self, query, args): self.ids = args[0]
            def fetchall(self): return [row] if candidate_id in self.ids else []
        class Connection:
            def cursor(self, **kwargs): return Cursor()
        repo = PostgresLiveTopicRepository(Connection())
        revision = {'members': {'a': {'candidateId': candidate_id, 'proposalHash': binding_hash}}}
        return repo.prepare_primary_edits(revision=revision, edits={'a': text})['a'], content

    def test_bound_kind_selects_primary_field(self):
        for kind, field in [('experience', 'event'), ('knowledge', 'statement'), ('emotion', 'expression')]:
            with self.subTest(kind=kind):
                value, _ = self.prepare(kind, '我每周五练习。')
                self.assertEqual(value, {field: '我每周五练习。'})

    def test_noop_edit_remains_complete_and_valid(self):
        for kind in ['experience', 'knowledge', 'emotion']:
            with self.subTest(kind=kind):
                value, content = self.prepare(kind, '  我每周二练习。  ')
                self.assertTrue(validate_memory_payload(kind=MemoryKind(kind), payload=value, schema_version='owner-truth-v5').accepted)
                again = reextract_owner_corrected_memory_payload(kind=MemoryKind(kind), source_payload=content, corrected_payload=value)
                self.assertTrue(validate_memory_payload(kind=MemoryKind(kind), payload=again, schema_version='owner-truth-v5').accepted)

    def test_mismatched_candidate_binding_is_rejected(self):
        from app.domain.owner_truth.live_topics import LiveThemeConflict
        with self.assertRaisesRegex(LiveThemeConflict, 'themeCandidateBindingMismatch'):
            self.prepare('experience', '更正', binding_hash='c' * 64)

    def test_unknown_kind_is_rejected(self):
        from app.domain.owner_truth.live_topics import LiveThemeConflict
        with self.assertRaisesRegex(LiveThemeConflict, 'themeCandidateKindInvalid'):
            self.prepare('unknown', '更正', content={'statement': '旧内容'})

    def test_changing_primary_does_not_copy_stale_facets(self):
        value, _ = self.prepare('experience', '我每周五练习。', content={
            'event': '我每周二练习。', 'location': '旧地点', 'participants': ['旧人物'],
        })
        self.assertEqual(value, {'event': '我每周五练习。'})


if __name__ == '__main__':
    unittest.main()
