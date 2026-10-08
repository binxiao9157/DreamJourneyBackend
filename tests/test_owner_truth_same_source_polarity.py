"""Sanitized captured group; deterministic rules, no Provider or network."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import unittest
from uuid import uuid4
from app.domain.owner_truth.candidate_decisions import OwnerTruthCandidateSnapshot,CandidateReviewAction
from app.domain.owner_truth.memory_changeset import OwnerTruthCurrentFormalMemory,build_memory_changeset_proposal
from app.domain.owner_truth.memory_changeset_group import OwnerTruthMemoryChangeSetGroupCommand,OwnerTruthMemoryChangeSetGroupSelection,OwnerTruthMemoryChangeSetGroupDependency
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_memory_changeset_group_review import OwnerTruthMemoryChangeSetGroupReviewService
from tests.test_owner_truth_memory_changeset_group_review import _Store
ROWS=json.loads((Path(__file__).parent/'fixtures/same_source_polarity_group.json').read_text())
def digest(v):return sha256(json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def candidate(index):
 row=ROWS[index];p=deepcopy(row['payload'])
 return OwnerTruthCandidateSnapshot(candidate_id=row['id'],vault_id='polarity-vault',owner_subject_id='polarity-owner',source_id=p['evidenceRefs'][0]['sourceId'],memory_kind=p['candidateKind'],perspective_type=p['perspectiveType'],epistemic_status=p['epistemicStatus'],sensitivity=p['sensitivity'],decision='pending',policy_version='owner-truth-v5',authority_epoch=0,row_version=1,content_hash=digest(p['content']),content_schema_version=p['contentSchemaVersion'],payload=p)
def current(c):
 return OwnerTruthCurrentFormalMemory(memory_id=str(uuid4()),memory_version_id=str(uuid4()),vault_id=c.vault_id,owner_subject_id=c.owner_subject_id,version_number=1,memory_kind=c.memory_kind,content_schema_version=c.content_schema_version,content=c.content,evidence_refs=c.source_refs)
def changed(c,fn):
 p=deepcopy(c.payload);fn(p)
 return replace(c,payload=p,content_hash=digest(p['content']),source_id=p['evidenceRefs'][0]['sourceId'])
def preview(c,t):return build_memory_changeset_proposal(candidate=c,current_memories=(t,),base_memory_revision=1).payload()['operations'][0]
def prepare_group():
 cs=[candidate(i) for i in range(23)];store=_Store()
 for c in cs:store.repository.seed(c)
 ctx=OwnerTruthCommandContext(vault_id=cs[0].vault_id,owner_subject_id=cs[0].owner_subject_id,actor_subject_id=cs[0].owner_subject_id,policy_version='owner-truth-v5')
 selections=tuple(OwnerTruthMemoryChangeSetGroupSelection(candidate_id=c.candidate_id,expected_candidate_version=1,action=CandidateReviewAction.ACCEPT,corrected_value=None,corrected_value_schema_version=None,reason_code='ownerReviewed') for c in cs)
 deps=tuple(OwnerTruthMemoryChangeSetGroupDependency(before_candidate_id=a.candidate_id,after_candidate_id=b.candidate_id) for a,b in zip(cs,cs[1:]))
 cmd=OwnerTruthMemoryChangeSetGroupCommand(command_id='polarity-preview',selections=selections,dependencies=deps)
 svc=OwnerTruthMemoryChangeSetGroupReviewService(store);p=svc.preview(command=cmd,context=ctx)
 commit=replace(cmd,command_id='polarity-confirm',expected_memory_revision=p.base_memory_revision,expected_group_proposal_id=p.proposal_id,expected_group_proposal_hash=p.proposal_hash)
 return store,svc,ctx,p,commit
class SameSourcePolarityTests(unittest.TestCase):
 def test_real_pair_both_orders_add_evidence_and_preserve_target(self):
  for a,b in [(8,0),(0,8)]:
   t=current(candidate(b));o=preview(candidate(a),t)
   self.assertEqual(o['operationKind'],'addEvidence')
   self.assertEqual(o['factDiff']['before'],o['factDiff']['after'])
   self.assertEqual(o['addedEvidenceCount'],1)
 def test_same_evidence_is_duplicate(self):
  t=current(candidate(0));c=changed(candidate(8),lambda p:p.update(evidenceRefs=list(t.evidence_refs)))
  self.assertEqual(preview(c,t)['operationKind'],'duplicate')
 def test_fallback_all_polarities_have_actual_diffs(self):
  for old in ['unknown','positive','negative','neutral']:
   for new in ['unknown','positive','negative','neutral']:
    if old==new:continue
    t=current(changed(candidate(0),lambda p:p['content']['qualifiers'].update(polarity=old)))
    c=changed(candidate(8),lambda p:p['content']['qualifiers'].update(polarity=new))
    c=changed(c,lambda p:p['evidenceRefs'][0].update(sourceId=str(uuid4())))
    o=preview(c,t)
    if o['operationKind']=='dispute':
     dif=o['factDiff']['changedFields'];self.assertTrue(dif,(old,new))
     self.assertTrue(any(x['before']!=x['after'] for x in dif),(old,new))
     self.assertEqual([x for x in o['changedFields'] if x!='evidenceRefs'],[x['path'] for x in dif])
 def test_fallback_includes_other_business_changes(self):
  c=changed(candidate(8),lambda p:p['content'].update(summary='我也记录叶子的颜色。'))
  o=preview(c,current(candidate(0)))
  self.assertEqual(o['operationKind'],'dispute')
  self.assertIn('summary',o['changedFields']);self.assertIn('qualifiers.polarity',o['changedFields'])
  self.assertNotIn('sourceTurnIndices',o['changedFields'])
 def test_known_opposition_stays_dispute(self):
  t=current(changed(candidate(0),lambda p:p['content']['qualifiers'].update(polarity='negative')))
  o=preview(candidate(8),t);self.assertEqual(o['operationKind'],'dispute');self.assertTrue(o['factDiff']['changedFields'])
 def test_new_exception_does_not_cross_boundaries(self):
  mutations=[lambda p:p['evidenceRefs'][0].update(sourceId=str(uuid4())),lambda p:p['evidenceRefs'][0].update(sourceVersion=2),lambda p:p['evidenceRefs'][0].pop('span'),lambda p:p['content']['qualifiers'].update(place={'label':'书房','entityId':None,'category':None}),lambda p:p['content']['qualifiers']['validTime'].update(expression='2027年',precision='year'),lambda p:p['content'].update(claimSubjectId='other',memorySubjectId='other'),lambda p:p.update(reviewMode='correction')]
  for fn in mutations:self.assertNotIn(preview(changed(candidate(8),fn),current(candidate(0)))['operationKind'],['duplicate','addEvidence'])
 def test_23_members_preview_commit_and_replay(self):
  store,svc,ctx,p,cmd=prepare_group()
  self.assertEqual(p.members[8].proposal.change_set.operation.kind.value,'addEvidence')
  before=p.members[8].proposal.payload()['operations'][0]['factDiff']
  self.assertEqual(before['before'],before['after'])
  result=svc.confirm(command=cmd,context=ctx)
  self.assertEqual(len(result.members),23)
  self.assertEqual(result.members[0].memory_id,result.members[8].memory_id)
  self.assertEqual(result.members[8].activation_outcome,'revised')
  rev=store.repository.memory_revision(context=ctx)
  self.assertEqual(svc.confirm(command=cmd,context=ctx).outcome,'deduplicated')
  self.assertEqual(store.repository.memory_revision(context=ctx),rev)
