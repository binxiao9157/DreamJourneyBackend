import json
import unittest
from copy import deepcopy
from hashlib import sha256
from uuid import uuid4
import httpx
from app.core.config import Settings
from app.async_effects.owner_truth_candidate_extraction_worker import ModelAssistedOwnerTruthLiveConversationExtractor as Extractor
from app.services.owner_truth_live_long_memory import InMemoryLiveLongMemoryRepository, LiveLongMemoryRunIdentity, LiveLongMemoryBudgetPolicy
from app.services.owner_truth_live_memory_contract_errors import LiveMemoryContractFailure
from app.services.owner_truth_live_theme_provider import DeepSeekLiveThemeProvider
from app.domain.owner_truth.live_topics import SupportedLiveTheme, digest


class Relations(unittest.TestCase):
    def setUp(self):
        self.repo = InMemoryLiveLongMemoryRepository()
        self.identity = LiveLongMemoryRunIdentity('owner', 'vault', 'scene', 1, 1)
        self.repo.begin_or_load(self.identity, LiveLongMemoryBudgetPolicy())
        self.repo.bind_source(run_id=self.identity.run_id, authority_epoch=1, source_id=str(uuid4()),
                              source_version=1, source_content_hash=digest('source'), final_watermark=1)
        text = 'A supported synthetic fact.'
        self.turns = [dict(index=1, role='user', text=text, captureMode='live')]
        self.memories = [dict(memoryKind='knowledge', claim='Fact '+str(i), sourceTurnIndices=[1],
            _sourceEvidenceRanges=[dict(turnIndex=1, start=0, end=len(text),
                textHash=sha256(text.encode()).hexdigest(), evidenceId=digest(i))]) for i in range(8)]
        self.results = [dict(incomingIndex=i, scannedExistingCount=8,
                        decisions=[dict(existingIndex=i, relation='duplicate')]) for i in range(8)]
        self.calls = 0
        owner = self
        class Provider:
            model = 'controlled'
            def request_relation_batch_review(self, **kwargs):
                owner.calls += 1
                return dict(results=deepcopy(owner.results))
        p = Provider()
        self.extractor = Extractor(settings=Settings(), organizer=p, support_reviewer=p,
                                   relation_reviewer=p, run_repository=self.repo)

    def execute(self):
        return self.extractor._relation_batch_page(turns=self.turns, incoming=self.memories,
            existing=self.memories, run_identity=self.identity, retry_context=None,
            ordinal=2009000, intra_batch=True)

    def test_self_reference_never_becomes_completed(self):
        error = None
        try: self.execute()
        except LiveMemoryContractFailure as caught: error = caught
        snap = self.repo.snapshot(self.identity.run_id)
        self.assertFalse(any(u['state']=='completed' for u in snap['units']),
                         'illegal model relation cached as completed')
        self.assertIsNotNone(error)
        self.assertIn('forwardBatchTarget', error.code)
        self.assertEqual(snap['providerRequestCount'], 1)

    def test_valid_distinct_then_duplicate_reuses_cache(self):
        for r in self.results: r['decisions'] = []
        self.results[7]['decisions'] = [dict(existingIndex=0, relation='duplicate')]
        self.assertEqual(self.execute(), self.results)
        self.assertEqual(self.execute(), self.results)
        self.assertEqual(self.calls, 1)

    def test_invalid_relation_enum_not_completed(self):
        for r in self.results: r['decisions'] = []
        self.results[7]['decisions'] = [dict(existingIndex=0, relation='notARelation')]
        with self.assertRaises(LiveMemoryContractFailure): self.execute()
        self.assertFalse(any(u['state']=='completed' for u in self.repo.snapshot(self.identity.run_id)['units']))

    def test_second_failure_cannot_get_new_initial_budget(self):
        for _ in range(4):
            try: self.execute()
            except Exception: pass
        self.assertLessEqual(self.calls, 2)
        self.assertLessEqual(self.repo.snapshot(self.identity.run_id)['providerRequestCount'], 2)

    def test_invalid_completed_cache_is_refused_without_overwrite(self):
        illegal=deepcopy(self.results)
        for r in self.results: r['decisions']=[]
        self.execute()
        unit=self.repo.snapshot(self.identity.run_id)['units'][0]
        coverage={'results':illegal}
        output_hash=sha256(json.dumps(coverage,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        if hasattr(self.repo, 'inject_legacy_cache'):
            self.repo.inject_legacy_cache(unit['unitId'],coverage,output_hash)
        else:
            self.repo._units[unit['unitId']].update(coverage=coverage,outputHash=output_hash)
        before=self.repo.snapshot(self.identity.run_id)
        with self.assertRaises(LiveMemoryContractFailure):self.execute()
        after=self.repo.snapshot(self.identity.run_id)
        self.assertEqual(after['units'],before['units'])
        self.assertEqual(after['providerRequestCount'],before['providerRequestCount'])
        self.assertEqual(self.calls,1)

    def test_inflight_concurrent_caller_does_not_send_twice(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        entered,release=Event(),Event()
        provider=self.extractor._relation_reviewer
        original=provider.request_relation_batch_review
        def blocked(**kw):
            entered.set()
            if not release.wait(10):raise AssertionError('test barrier timeout')
            return original(**kw)
        for r in self.results:r['decisions']=[]
        provider.request_relation_batch_review=blocked
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(self.execute)
            self.assertTrue(entered.wait(10))
            try:
                with self.assertRaises(Exception): self.execute()
            finally: release.set()
            self.assertEqual(first.result(),self.results)
        self.assertEqual(self.calls,1)
        self.assertEqual(self.repo.snapshot(self.identity.run_id)['providerRequestCount'],1)

    def test_superseded_attempt_cannot_complete_or_fail_successor(self):
        from app.services.owner_truth_live_long_memory import LiveLongMemoryUnitPlan,LiveLongMemoryConflict
        from datetime import datetime,timezone,timedelta
        plan=LiveLongMemoryUnitPlan(self.identity.run_id,999,'relationReviewBatch',1,({'index':1},))
        self.repo.record_unit(plan)
        args=dict(run_id=self.identity.run_id,unit_id=plan.unit_id,stage='relationReviewBatch',request_hash=digest('request'),reserved_input_tokens=100,reserved_output_tokens=100,recovery=False)
        old=self.repo.reserve_provider_attempt(**args)
        if isinstance(self.repo,InMemoryLiveLongMemoryRepository):
            self.repo._attempts[old.attempt_id]['reservedAt']=(datetime.now(timezone.utc)-timedelta(seconds=100)).isoformat()
        else:self.repo.expire_attempt(old.attempt_id)
        current=self.repo.reserve_provider_attempt(**args)
        with self.assertRaises(LiveLongMemoryConflict):
            self.repo.record_unit_result(plan=plan,atoms=(),output_hash=digest('old'),coverage={},provider_attempt_id=old.attempt_id)
        with self.assertRaises(LiveLongMemoryConflict):
            self.repo.record_unit_failure(plan=plan,failure_code='lateFailure',terminal=True,provider_attempt_id=old.attempt_id)
        self.repo.record_unit_result(plan=plan,atoms=(),output_hash=digest('current'),coverage={},provider_attempt_id=current.attempt_id)
        self.assertEqual(self.repo.snapshot(self.identity.run_id)['recoveryRequestCount'],1)


class ProviderContract(unittest.TestCase):
    def setUp(self):
        self.p = DeepSeekLiveThemeProvider(Settings(deepseek_api_key='synthetic-only'))
        self.atom = dict(atomId='a', content={'summary':'Synthetic event'}, contentHash=digest('a'),
            supportState='supported', dimensions=['experience'], evidenceIds=['e'], evidence=[dict(text='Synthetic event')])
        self.theme = SupportedLiveTheme('t','Title','Synthetic event',('a',),('e',),('experience',),digest('proof'))

    def test_safety_request_conforms_without_user_json_word(self):
        stage, request, _ = self.p.prepare_safety(themes=[self.theme], context=[dict(text='Synthetic event')])
        body = request.payload()
        self.assertEqual(body['response_format'], {'type':'json_object'})
        self.assertIn('json', body['messages'][0]['content'].lower())

    def test_relation_request_conforms_without_user_json_word(self):
        _, request = self.p.prepare_relation(material={'targets':[{'topicId':'t'}], 'atoms':[self.atom]})
        self.assertIn('json', request.payload()['messages'][0]['content'].lower())

    def test_support_request_bytes_survive_jsonb_key_order(self):
        proposal = dict(inputHash=digest([self.atom]), schemaVersion='owner-truth-live-theme-v1',
            themes=[dict(key='t', summary='Synthetic event', atomIds=['a'], evidenceIds=['e'])])
        reordered = json.loads(json.dumps(proposal, sort_keys=True))
        self.assertEqual(self.p.prepare(atoms=[self.atom], proposal=proposal)[1].body,
                         self.p.prepare(atoms=[self.atom], proposal=reordered)[1].body)

    def test_relation_http_failure_preserves_typed_stage(self):
        self.p = DeepSeekLiveThemeProvider(Settings(deepseek_api_key='synthetic-only'),
            transport=httpx.MockTransport(lambda r: httpx.Response(400, request=r)))
        stage, request = self.p.prepare_relation(material={'targets':[{'topicId':'t'}], 'atoms':[self.atom]})
        with self.assertRaises(LiveMemoryContractFailure) as failure:
            self.p.request_prepared(stage=stage, request=request)
        self.assertEqual(failure.exception.provider_status, 400)
        self.assertEqual(failure.exception.reason, 'requestRejected')

    def test_http_taxonomy_and_diagnostic_never_contains_body_or_request_id(self):
        for status in [400,401,403,429,503]:
            with self.subTest(status=status):
                self.p=DeepSeekLiveThemeProvider(Settings(deepseek_api_key='synthetic-only'),
                    transport=httpx.MockTransport(lambda r:httpx.Response(status,request=r,
                        headers={'x-request-id':'private-id','retry-after':'3'},json={'error':{'code':'invalid_request_error','message':'PRIVATE_CONTENT'}})))
                stage,request,_=self.p.prepare_safety(themes=[self.theme],context=[{'text':'PRIVATE_PROMPT'}])
                with self.assertRaises(LiveMemoryContractFailure) as caught:self.p.request_prepared(stage=stage,request=request)
                e=caught.exception
                self.assertEqual(e.provider_status,status)
                self.assertEqual(e.transport_retryable,status in [429,503])
                self.assertEqual(e.provider_observation['httpStatus'],status)
                saved=json.dumps(e.provider_observation)
                for secret in ['PRIVATE_CONTENT','PRIVATE_PROMPT','private-id','synthetic-only']:self.assertNotIn(secret,saved)

    def test_old_contract_is_rejected_by_independent_transport(self):
        calls=[]
        def service(r):
            body=json.loads(r.content);calls.append(body)
            valid='json' in body['messages'][0]['content'].lower()
            return httpx.Response(200 if valid else 400,request=r,json={'choices':[{'finish_reason':'stop','message':{'content':'{}'}}]})
        self.p=DeepSeekLiveThemeProvider(Settings(deepseek_api_key='synthetic-only'),transport=httpx.MockTransport(service))
        stage,request,_=self.p.prepare_safety(themes=[self.theme],context=[{'text':'synthetic'}])
        self.p.request_prepared(stage=stage,request=request)
        old=request.payload();old['messages'][0]['content']='Return a safety verdict.'
        with httpx.Client(transport=httpx.MockTransport(service)) as c:
            self.assertEqual(c.post('https://controlled.invalid/v1/chat',json=old).status_code,400)
        self.assertEqual(len(calls),2)


if __name__ == '__main__': unittest.main(verbosity=2)
