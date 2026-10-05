"""Relation quality must not claim content loss after independent safety passes."""
import json
import unittest
from copy import deepcopy
from contextlib import nullcontext
from types import SimpleNamespace as NS
from uuid import uuid4
from hashlib import sha256
import httpx
from app.core.config import Settings
from app.services.deepseek import DeepSeekLiveMemoryOrganizationProxy as Proxy
from app.services.owner_truth_live_memory_contract_errors import LiveMemoryContractFailure, contract_failure
from app.async_effects.owner_truth_live_recovery_worker import RecoveryThemeAssembler
from app.domain.owner_truth.live_topics import SupportedLiveTheme, digest
from tests.test_owner_truth_live_long_memory_pipeline import _typed_memory

class AssembleProbe(RecoveryThemeAssembler):
    def __init__(self, deny=False, missing=False, failure=True, omitted=False, extraction_failure=False):
        self.deny=deny; self.failure=failure; self.omit=omitted; self.guarded=[]; self.questions=[]
        self.turns=[]; self.atoms=[]
        for i,text in enumerate(('我每周三练习书法。','我用蓝色笔记本记练习要点。'),1):
            th=sha256(text.encode()).hexdigest()
            self.turns.append(dict(index=i,role='user',text=text,messageId=str(uuid4()),contentHash=th))
            memory=_typed_memory(text,[i]);memory.update(_supportProofHash=digest(text),_sourceEvidenceRanges=[dict(turnIndex=i,start=0,end=len(text),textHash=th,evidenceId=sha256(f'{i}:0:{len(text)}:{th}'.encode()).hexdigest())])
            self.atoms.append(dict(id=str(uuid4()),state='active',memory=memory))
        self.units=[dict(state='completed',ownership=[dict(index=1),dict(index=2)])]
        if extraction_failure:
            self.units=[dict(state='completed',ownership=[dict(index=1)]),dict(state='failed',ownership=[dict(index=2)])]
            self.atoms=self.atoms[:1]
        cursor=NS(execute=lambda sql,*a:self.questions.append(sql),fetchone=lambda:dict(id='run'),fetchall=self.fetch)
        repo=NS(_cursor=lambda:nullcontext(cursor))
        self.created=[]
        def build(**kw):self.created=kw['memories'];return NS(proposals=())
        host=NS(_settings=Settings(deepseek_api_key='controlled'),_store=NS(owner_truth_live_topic_repository=lambda:repo),_live_preorganizer=NS(build_verified_proposals=build))
        super().__init__(host)
        self.source=NS(source_id='source',source_content_hash=digest('source'),source_metadata=dict(origin='liveRecoverySnapshot',snapshotRevision=1,productSessionId='scene',productCaptureGeneration=1,conversationTurns=self.turns,snapshotId='snapshot',snapshotHash=digest('snapshot'),receivedRanges=[[1,2]],missingRanges=[[3,3]] if missing else [],endPositionKnown=not missing,completeness='partial' if missing else 'receivedComplete'))
    def fetch(self):
        sql=self.questions[-1]
        if "SELECT id,state,ownership" in sql:return self.units
        if 'SELECT a.id' in sql:return deepcopy(self.atoms)
        return []
    def _uow(self,*args):return nullcontext()
    def _admit(self,*args):pass
    def resolve_snapshot_atoms(self,**kw):
        if self.failure:raise contract_failure('relationValidate','schemaInvalid',eligible=True)
        return kw['atoms'],dict(lineage={},retractedAtomIds=[])
    def organize_pages(self,**kw):
        atoms=kw['catalog']; selected=atoms[:-1] if self.omit else atoms
        themes=tuple(SupportedLiveTheme(a['atomId'],'书法',a['content']['claim'],(a['atomId'],),tuple(a['evidenceIds']),('knowledgeSkills',),digest(a)) for a in selected)
        return themes,tuple(a['atomId'] for a in atoms[len(selected):]),()
    def guard_partial_themes(self,**kw):
        self.guarded.append(kw)
        if self.deny:return kw['themes'][:-1],(dict(reason='unresolvedCorrectionContext',atomIds=list(kw['themes'][-1].atom_ids)),)
        return kw['themes'],()
    def relate_themes(self,**kw):return kw['themes'],{},(),(),{}
    def run_scene(self):return self.assemble(lease=None,intent=NS(target=NS(vault_id='v',owner_subject_id='o',authority_epoch=1)),source=self.source)

