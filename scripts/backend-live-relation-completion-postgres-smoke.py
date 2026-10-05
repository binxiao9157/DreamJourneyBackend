"""Relation completion regression: default API/Worker, real local PG, controlled HTTP.
Derived from run-terminal smoke; preserves short-A -> logical10 -> short-B gates.
"""
import sys, json, importlib.util, uuid, os
from urllib.parse import urlparse
from pathlib import Path
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from concurrent.futures import ThreadPoolExecutor
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import psycopg, httpx
import app.main as main
from app.core.config import Settings
from app.db.migrator import PostgresMigrator,default_migrations_dir
from app.services.postgres_store import PostgresStore
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
spec=importlib.util.spec_from_file_location('helper', ROOT/'scripts/backend-owner-truth-live-candidate-formal-postgres-smoke.py')
h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
DB='dj_live_recovery_'+uuid.uuid4().hex[:10]
ADMIN=os.environ['LOCAL_LIVE_TEST_ADMIN_DSN']
parsed=urlparse(ADMIN)
assert parsed.hostname in {'127.0.0.1','localhost'}, 'local disposable PostgreSQL only'
DSN=parsed._replace(path='/'+DB).geturl()
OUTPUT=Path(os.environ['LOCAL_LIVE_TEST_OUTPUT'])
OUTPUT.mkdir(parents=True,exist_ok=True)
with psycopg.connect(ADMIN,autocommit=True) as c:
    c.execute(psycopg.sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(psycopg.sql.Identifier(DB)))
PostgresMigrator(dsn=DSN,migrations_dir=default_migrations_dir(),build_id='local-live-recovery').apply()
store=PostgresStore(dsn=DSN,pool_min_size=1,pool_max_size=5);store.open_pool(wait=True)
main.store=store
main.settings=replace(main.settings,owner_truth_live_recovery_enabled=True,owner_truth_live_long_memory_pipeline_enabled=True)
main.BACKEND_API_TOKEN='';main.AUTH_LEGACY_PHONE_LOGIN_ENABLED=True
main.AUTH_ROUTE_MODE='enforce';main.AUTH_OWNERSHIP_MODE='enforce';main.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED=True
# Use the ordinary API startup/shutdown; do not bypass lifespan.
import socket, threading, time, uvicorn
listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(128);port=listener.getsockname()[1]
server=uvicorn.Server(uvicorn.Config(main.app,host='127.0.0.1',port=port,lifespan='on',log_level='error'))
thread=threading.Thread(target=lambda:server.run(sockets=[listener]),daemon=True);thread.start()
limit=time.monotonic()+15
while not server.started and thread.is_alive() and time.monotonic()<limit:time.sleep(.01)
assert server.started,'default API startup did not complete'

client=httpx.Client(base_url=f'http://127.0.0.1:{port}',timeout=20)
results={}
from tests.test_owner_truth_live_long_memory_pipeline import _typed_memory
from app.domain.owner_truth.live_topics import digest
from app.async_effects.owner_truth_candidate_extraction_worker import OwnerTruthCandidateExtractionWorkerRuntime
from app.domain.owner_truth.source_commands import OwnerTruthCommandAuthorizationCapture
active={};model_requests=[]
settings=replace(main.settings,store_backend='postgres',database_url=DSN,deepseek_api_key='local-controlled',
    async_effect_v1_enabled=True,async_effect_worker_enabled=True,owner_truth_candidate_extraction_worker_enabled=True,
    owner_truth_live_memory_organization_enabled=True)
