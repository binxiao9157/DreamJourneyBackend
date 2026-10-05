"""Real-provider synthetic payload replay; identifiers remapped, no network calls."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import unittest
from uuid import uuid4

from app.domain.owner_truth.candidate_decisions import OwnerTruthCandidateSnapshot, CandidateReviewAction, OwnerTruthCandidateReviewError
from app.domain.owner_truth.contracts import CandidateDecision, EpistemicStatus, MemoryKind, PerspectiveType, SensitivityLevel
from app.domain.owner_truth.memory_changeset import OwnerTruthCurrentFormalMemory, build_memory_changeset
from app.domain.owner_truth.memory_changeset_activation import build_memory_changeset_activation_plan
from app.domain.owner_truth.memory_changeset_group import OwnerTruthMemoryChangeSetGroupCommand, OwnerTruthMemoryChangeSetGroupSelection, OwnerTruthMemoryChangeSetGroupDependency
from app.domain.owner_truth.ontology import enrich_memory_payload_v4
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_memory_changeset_group_review import OwnerTruthMemoryChangeSetGroupReviewService
from tests.test_owner_truth_memory_changeset_group_review import _Store

ROWS = json.loads((Path(__file__).parent / "fixtures/same_source_repetition.json").read_text())

def candidate(index=1):
    row = deepcopy(ROWS[index]); payload = row['payload']
    return OwnerTruthCandidateSnapshot(
        candidate_id=row['id'], vault_id='local-repetition', owner_subject_id='local-owner',
        source_id=payload['evidenceRefs'][0]['sourceId'], memory_kind=MemoryKind(payload['candidateKind']),
        perspective_type=PerspectiveType.FIRST_PERSON, epistemic_status=EpistemicStatus.RECALLED,
        sensitivity=SensitivityLevel.STANDARD, decision=CandidateDecision.PENDING,
        policy_version='owner-truth-v5', authority_epoch=0,row_version=1,
        content_hash=sha256(json.dumps(payload['content'],ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
        content_schema_version='owner-truth-v5',payload=payload)

def current(index=0):
    c=candidate(index)
    return OwnerTruthCurrentFormalMemory(memory_id=str(uuid4()),memory_version_id=str(uuid4()),
        vault_id=c.vault_id,owner_subject_id=c.owner_subject_id,version_number=1,memory_kind=c.memory_kind,
        content_schema_version=c.content_schema_version,content=c.content,evidence_refs=c.source_refs)

def changed(c, mutate):
    payload=deepcopy(c.payload); mutate(payload)
    if payload['content'].get('statement') != c.content.get('statement'):
        payload['content']['semantic'] = enrich_memory_payload_v4(kind=c.memory_kind,payload=payload['content'])['semantic']
    return replace(c,payload=payload,source_id=payload.get('evidenceRefs',[{}])[0].get('sourceId',c.source_id) if payload.get('evidenceRefs') else c.source_id,content_hash=sha256(json.dumps(payload['content'],ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest())

def kind(c,t):
    return build_memory_changeset(candidate=c,current_memories=(t,),base_memory_revision=1).operation.kind.value

def prepare_group(store):
    cs=[candidate(0),candidate(1)]
    for c in cs:store.repository.seed(c)
    ctx=OwnerTruthCommandContext(vault_id=cs[0].vault_id,owner_subject_id=cs[0].owner_subject_id,actor_subject_id=cs[0].owner_subject_id,policy_version=cs[0].policy_version)
    selections=tuple(OwnerTruthMemoryChangeSetGroupSelection(candidate_id=c.candidate_id,expected_candidate_version=1,action=CandidateReviewAction.ACCEPT,corrected_value=None,corrected_value_schema_version=None,reason_code='ownerReviewed') for c in cs)
    cmd=OwnerTruthMemoryChangeSetGroupCommand(command_id='repetition-preview',selections=selections,dependencies=(OwnerTruthMemoryChangeSetGroupDependency(before_candidate_id=cs[0].candidate_id,after_candidate_id=cs[1].candidate_id),))
    svc=OwnerTruthMemoryChangeSetGroupReviewService(store); p=svc.preview(command=cmd,context=ctx)
    confirm=replace(cmd,command_id='repetition-confirm',expected_memory_revision=p.base_memory_revision,expected_group_proposal_id=p.proposal_id,expected_group_proposal_hash=p.proposal_hash)
    return svc,ctx,p,confirm

class SameSourceRepetitionTests(unittest.TestCase):
    def test_captured_applicability_and_missing_fields_only_add_evidence(self):
        self.assertEqual(kind(candidate(1),current(0)), 'addEvidence')

    def test_captured_scenario_suffix_only_adds_evidence(self):
        self.assertEqual(kind(candidate(3),current(2)), 'addEvidence')
        self.assertEqual(kind(candidate(5),current(4)), 'addEvidence')

    def test_existing_evidence_is_duplicate_even_with_extractor_drift(self):
        t=current(); c=changed(candidate(),lambda p:p.update(evidenceRefs=list(t.evidence_refs)))
        self.assertEqual(kind(c,t),'duplicate')

    def test_activation_preserves_reviewed_content_and_both_source_spans(self):
        c=replace(candidate(),decision=CandidateDecision.ACCEPTED,row_version=2);t=current()
        plan=build_memory_changeset_activation_plan(candidate=c,receipt_id=str(uuid4()),receipt_decision=CandidateDecision.ACCEPTED,receipt_after_hash=c.content_hash,current_memories=(t,),base_memory_revision=1)
        self.assertEqual(plan.change_set.operation.kind.value,'addEvidence')
        self.assertEqual(plan.memory_id,t.memory_id);self.assertEqual(plan.memory_version,2)
        actual=deepcopy(plan.payload['content']);expected=deepcopy(t.typed_content)
        actual.pop('provenance');expected.pop('provenance');self.assertEqual(actual,expected)
        self.assertCountEqual(plan.payload['evidenceRefs'],[*t.evidence_refs,*c.source_refs])

    def test_virtual_group_uses_one_memory_and_replay_is_idempotent(self):
        store=_Store();svc,ctx,p,cmd=prepare_group(store)
        self.assertEqual([m.proposal.change_set.operation.kind.value for m in p.members],['add','addEvidence'])
        result=svc.confirm(command=cmd,context=ctx)
        self.assertEqual([m.activation_outcome for m in result.members],['created','revised'])
        self.assertEqual(result.members[0].memory_id,result.members[1].memory_id)
        self.assertEqual(svc.confirm(command=cmd,context=ctx).outcome,'deduplicated')
        self.assertEqual(store.repository.memory_revision(context=ctx),2)

    def test_explicit_change_and_unsafe_differences_do_not_use_repetition(self):
        cases={
            'known-opposite-polarity':lambda p:p['content']['qualifiers'].update(polarity='negative'),
            'explicit-time':lambda p:p['content']['qualifiers']['validTime'].update(expression='2027年',precision='year'),
            'different-place':lambda p:p['content']['qualifiers'].update(place={'label':'家里','category':None,'entityId':None}),
            'different-scenario':lambda p:p['content']['qualifiers'].update(scenario='工作会议'),
            'changed-statement':lambda p:p['content'].update(statement='我上陶艺课会提前 20 分钟到教室。',event='我上陶艺课会提前 20 分钟到教室。'),
            'changed-summary':lambda p:p['content'].update(summary='我不再复习了。'),
            'added-business-detail':lambda p:p['content'].update(summary='我提前到教室，还会帮助同学。'),
            'different-source':lambda p:p['evidenceRefs'][0].update(sourceId=str(uuid4())),
            'different-version':lambda p:p['evidenceRefs'][0].update(sourceVersion=2),
            'missing-evidence':lambda p:p.update(evidenceRefs=[]),
            'empty-span':lambda p:p['evidenceRefs'][0].update(span={'start':0,'end':0}),
            'missing-span':lambda p:p['evidenceRefs'][0].pop('span'),
            'mixed-source':lambda p:p['evidenceRefs'].append({'sourceId':str(uuid4()),'sourceVersion':1,'span':{'start':0,'end':1}}),
            'explicit-correction':lambda p:p.update(reviewMode='correction'),
        }
        for name,mutate in cases.items():
            with self.subTest(name=name):
                try:
                    c=changed(candidate(),mutate)
                except OwnerTruthCandidateReviewError:
                    self.assertIn(name, {'missing-evidence','mixed-source'})
                    continue
                self.assertNotIn(kind(c,current()),['addEvidence','duplicate'])

    def test_conflicting_place_identity_is_not_dropped(self):
        t=current();tc=deepcopy(t.content);tc['qualifiers']['place']['entityId']='place-one';t=replace(t,content=tc)
        c=changed(candidate(),lambda p:p['content']['qualifiers'].update(place={'label':'教室','category':None,'entityId':'place-two'}))
        self.assertNotIn(kind(c,t),['addEvidence','duplicate'])

    def test_explicit_date_in_either_fact_does_not_take_new_rule(self):
        t=current();tc=deepcopy(t.content);tc['qualifiers']['validTime'].update(expression='2020年',precision='year');t=replace(t,content=tc)
        self.assertEqual(kind(candidate(),t),'temporalChange')

    def test_cross_subject_not_merged(self):
        c=changed(candidate(),lambda p:p['content'].update(claimSubjectId='another-person',memorySubjectId='another-person'))
        self.assertEqual(kind(c,current()),'add')

    def test_explicit_correction_remains_correction(self):
        c=changed(candidate(),lambda p:p.update(reviewMode='correction'))
        self.assertEqual(kind(c,current()),'correct')