class CompletionTests(unittest.TestCase):
    def test_relation_failure_full_safety_and_full_coverage_is_content_complete(self):
        p=AssembleProbe();out=p.run_scene();m=out.manifest
        self.assertEqual(len(p.guarded),1);self.assertEqual(p.guarded[0]['turns'],p.turns)
        self.assertEqual(m['completeness'],'receivedComplete')
        self.assertEqual(m['blockedThemes'],[]);self.assertEqual(m['omittedAtomIds'],[])
        self.assertEqual(set(m['atomIds']),{a['id'] for a in p.atoms});self.assertEqual(len(p.created),2)
        self.assertEqual(m['relationDiagnostics'][0]['failureCode'],'candidateExtraction.live.relationValidate.schemaInvalid')
        self.assertEqual(m['relationDiagnostics'][0]['safetyReview'],'passed')
        hashed=dict(m);hashed.pop('hash');self.assertEqual(m['hash'],digest(hashed))
    def test_failed_safety_still_removes_unsafe_fact_and_reports_partial(self):
        p=AssembleProbe(deny=True);m=p.run_scene().manifest
        self.assertEqual(m['completeness'],'partial');self.assertEqual(len(p.created),1)
        self.assertEqual(len(m['omittedAtomIds']),1)
        self.assertEqual([b['reason'] for b in m['blockedThemes']],['unresolvedCorrectionContext'])
        self.assertEqual(m['relationDiagnostics'][0]['safetyReview'],'blocked')
    def test_source_gap_and_real_omission_or_extraction_failure_remain_partial(self):
        for args in (dict(missing=True),dict(omitted=True),dict(extraction_failure=True)):
            with self.subTest(args=args):
                p=AssembleProbe(**args);m=p.run_scene().manifest
                self.assertEqual(m['completeness'],'partial');self.assertEqual(len(p.guarded),1)
    def test_success_does_not_add_safety_call_or_diagnostic(self):
        p=AssembleProbe(failure=False);m=p.run_scene().manifest
        self.assertFalse(p.guarded);self.assertEqual(m['completeness'],'receivedComplete')
        self.assertIn('relationDiagnostics',m);self.assertEqual(m['relationDiagnostics'],[])
    def test_all_unsafe_themes_cannot_become_success(self):
        p=AssembleProbe()
        p.guard_partial_themes=lambda **kw:((),tuple(dict(reason='unresolvedCorrectionContext',atomIds=list(t.atom_ids)) for t in kw['themes']))
        with self.assertRaisesRegex(LiveMemoryContractFailure,'noReliableThemes'):p.run_scene()
        self.assertFalse(p.created)
    def test_authority_and_database_faults_never_become_completion(self):
        for error in (contract_failure('themeValidate','sourceAuthorityChanged',category='domain'),RuntimeError('database unavailable')):
            p=AssembleProbe()
            def fail(**kw):raise error
            p.resolve_snapshot_atoms=fail
            with self.assertRaises(type(error)):p.run_scene()
            self.assertFalse(p.created)