def model(request):
    data=json.loads(request.content)
    try:body=json.loads(data['messages'][1]['content'])
    except ValueError:
        if active.get('relation_failure') and '新事实页：' in data['messages'][1]['content']:
            # Deliberately invalid non-supplement field, through the real adapter.
            prompt=data['messages'][1]['content']
            raw=json.JSONDecoder().raw_decode(prompt.split('新事实页：',1)[1])[0]
            old=json.JSONDecoder().raw_decode(prompt.split('既有事实页：',1)[1])[0]
            value=dict(results=[dict(incomingIndex=i,scannedExistingCount=len(old),decisions=[dict(existingIndex=0,relation='duplicate',resolvedMemory=None)]) for i in range(len(raw))])
            active['invalid_relations']=active.get('invalid_relations',0)+1
            return httpx.Response(200,request=request,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(value)}}]})
        return active['handler'](request)
    model_requests.append(list(body) if isinstance(body,dict) else [])
    if 'context' in body and 'themes' in body:
        active['safety_calls']=active.get('safety_calls',0)+1
        value=dict(schemaVersion='owner-truth-live-theme-safety-v1',inputHash=body['inputHash'],themes=[dict(key=t['key'],verdict='uncertain' if active.get('deny_safety') and '暂不发布' in t['summary'] else 'safe') for t in body['themes']])
    elif 'relationScreen' in body:
        value=dict(schemaVersion='live-relation-screen-v1',inputHash=body['inputHash'],targets=[
            dict(topicId=t['topicId'],version=t['version'],proposalHash=t['proposalHash'],verdict='uncertain')
            for t in body['relationScreen']['targets']])
    elif 'material' in body:
        material=body['material'];target=next((t for t in material['targets'] if t['topicId']==active.get('target')),None)
        if 'proposal' in body:
            if target and target['state'] in ('accepted','rejected'):
                assert body['proposal']['summary']==material['theme']['summary']
                assert body['proposal']['title']==material['theme']['title']
            value=dict(schemaVersion='owner-truth-live-theme-relation-support-v1',inputHash=body['inputHash'],proposalHash=body['proposalHash'],
                verdict='supported',sameSubjectEvent=body['proposal']['relation'] not in ('none','uncertain'),compatibleTime=True,correctionsResolved=body['proposal']['relation']=='correction',summarySupported=True,correctionIntentSupported=not active.get('deny_intent',False))
        elif active.get('relation')=='uncertain':
            value=dict(schemaVersion='owner-truth-live-theme-relation-v1',inputHash=body['inputHash'],relation='uncertain',targetTopicId=None)
        elif target is None:
            value=dict(schemaVersion='owner-truth-live-theme-relation-v1',inputHash=body['inputHash'],relation='none',targetTopicId=None)
        else:
            relation=active['relation'];new=material['atoms'];old=target['atoms']
            correction=next((a for a in old if '两年' in a['content'].get('claim','')),old[0])
            claims=[(a['content'].get('claim') or a['content'].get('summary')) for a in new]
            if target['state']=='pending':claims=[(a['content'].get('claim') or a['content'].get('summary')) for a in old if relation!='correction' or a['atomId']!=correction['atomId']]+claims
            value=dict(schemaVersion='owner-truth-live-theme-relation-v1',inputHash=body['inputHash'],relation=relation,
                targetTopicId=target['topicId'],targetVersion=target['version'],targetHash=target['proposalHash'],
                duplicateAtomIds=[a['atomId'] for a in new] if relation=='duplicate' else [],
                replaces={new[0]['atomId']:correction['atomId']} if relation=='correction' else {},title='杭州求学',summary='；'.join(claims))
            if target['state'] in ('accepted','rejected'):
                value.update(title=material['theme']['title'],summary=material['theme']['summary']+'【模型错误混入旧事实】')
            if relation=='correction' and not active.get('omit_intent',False):
                value['correctionEvidence']={new[0]['atomId']:dict(evidenceId=new[0]['evidence'][0]['evidenceId'],quote=new[0]['evidence'][0]['text'])}
    elif 'atoms' in body:
        if 'proposal' in body:
            value=dict(schemaVersion='owner-truth-live-theme-support-v1',inputHash=body['inputHash'],proposalHash=body['proposalHash'],
                themes=[dict(key=t['key'],atomIds=t['atomIds'],evidenceIds=t['evidenceIds'],verdict='supported',sameSubjectEvent=True,
                    compatibleTime=True,correctionsResolved=True) for t in body['proposal']['themes']])
        elif active.get('deny_safety'):
            atoms=body['atoms'];value=dict(schemaVersion='owner-truth-live-theme-v1',inputHash=body['inputHash'],omittedAtomIds=[],themes=[dict(key='part'+str(i),title='合成主题',summary=a['content'].get('claim') or a['content'].get('summary'),atomIds=[a['atomId']],evidenceIds=a['evidenceIds'],dimensions=a['dimensions']) for i,a in enumerate(atoms)])
        else:
            atoms=body['atoms'];value=dict(schemaVersion='owner-truth-live-theme-v1',inputHash=body['inputHash'],omittedAtomIds=[],
                themes=[dict(key='scene',title='合成主题',summary='；'.join((a['content'].get('claim') or a['content'].get('summary')) for a in atoms),
                    atomIds=[a['atomId'] for a in atoms],evidenceIds=sorted({e for a in atoms for e in a['evidenceIds']}),
                    dimensions=sorted({d for a in atoms for d in a['dimensions']}))])
    else:return active['handler'](request)
    return httpx.Response(200,request=request,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(value,ensure_ascii=False)}}]})
