import json
import unittest
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from uuid import uuid4
import httpx
from app.core.config import Settings
from app.domain.owner_truth.live_topics import supported_atom_catalog,LiveThemeConflict,digest
from app.services.owner_truth_live_theme_provider import DeepSeekLiveThemeProvider
from app.services.owner_truth_live_memory_contract_errors import LiveMemoryContractFailure

class ThemeProviderTests(unittest.TestCase):
    def setUp(self):
        text='我去年在杭州读书。';h=sha256(text.encode()).hexdigest()
        self.turns=[dict(index=1,messageId=str(uuid4()),role='user',text=text,contentHash=h)]
        ref=dict(turnIndex=1,start=0,end=len(text),textHash=h,
            evidenceId=sha256(f'1:0:{len(text)}:{h}'.encode()).hexdigest())
        self.atoms=[dict(id=str(uuid4()),state='active',memory=dict(memoryKind='experience',summary=text,
            sourceTurnIndices=[1],dimensions=['knowledgeSkills'],_sourceEvidenceRanges=[ref],_supportProofHash=digest('supported')))]
        self.catalog=supported_atom_catalog(atoms=self.atoms,turns=self.turns)
        self.calls=[]
        self.settings=Settings(deepseek_api_key='synthetic-only',deepseek_base_url='https://controlled.invalid/chat/completions')

    def response(self,request):
        body=json.loads(request.content);self.calls.append(body)
        data=json.loads(body['messages'][1]['content']);a=data['atoms'][0]
        if 'proposal' not in data:
            result=dict(schemaVersion='owner-truth-live-theme-v1',inputHash=data['inputHash'],omittedAtomIds=[],
                themes=[dict(key='study',title='杭州求学',summary=a['content']['summary'],atomIds=[a['atomId']],
                    evidenceIds=a['evidenceIds'],dimensions=a['dimensions'])])
        else:
            result=dict(schemaVersion='owner-truth-live-theme-support-v1',inputHash=data['inputHash'],
                proposalHash=data['proposalHash'],themes=[dict(key=g['key'],atomIds=g['atomIds'],evidenceIds=g['evidenceIds'],
                verdict='supported',sameSubjectEvent=True,compatibleTime=True,correctionsResolved=True) for g in data['proposal']['themes']])
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(result,ensure_ascii=False)}}]},request=request)

    def provider(self,handler=None):
        return DeepSeekLiveThemeProvider(self.settings,transport=httpx.MockTransport(handler or self.response))

    def test_two_transport_calls_and_independent_exact_binding(self):
        provider=self.provider();stage,request=provider.prepare(atoms=self.catalog)
        organized=provider.request_prepared(stage=stage,request=request)
        stage,request=provider.prepare(atoms=self.catalog,proposal=organized.payload)
        reviewed=provider.request_prepared(stage=stage,request=request)
        result=provider.validate(atoms=self.catalog,proposal=organized.payload,review=reviewed.payload)
        self.assertEqual(len(result.themes),1);self.assertEqual(len(self.calls),2)
        self.assertEqual(self.calls[1]['max_tokens'],4096)
        self.assertNotEqual(self.calls[0]['messages'][0]['content'],self.calls[1]['messages'][0]['content'])

    def test_input_capacity_rejected_before_transport_without_truncation(self):
        provider=self.provider();oversized=deepcopy(self.catalog)
        oversized[0]['content']['summary']='长'*20000
        with self.assertRaisesRegex(LiveMemoryContractFailure,'inputOverCapacity'):provider.prepare(atoms=oversized)
        self.assertEqual(self.calls,[])

    def test_429_is_not_retried_by_adapter_and_retains_retry_after(self):
        calls=[]
        def handler(request):
            calls.append(request);return httpx.Response(429,headers={'Retry-After':'17'},request=request)
        p=self.provider(handler);stage,r=p.prepare(atoms=self.catalog)
        with self.assertRaises(LiveMemoryContractFailure) as failure:p.request_prepared(stage=stage,request=r)
        self.assertEqual(len(calls),1);self.assertEqual(failure.exception.retry_after_seconds,17)
        self.assertTrue(failure.exception.transport_retryable)

    def test_truncated_or_wrong_type_finish_reason_cannot_publish(self):
        for finish in ['length',{'stop':True}]:
            with self.subTest(finish=finish):
                p=self.provider(lambda r:httpx.Response(200,json={'choices':[{'finish_reason':finish,'message':{'content':'{}'}}]},request=r))
                stage,r=p.prepare(atoms=self.catalog)
                with self.assertRaises(LiveMemoryContractFailure):p.request_prepared(stage=stage,request=r)

    def test_timeout_is_typed_and_no_retry(self):
        def handler(request):raise httpx.ReadTimeout('controlled',request=request)
        p=self.provider(handler);stage,r=p.prepare(atoms=self.catalog)
        with self.assertRaises(LiveMemoryContractFailure) as failure:p.request_prepared(stage=stage,request=r)
        self.assertEqual(failure.exception.reason,'readTimeout')

    def test_real_range_but_wrong_message_body_rejected(self):
        changed=deepcopy(self.turns);changed[0]['text']='我没有去杭州。'
        with self.assertRaisesRegex(LiveThemeConflict,'sourceMessageHashMismatch'):
            supported_atom_catalog(atoms=self.atoms,turns=changed)

    def test_assistant_text_cannot_support_owner_fact(self):
        changed=deepcopy(self.turns);changed[0]['role']='assistant'
        with self.assertRaisesRegex(LiveThemeConflict,'atomEvidenceOutsideOwnerSource'):
            supported_atom_catalog(atoms=self.atoms,turns=changed)

    def test_wrong_range_hash_rejected_even_with_support_hash(self):
        changed=deepcopy(self.atoms);changed[0]['memory']['_sourceEvidenceRanges'][0]['textHash']='0'*64
        with self.assertRaisesRegex(LiveThemeConflict,'atomEvidenceHashMismatch'):
            supported_atom_catalog(atoms=changed,turns=self.turns)

    def test_equal_text_at_another_identity_is_distinct_evidence(self):
        changed=deepcopy(self.turns);changed[0]['messageId']=str(uuid4())
        other=supported_atom_catalog(atoms=self.atoms,turns=changed)
        self.assertNotEqual(other[0]['evidenceIds'],self.catalog[0]['evidenceIds'])