class RelationContractTests(unittest.TestCase):
    turns=[dict(index=1,role='user',text='我用蓝色笔记本。'),dict(index=2,role='user',text='这本蓝色笔记本记录书法要点。')]
    def result(self,relation='duplicate'):
        return dict(results=[dict(incomingIndex=0,scannedExistingCount=1,decisions=[dict(existingIndex=0,relation=relation)])])
    def parse(self,value):return Proxy.parse_relation_batch_review(json.dumps(value),turns=self.turns,incoming_count=1,existing_count=1)
    def test_positive_relations_and_empty_decisions(self):
        for kind in ('duplicate','correction','retraction','unresolved','supplement'):
            v=self.result(kind)
            if kind=='supplement':v['results'][0]['decisions'][0]['resolvedMemory']=_typed_memory('我用蓝色笔记本记录书法要点。',[1,2])
            self.assertEqual(self.parse(v)['results'][0]['decisions'][0]['relation'],kind)
        v=self.result();v['results'][0]['decisions']=[];self.assertEqual(self.parse(v),v)
    def test_contract_failure_has_specific_value_free_reason(self):
        cases=[]
        v=self.result();v['results']=[];cases.append((v,'batchCoverageInvalid'))
        v=self.result();v['results'][0]['scannedExistingCount']=1.0;cases.append((v,'batchReceiptInvalid'))
        v=self.result();v['results'][0]['incomingIndex']=True;cases.append((v,'batchReceiptInvalid'))
        v=self.result();v['results'][0]['decisions'][0]['existingIndex']=5;cases.append((v,'batchTargetInvalid'))
        v=self.result();v['results'][0]['decisions'][0]['relation']='private-synthetic-body';cases.append((v,'batchRelationInvalid'))
        v=self.result();v['results'][0]['decisions'][0]['resolvedMemory']=None;cases.append((v,'unexpectedResolvedMemory'))
        v=self.result('supplement');cases.append((v,'resolvedMemoryMissing'))
        v=self.result('supplement');v['results'][0]['decisions'][0]['resolvedMemory']=dict(memoryKind='knowledge',claim='private-synthetic-body',sourceTurnIndices=[1]);cases.append((v,'supplementFacetsInvalid'))
        v=self.result('supplement');v['results'][0]['decisions'][0]['resolvedMemory']=_typed_memory('private-synthetic-body',[999]);cases.append((v,'supplementEvidenceInvalid'))
        for value,reason in cases:
            with self.subTest(reason=reason):
                with self.assertRaises(ValueError) as caught:self.parse(value)
                self.assertIsInstance(caught.exception,LiveMemoryContractFailure)
                self.assertEqual(caught.exception.reason,reason);self.assertTrue(caught.exception.contract_retry_eligible)
                self.assertNotIn('private-synthetic-body',str(caught.exception))
    def test_prompt_explains_non_supplement_field_prohibition_and_supplement_shape(self):
        p=Proxy(Settings(deepseek_api_key='controlled'))
        body=p.build_relation_batch_request(turns=self.turns,incoming=[_typed_memory(self.turns[1]['text'],[2])],existing=[_typed_memory(self.turns[0]['text'],[1])])['json']
        prompt=body['messages'][1]['content']
        self.assertIn('非 supplement',prompt);self.assertIn('null',prompt);self.assertIn('facets',prompt)
        self.assertIn('evidenceFragmentIds',prompt);self.assertEqual(body['max_tokens'],4096)
    def test_real_adapter_attaches_safe_response_observation(self):
        p=Proxy(Settings(deepseek_api_key='controlled'));value=self.result();value['results'][0]['decisions'][0]['resolvedMemory']='sensitive-string'
        raw=json.dumps(value)
        p._client=lambda:httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,json=dict(choices=[dict(finish_reason='stop',message=dict(content=raw))],usage=dict(total_tokens=42)))))
        with self.assertRaises(LiveMemoryContractFailure) as caught:p.request_relation_batch_review(turns=self.turns,incoming=[{}],existing=[{}])
        e=caught.exception;self.assertEqual(e.reason,'unexpectedResolvedMemory')
        self.assertEqual(e.provider_observation['responseHash'],sha256(raw.encode()).hexdigest())
        self.assertEqual(e.provider_observation['finishReason'],'stop');self.assertNotIn('sensitive-string',json.dumps(e.provider_observation))

if __name__=='__main__':unittest.main()