model_server,model_thread=h.start_controlled_model_http(model)
worker=None
try:
    owner,headers,auth=h.FORMAL.login(client,phone='13900000432');headers['X-DreamJourney-QA-Owner-Truth']='1';vault='local-relations'
    with psycopg.connect(DSN) as c:c.execute('INSERT INTO owner_truth.vaults(vault_id,owner_subject_id) VALUES(%s,%s)',(vault,owner))
    capture=OwnerTruthCommandAuthorizationCapture(feature='ownerTruthCandidateReview',policy_version='local-test',policy_revision=1,
        emergency_revision=0,account_generation_hash='a'*64,decision_id_hash='b'*64,audience='authenticated',cohort='internal',client_build=1,
        expires_at=(datetime.now(timezone.utc)+timedelta(minutes=20)).isoformat())
    context=OwnerTruthCommandContext(vault_id=vault,owner_subject_id=owner,actor_subject_id=owner,authorization_capture=capture)
    settings=replace(settings,deepseek_base_url=f'http://127.0.0.1:{model_server.server_port}/chat/completions')
    worker=OwnerTruthCandidateExtractionWorkerRuntime(settings=settings,store=store,worker_id='cross-scene-local')
    def scene(name,text,target=None,relation='none',expected='published',question_only=False, missing_seq=None, unknown_end=False):
        sid,tid=str(uuid.uuid4()),str(uuid.uuid4());base=f'/v2/vaults/{vault}/interview-sessions'
        active.clear();active.update(target=target,relation=relation,omit_intent=name=='ambiguous-omit_intent',deny_intent=name=='ambiguous-deny_intent',relation_failure=name.startswith('relation-'),deny_safety=name=='relation-unsafe')
        texts=text if isinstance(text,list) else [text]
        turns=[t for i,value in enumerate(texts) for t in (dict(index=2*i+1,role='user',text=value),dict(index=2*i+2,role='assistant',text='我听到了。'))]
        active['handler']=h.controlled_extractor(settings,turns=turns,memories=[] if question_only else [_typed_memory(value,[2*i+1]) for i,value in enumerate(texts)],store=store,expose_handler=True)[0]
        r=client.post(base,headers=headers,json=dict(commandId=name,threadId=tid,sessionId=sid,entryMode='live',productSessionId=name,recoveryProtocol='live-recovery-v1'));assert r.status_code==201,r.text
        receipt=r.json()['receipt']
        for turn in turns:
            if turn['index']==missing_seq:continue
            r=client.post(base+'/'+sid+'/messages',headers=headers,json=dict(commandId=name+str(turn['index']),threadId=tid,messageId=str(uuid.uuid4()),
                expectedThreadVersion=receipt['threadVersion'],expectedSessionVersion=receipt['sessionVersion'],text=turn['text'],
                role='owner' if turn['role']=='user' else 'assistant',captureMode='live',clientSequenceNumber=turn['index'],capturedAt=(datetime(2026,10,2,tzinfo=timezone.utc)+timedelta(seconds=(turn['index']-1)*15)).isoformat()))
            assert r.status_code==201,r.text;receipt=r.json()['receipt']
        with store.request_unit_of_work(correlation_id=name,command_id=name):store.owner_truth_live_recovery_repository().authorize_publication(session_id=sid,context=context,authority_epoch=receipt['authorityEpoch'])
        r=client.post(base+'/'+sid+'/recovery',headers=headers,json=dict(protocol='live-recovery-v1',generation=1,action='close',finalSequence=None if unknown_end else len(turns)));assert r.status_code==200,r.text
        if missing_seq or unknown_end:
            with store.request_unit_of_work(correlation_id=name+'-freeze',command_id=name+'-freeze'):
                store.owner_truth_live_recovery_repository().scan(now=datetime.now(timezone.utc)+timedelta(seconds=61))
        for i in range(80):
            result=worker.run_once()
            with psycopg.connect(DSN) as c:row=c.execute('SELECT state,publication_manifest,source_id FROM owner_truth.live_recovery_snapshots WHERE session_id=%s ORDER BY revision DESC LIMIT 1',(sid,)).fetchone()
            if row and row[0] in ('published','noChange','failed'):break
            time.sleep(.05)
        assert row and row[0]==expected,(row,result)
        with psycopg.connect(DSN) as c:
            run=c.execute('SELECT state FROM owner_truth.live_memory_runs WHERE product_session_id=%s',(name,)).fetchone()
            assert run and run[0]=='published',('published snapshot must settle run',name,row[0],run)
        cards=client.get(f'/v2/vaults/{vault}/live-memory-themes',headers=headers);assert cards.status_code==200,cards.text
        return cards.json()['themes'],row
    def confirm(card,action='accept'):
        path=f"/v2/vaults/{vault}/live-memory-themes/{card['topicId']}"
        value=dict(commandId=str(uuid.uuid4()),version=card['version'],proposalHash=card['proposalHash'],action=action)
        r=client.post(path+'/preview',headers=headers,json=value);assert r.status_code==200,r.text
        proposal=r.json()['groupProposal'];value.update(expectedMemoryRevision=proposal['baseMemoryRevision'],expectedGroupProposalId=proposal['groupProposalId'],expectedGroupProposalHash=proposal['groupProposalHash'])
        r=client.post(path+'/confirm',headers=headers,json=value);assert r.status_code==201,r.text
        return r.json()
    cards,row=scene('historical-base','我在杭州读了两年书。');old=cards[0]
    confirm(old)
    with psycopg.connect(DSN) as c:
        historical=c.execute("SELECT id,payload,is_current FROM owner_truth.memory_versions ORDER BY id").fetchall()
    gates=[]
    scenarios=[('short-A',['我今年开始学陶艺。','我在这门陶艺课上用蓝色本子记笔记。']),
        ('logical10',[f'2026年我在青岛参加同一次陶艺课程，第{i+1}节学习第{i+1}种花纹。' for i in range(20)]),
        ('short-B',['我每周三在社区教书法。','我用绿色本子记录这门书法课的教学进度。'])]
    for name,texts in scenarios:
        if name=='logical10':assert gates[-1]=='short-A'
        cards,row=scene(name,texts,old['topicId'],'uncertain')
        source=str(row[2]);fresh=[t for t in cards if t['sourceId']==source]
        assert fresh and row[1]['deferredRelations'],(name,row)
        details=[m for t in fresh for m in t['memberDetails'].values()]
        assert {m['statement'] for m in details}==set(texts),(name,details)
        assert len(details)==len(texts) and not row[1]['omittedAtomIds']
        assert all(t.get('linkedTopicId') is None for t in fresh)
        assert all(m['sourceId']==source for t in fresh for m in t['members'].values())
        with psycopg.connect(DSN) as c:
            now=c.execute("SELECT id,payload,is_current FROM owner_truth.memory_versions WHERE id=ANY(%s::uuid[]) ORDER BY id",([r[0] for r in historical],)).fetchall()
            assert now==historical,'historical memory changed without confirmation'
            count=c.execute('SELECT count(*) FROM owner_truth.conversation_messages WHERE session_id=(SELECT session_id FROM owner_truth.live_recovery_snapshots WHERE source_id=%s)',(source,)).fetchone()[0]
            assert count==len(texts)*2
        confirmed=[confirm(t) for t in fresh]
        version_ids=[m['memoryVersionId'] for result in confirmed for m in result['members']]
        store.close_pool()
        store=PostgresStore(dsn=DSN,pool_min_size=1,pool_max_size=5);store.open_pool(wait=True)
        main.store=store
        worker=OwnerTruthCandidateExtractionWorkerRuntime(settings=settings,store=store,worker_id='cross-scene-rebuilt')
        with psycopg.connect(DSN) as c:
            formal=c.execute('SELECT payload FROM owner_truth.memory_versions WHERE id=ANY(%s::uuid[]) AND is_current',(version_ids,)).fetchall()
            assert {r[0]['content']['claim'] for r in formal}==set(texts)
        for result in confirmed:
            for member in result['members']:
                reply=client.get(f"/v2/vaults/{vault}/memories/{member['memoryId']}",headers=headers)
                assert reply.status_code==200,reply.text
        # A settled snapshot must not republish on the next ordinary Worker poll.
        before={t['topicId'] for t in fresh}
        with psycopg.connect(DSN) as c:
            requests_before=c.execute('SELECT provider_request_count FROM owner_truth.live_memory_runs WHERE product_session_id=%s',(name,)).fetchone()[0]
        for _ in range(3):worker.run_once()
        with psycopg.connect(DSN) as c:
            settled=c.execute('SELECT state,provider_request_count FROM owner_truth.live_memory_runs WHERE product_session_id=%s',(name,)).fetchone()
            assert settled==('published',requests_before),('settled poll changed state/budget',settled,requests_before)
        with psycopg.connect(DSN) as c:
            assert c.execute('SELECT count(*) FROM owner_truth.live_memory_topic_revisions WHERE source_id=%s',(source,)).fetchone()[0]==len(fresh)
        gates.append(name)
        results[name]=dict(messages=count,expectedFacts=len(texts),themes=len(fresh),formalVersions=len(version_ids),
            deferredRelations=len(row[1]['deferredRelations']),exactFacts=True,exactSource=True,historyUnchanged=True,reopenedRead=True,idempotent=True)
        print(json.dumps(dict(scene=name,result=results[name]),ensure_ascii=False),flush=True)
    # Default Worker partial publication -> actual HTTP late repair -> revision 2.
    for name,denied in [('relation-warning',False),('relation-unsafe',True)]:
        texts=[f'我在社区参加同一次篆刻课程，第{i+1}次学习第{i+1}种刀法。' for i in range(8)]+['暂不发布的练习安排，我还在核实。' if denied else '我用绿色笔记本记录篆刻练习。']
        cards,row=scene(name,texts)
        m=row[1];source=str(row[2]);fresh=[t for t in cards if t['sourceId']==source]
        assert active.get('invalid_relations',0)>=1 and active.get('safety_calls',0)>=1,active
        assert m['relationDiagnostics'][0]['failureCode'].endswith('unexpectedResolvedMemory'),m
        assert m['relationDiagnostics'][0]['safetyReview']==('blocked' if denied else 'passed'),m
        assert (m['completeness']=='partial')==denied,m
        assert not any(b['reason']=='relationResolutionFailed' for b in m['blockedThemes'])
        expected=set(texts[:-1]) if denied else set(texts)
        assert {a['statement'] for t in fresh for a in t['memberDetails'].values()}==expected
        with psycopg.connect(DSN) as c:
            sid=str(c.execute('SELECT session_id FROM owner_truth.live_recovery_snapshots WHERE source_id=%s',(source,)).fetchone()[0])
            attempts=c.execute("SELECT a.exposure_state,a.usage,a.response_hash FROM owner_truth.live_memory_provider_attempts a JOIN owner_truth.live_memory_runs r ON r.id=a.run_id WHERE r.product_session_id=%s AND a.stage='relationReviewBatch'",(name,)).fetchall()
        r=client.post(f'/v2/vaults/{vault}/interview-sessions/{sid}/recovery',headers=headers,json={'protocol':'live-recovery-v1','generation':1,'action':'status'})
        assert r.status_code==200,r.text
        assert r.json()['publication']['isPartial']==denied,r.json()
        assert attempts and all(a[0]!='responseAccepted' for a in attempts),attempts
        assert all(a[1]['_diagnostic']['reason']=='unexpectedResolvedMemory' and a[2] for a in attempts)
        confirmed=[confirm(t) for t in fresh]
        ids=[x['memoryVersionId'] for receipt in confirmed for x in receipt['members']]
        # Independent connection reads actual persisted formal content after review.
        with psycopg.connect(DSN) as c:
            content=c.execute('SELECT payload FROM owner_truth.memory_versions WHERE id=ANY(%s::uuid[]) AND is_current',(ids,)).fetchall()
            assert {x[0]['content']['claim'] for x in content}==expected
            before=c.execute('SELECT provider_request_count FROM owner_truth.live_memory_runs WHERE product_session_id=%s',(name,)).fetchone()
        worker.run_once()
        with psycopg.connect(DSN) as c:assert c.execute('SELECT provider_request_count FROM owner_truth.live_memory_runs WHERE product_session_id=%s',(name,)).fetchone()==before
        results[name]=dict(contentComplete=not denied,actualHTTPPartial=denied,safetyCalls=active['safety_calls'],invalidRelationCalls=active['invalid_relations'],publishedStatements=sorted(expected),formalCount=len(ids),settledReplayNoNewCalls=True,manifest=m)
    for kind in ('late-body','late-final'):
        texts=['我每周一学油画。','我每周五练素描。']
        _,first=scene(kind,texts,missing_seq=3 if kind=='late-body' else None,unknown_end=kind=='late-final')
        with psycopg.connect(DSN) as c:
            sid,tid=c.execute('SELECT s.session_id,i.current_thread_id FROM owner_truth.live_recovery_snapshots s JOIN owner_truth.interview_sessions i ON i.id=s.session_id WHERE s.source_id=%s',(first[2],)).fetchone()
            sid,tid=str(sid),str(tid)
            original=c.execute('SELECT snapshot_hash,snapshot FROM owner_truth.live_recovery_snapshots WHERE source_id=%s',(first[2],)).fetchone()
            budget=c.execute('SELECT provider_request_count,reserved_input_tokens,reserved_output_tokens,organization_started_at,budget_policy_hash FROM owner_truth.live_memory_runs WHERE product_session_id=%s',(kind,)).fetchone()
        base=f'/v2/vaults/{vault}/interview-sessions/{sid}'
        if kind=='late-body':
            body=dict(protocol='live-recovery-v1',generation=1,commandId=kind+'3',threadId=tid,messageId=str(uuid.uuid4()),expectedThreadVersion=1,expectedSessionVersion=1,text=texts[1],role='owner',captureMode='live',clientSequenceNumber=3,capturedAt='2026-10-04T00:00:00Z')
            response=client.post(base+'/recovery/messages',headers=headers,json=body)
            assert response.status_code==201,response.text
        else:
            response=client.post(base+'/recovery',headers=headers,json=dict(protocol='live-recovery-v1',generation=1,action='close',finalSequence=4))
            assert response.status_code==200,response.text
        with psycopg.connect(DSN) as c:
            reopened=c.execute('SELECT state,provider_request_count,reserved_input_tokens,reserved_output_tokens,organization_started_at,budget_policy_hash FROM owner_truth.live_memory_runs WHERE product_session_id=%s',(kind,)).fetchone()
            assert reopened[0]=='organizing' and reopened[1:]==budget,('late repair must retain budget',reopened,budget)
        for _ in range(80):
            worker.run_once()
            with psycopg.connect(DSN) as c:
                last=c.execute('SELECT revision,state,publication_manifest FROM owner_truth.live_recovery_snapshots WHERE session_id=%s ORDER BY revision DESC LIMIT 1',(sid,)).fetchone()
            if last[0]==2 and last[1]=='published':break
            time.sleep(.05)
        assert last[0:2]==(2,'published'),last
        assert last[2]['completeness']=='receivedComplete'
        with psycopg.connect(DSN) as c:
            assert c.execute('SELECT snapshot_hash,snapshot FROM owner_truth.live_recovery_snapshots WHERE source_id=%s',(first[2],)).fetchone()==original
            before=c.execute('SELECT state,provider_request_count FROM owner_truth.live_memory_runs WHERE product_session_id=%s',(kind,)).fetchone()
            assert before[0]=='published',before
        if kind=='late-body':
            response=client.post(base+'/recovery/messages',headers=headers,json=body)
            assert response.status_code==200,response.text
        else:
            response=client.post(base+'/recovery',headers=headers,json=dict(protocol='live-recovery-v1',generation=1,action='close',finalSequence=4))
            assert response.status_code==200,response.text
        with store.request_unit_of_work(correlation_id='settled-scan',command_id='settled-scan'):
            store.owner_truth_live_recovery_repository().scan(now=datetime.now(timezone.utc)+timedelta(seconds=180))
        worker.run_once()
        with psycopg.connect(DSN) as c:
            assert c.execute('SELECT state,provider_request_count FROM owner_truth.live_memory_runs WHERE product_session_id=%s',(kind,)).fetchone()==before
            coords=c.execute('SELECT coordinates FROM owner_truth.live_recovery_sessions WHERE session_id=%s',(sid,)).fetchone()[0]
            assert not coords.get('firstPlannerFailure'),coords.get('firstPlannerFailure')
            assert len(coords['snapshots'])==2
        results[kind]=dict(partialSettled=True,reopenedSameBudget=True,secondRevisionPublished=True,duplicateIdempotent=True,scanNoFalseFailure=True)
    _,row=scene('no-facts','你能讲一个故事吗？',question_only=True,expected='noChange')
    results['no-facts']=dict(noChangeSettlesRun=True)
    _,row=scene('empty-scene',[],expected='noChange')
    results['empty-scene']=dict(noOwnerSource=True,noChangeSettlesRun=True)
    result=dict(database=DB,status='LOCAL_PG_PASS',scope='real local HTTP API + production Worker runtime + isolated PostgreSQL; controlled model HTTP; logical timestamps, not physical minutes',results=results,modelRequests=len(model_requests))
    (OUTPUT/'pg-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False,indent=2))
finally:
    client.close();server.should_exit=True;thread.join(timeout=10);model_server.shutdown();model_thread.join(timeout=5);store.close_pool()