class ThemeLongPageTests(unittest.TestCase):
    def test_seventy_facts_same_event_reduce_without_identity_loss_or_oversized_request(self):
        from app.async_effects.owner_truth_live_recovery_worker import RecoveryThemeAssembler
        atoms=[dict(atomId=f'a{i}',content=dict(summary=f'同一杭州旅行的明确细节{i}'),contentHash=digest(i),
            dimensions=['dailyLife'],supportState='supported',evidenceIds=[f'e{i}'],
            evidence=[dict(evidenceId=f'e{i}',text=f'原文细节{i}')]) for i in range(70)]
        calls=[]
        def respond(request):
            body=json.loads(request.content);material=json.loads(body['messages'][1]['content'])
            calls.append((len(request.content),len(material['atoms'])))
            if 'proposal' not in material:
                page=material['atoms']
                payload=dict(schemaVersion='owner-truth-live-theme-v1',inputHash=material['inputHash'],
                    omittedAtomIds=[],themes=[dict(key='trip',title='杭州旅行',summary='同一旅行的受控归纳',
                        atomIds=[a['atomId'] for a in page],evidenceIds=[e for a in page for e in a['evidenceIds']],dimensions=['dailyLife'])])
            else:
                payload=dict(schemaVersion='owner-truth-live-theme-support-v1',inputHash=material['inputHash'],
                    proposalHash=material['proposalHash'],themes=[dict(key=g['key'],atomIds=g['atomIds'],evidenceIds=g['evidenceIds'],
                        verdict='supported',sameSubjectEvent=True,compatibleTime=True,correctionsResolved=True) for g in material['proposal']['themes']])
            return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(payload)}}]},request=request)
        provider=DeepSeekLiveThemeProvider(Settings(deepseek_api_key='synthetic',deepseek_base_url='https://controlled.invalid'),transport=httpx.MockTransport(respond))
        assembler=object.__new__(RecoveryThemeAssembler);assembler.provider=provider
        def call(**kwargs):
            page=kwargs['atoms'];proposal=kwargs.get('proposal')
            stage,request=provider.prepare(atoms=page,proposal=proposal)
            result=provider.request_prepared(stage=stage,request=request)
            provider.validate_payload(atoms=page,proposal=proposal,payload=result.payload)
            return result.payload
        assembler._call=call
        themes,omitted,blocked=assembler.organize_pages(lease=None,run_id='run',revision=1,source_id='source',catalog=atoms)
        self.assertEqual(len(themes),1)
        self.assertEqual(set(themes[0].atom_ids),{f'a{i}' for i in range(70)})
        self.assertEqual(set(themes[0].evidence_ids),{f'e{i}' for i in range(70)})
        self.assertEqual(omitted,());self.assertEqual(blocked,())
        self.assertGreater(len(calls),2);self.assertTrue(all(size<=48000 and count<=32 for size,count in calls))

    def test_bad_json_shape_is_rejected_before_it_can_be_cached_as_completed(self):
        atoms=[dict(atomId='a',content={'summary':'事实'},contentHash=digest('a'),dimensions=['dailyLife'],
                    evidenceIds=['e'],evidence=[{'text':'事实'}],supportState='supported')]
        p=DeepSeekLiveThemeProvider(Settings())
        with self.assertRaisesRegex(LiveMemoryContractFailure,'invalidThemes'):
            p.validate_payload(atoms=atoms,proposal=None,payload={'inputHash':digest(atoms)})

    def test_wrong_evidence_is_rejected_even_when_json_is_well_formed(self):
        atoms=[dict(atomId='a',content={'summary':'事实'},contentHash=digest('a'),dimensions=['dailyLife'],
                    evidenceIds=['e'],evidence=[{'text':'事实'}],supportState='supported')]
        proposal=dict(schemaVersion='owner-truth-live-theme-v1',inputHash=digest(atoms),omittedAtomIds=[],
            themes=[dict(key='t',title='主题',summary='事实',atomIds=['a'],evidenceIds=['wrong'],dimensions=['dailyLife'])])
        with self.assertRaisesRegex(LiveMemoryContractFailure,'themeEvidenceMismatch'):
            DeepSeekLiveThemeProvider(Settings()).validate_payload(atoms=atoms,proposal=None,payload=proposal)


class PartialSafetyTests(unittest.TestCase):
    def test_failed_page_correction_blocks_only_affected_theme_and_preserves_all_context(self):
        from app.domain.owner_truth.live_topics import SupportedLiveTheme
        from app.async_effects.owner_truth_live_recovery_worker import RecoveryThemeAssembler
        themes=[SupportedLiveTheme(k,k,k,(k,),('e'+k,),('experience',),digest(k)) for k in ['trip','work']]
        turns=[dict(role='user',index=i,messageId=str(uuid4()),text=('合成上下文。'*70)+('更正：不是杭州' if i==55 else '')) for i in range(1,61)]
        seen={'trip':set(),'work':set()};calls=[]
        def handler(request):
            body=json.loads(request.content);data=json.loads(body['messages'][1]['content']);calls.append(request)
            key=data['themes'][0]['key'];seen[key].update(t['index'] for t in data['context'])
            verdict='conflict' if key=='trip' and any('更正' in t['text'] for t in data['context']) else 'safe'
            payload=dict(schemaVersion='owner-truth-live-theme-safety-v1',inputHash=data['inputHash'],themes=[dict(key=key,verdict=verdict)])
            return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(payload)}}]},request=request)
        assembler=object.__new__(RecoveryThemeAssembler)
        assembler.provider=DeepSeekLiveThemeProvider(Settings(deepseek_api_key='synthetic-only'),transport=httpx.MockTransport(handler))
        def call(**kw):
            stage,request=kw['prepared'];response=assembler.provider.request_prepared(stage=stage,request=request)
            kw['validator'](response.payload);return response.payload
        assembler._call=call
        safe,blocked=assembler.guard_partial_themes(lease=None,run_id='local',revision=1,source_id='local',themes=themes,turns=turns)
        self.assertEqual([t.key for t in safe],['work']);self.assertEqual(blocked[0]['key'],'trip')
        self.assertEqual(seen['work'],set(range(1,61)))
        self.assertTrue(all(len(r.content)<=48000 for r in calls));self.assertLess(len(calls),30)

    def test_fact_fingerprint_ignores_nested_source_location_but_not_meaning(self):
        from app.services.owner_truth_live_topics import fact_fingerprint
        a={'statement':'我在杭州读书','provenance':{'mode':'selfReport','evidenceRefs':[{'sourceId':'a'}]}}
        b=deepcopy(a);b['provenance']['evidenceRefs']=[{'sourceId':'b'}]
        self.assertEqual(fact_fingerprint(a),fact_fingerprint(b))
        b['statement']='我在上海读书';self.assertNotEqual(fact_fingerprint(a),fact_fingerprint(b))
