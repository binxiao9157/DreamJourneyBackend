"""Cross-entry biography: actual Live API, confirmation, PG and CLI/model HTTP.
Synthetic data only; no external provider or device acceptance.
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
    except ValueError:return active['handler'](request)
    model_requests.append(list(body) if isinstance(body,dict) else [])
    if 'relationScreen' in body:
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
    def scene(name,text,target=None,relation='none',expected='published',question_only=False, missing_seq=None, unknown_end=False, memories=None):
        sid,tid=str(uuid.uuid4()),str(uuid.uuid4());base=f'/v2/vaults/{vault}/interview-sessions'
        active.clear();active.update(target=target,relation=relation,omit_intent=name=='ambiguous-omit_intent',deny_intent=name=='ambiguous-deny_intent')
        texts=text if isinstance(text,list) else [text]
        turns=[t for i,value in enumerate(texts) for t in (dict(index=2*i+1,role='user',text=value),dict(index=2*i+2,role='assistant',text='我听到了。'))]
        active['handler']=h.controlled_extractor(settings,turns=turns,memories=memories if memories is not None else ([] if question_only else [_typed_memory(value,[2*i+1]) for i,value in enumerate(texts)]),store=store,expose_handler=True)[0]
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
    from copy import deepcopy
    from hashlib import sha256
    from psycopg.types.json import Jsonb
    from app.services.owner_truth_candidate_review import OwnerTruthCandidateReviewService
    from app.domain.owner_truth.candidate_decisions import OwnerTruthCandidateReviewCommand, CandidateReviewAction
    from app.domain.owner_truth.ontology import enrich_memory_payload_v5
    from app.domain.owner_truth.contracts import MemoryKind
    from app.services.owner_truth_person_memory_profile import OwnerTruthPersonMemoryProfileService
    from app.services.owner_truth_biography import biography_model_config
    import subprocess
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    fixture=json.loads((ROOT/'tests/fixtures/cross_entry_object.json').read_text())
    contents=fixture['contents']
    # Provider wire shape differs from stored V5 content; retain exact text/null object.
    live_memories=[_typed_memory(content['statement'],[2*i+1]) for i,content in enumerate(contents[:2])]
    cards,row=scene('cross-object-short',[c['statement'] for c in contents[:2]],memories=live_memories)
    fresh=[c for c in cards if c['sourceId']==str(row[2])]
    assert len(fresh)==1 and len(fresh[0]['memberDetails'])==2
    confirmed=confirm(fresh[0]);assert len(confirmed['members'])==2
    other=replace(context,vault_id='cross-entry-other')
    with psycopg.connect(DSN) as c:c.execute('INSERT INTO owner_truth.vaults(vault_id,owner_subject_id) VALUES(%s,%s)',(other.vault_id,owner))

    def activate(content,scope=context):
        source,candidate=str(uuid.uuid4()),str(uuid.uuid4())
        value=enrich_memory_payload_v5(kind=MemoryKind.EXPERIENCE,payload=deepcopy(content))
        for ref in value.get('provenance',{}).get('evidenceRefs',[]):ref['sourceId']=source
        content_hash=sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        payload=dict(schemaVersion='owner-truth-candidate-v1',candidateKind='experience',content=value,contentSchemaVersion='owner-truth-v5',evidenceRefs=[dict(sourceId=source,sourceVersion=1)])
        with psycopg.connect(DSN) as c:
            c.execute("INSERT INTO owner_truth.sources(id,vault_id,owner_subject_id,source_kind,content_hash,policy_version,authority_epoch) VALUES(%s,%s,%s,'text',%s,%s,0)",(source,scope.vault_id,scope.owner_subject_id,content_hash,scope.policy_version))
            c.execute("""INSERT INTO owner_truth.memory_candidates(id,vault_id,owner_subject_id,source_id,candidate_kind,perspective_type,epistemic_status,sensitivity,policy_version,authority_epoch,content_hash,payload_schema_version,payload)
            VALUES(%s,%s,%s,%s,'experience','firstPerson','recalled','standard',%s,0,%s,'owner-truth-v5',%s)""",(candidate,scope.vault_id,scope.owner_subject_id,source,scope.policy_version,content_hash,Jsonb(payload)))
        svc=OwnerTruthCandidateReviewService(store);preview=svc.preview_changeset(candidate_id=candidate,context=scope)
        command=OwnerTruthCandidateReviewCommand(command_id='cross-'+candidate,candidate_id=candidate,expected_candidate_version=1,
            expected_memory_revision=preview.change_set.base_memory_revision,expected_change_set_id=preview.change_set.change_set_id,
            expected_proposal_hash=preview.proposal_hash,action=CandidateReviewAction.ACCEPT,corrected_value=None,
            corrected_value_schema_version='owner-truth-v5',reason_code='ownerReviewed')
        result=svc.decide_and_activate(command=command,context=scope)
        return dict(source=source,version=str(result.memory_activation.memory_version_id))

    activate({'summary':'我偶尔喝咖啡。'})
    activate(contents[2],other)
    mode={'fail':False,'hook':None};calls=[]
    class Model(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            request=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            material=json.loads(request['messages'][1]['content']);audit='original' in material
            raw=material['original'] if audit else material;ids=[s['sentenceId'] for s in raw['sentences']]
            calls.append(dict(stage='audit' if audit else 'write',input=raw))
            if not audit and mode['hook'] is not None:
                hook=mode['hook'];mode['hook']=None;hook()
            if audit:
                value=dict(verdict='uncertain' if mode['fail'] else 'supported',issues=['controlled uncertain'] if mode['fail'] else [],
                    facts=[dict(sentenceId=i,covered=True,qualifiersPreserved=True) for i in ids],
                    sentences=[dict(index=i,supported=True) for i in range(len(material['proposed']['sentences']))])
            else:
                text='我把一个手摇音乐盒放在玄关的木柜上，它是深蓝色的。'
                joined=''.join(s['text'] for s in raw['sentences'])
                if '每周日' in joined:text='玄关木柜上的手摇音乐盒是深蓝色的，我每周日晚上会转动它的手柄，听一小段音乐。'
                if '软布' in joined:text+='我用软布擦拭这个音乐盒。'
                if '不把它' in joined:text+='我不把它借给别人。'
                if '白色垫子' in joined:text+='它下面是我放的白色垫子。'
                value=dict(paragraph=text,sentences=[dict(text=text,sourceSentenceIds=ids)])
            data=json.dumps(dict(model='local-controlled',choices=[dict(finish_reason='stop',message=dict(content=json.dumps(value,ensure_ascii=False)))]),ensure_ascii=False).encode()
            self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    prose_server=ThreadingHTTPServer(('127.0.0.1',0),Model)
    prose_thread=threading.Thread(target=prose_server.serve_forever,daemon=True);prose_thread.start()
    endpoint=f'http://127.0.0.1:{prose_server.server_port}/chat/completions'
    config=biography_model_config(replace(settings,deepseek_base_url=endpoint))
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',STORE_BACKEND='postgres',DATABASE_URL=DSN,
        OWNER_TRUTH_BIOGRAPHY_TOPICS_ENABLED='true',DB_POOL_MIN_SIZE='1',DB_POOL_MAX_SIZE='2',
        DEEPSEEK_API_KEY='local-controlled-only',DEEPSEEK_BASE_URL=endpoint)
    cmd=[sys.executable,'-m','app.async_effects.owner_truth_biography_worker','--vault-id',vault,'--owner-subject-id',owner,'--max-jobs','1']
    def once(label):
        response=subprocess.run(cmd,cwd=ROOT,env=env,capture_output=True,text=True,timeout=75)
        (OUTPUT/(label+'.log')).write_text(response.stdout+response.stderr)
        assert response.returncode==0,response.stderr
        return json.loads(response.stdout)
    def read(scope=context):
        with store.request_unit_of_work(correlation_id='cross-read',command_id='cross-read'):
            model=OwnerTruthPersonMemoryProfileService(store,biography_topics=True,composition_config=config).read(context=scope).memory_model
            return [b for section in model['biographyProjection']['sections'] for b in section['blocks']]
    def ledger():
        with psycopg.connect(DSN) as c:return c.execute('SELECT id,content_hash,payload,is_current FROM owner_truth.memory_versions ORDER BY id').fetchall()
    def target(bs):return next(b for b in bs if '音乐盒' in b['text'])
    def addition(statement):
        c=deepcopy(contents[2]);c.update(statement=statement,summary=statement,event=statement)
        c.pop('semantic',None);c['qualifiers']['validTime']=dict(start=None,end=None,precision='unknown',expression=None)
        c['facets']['time']=[];c['facets']['habits']=[];c['actions']=[];c['predicate']='states'
        return activate(c)
    try:
        formal_before=ledger();other_before=read(other)
        for i in range(3):once('initial-'+str(i))
        assert ledger()==formal_before
        before=read();initial=target(before);assert initial['qualityState']=='polished' and len(initial['evidence'])==2
        unrelated=next(b for b in before if '咖啡' in b['text'])
        count=len(calls);third=activate(contents[2]);formal_before=ledger()
        current=target(read());assert current['topicId']==initial['topicId'] and len(current['evidence'])==3
        assert once('cross-entry-add')['status']=='ready';assert len(calls)==count+2
        # The actual HTTP writer received both old facts and the new entry.
        assert len(calls[-2]['input']['sentences'])==3
        assert {s['text'] for s in calls[-2]['input']['sentences']}=={c['statement'] for c in contents}
        store.close_pool();store=PostgresStore(dsn=DSN,pool_min_size=1,pool_max_size=2);store.open_pool(wait=True);main.store=store
        merged=target(read());assert merged['qualityState']=='polished' and len(merged['evidence'])==3
        assert ledger()==formal_before and read(other)==other_before
        assert next(b for b in read() if '咖啡' in b['text'])==unrelated
        (OUTPUT/'three-facts-merged.json').write_text(json.dumps(merged,ensure_ascii=False,indent=2))
        from unittest.mock import patch
        with patch('app.domain.owner_truth.biography_object_links.RULE_VERSION', 'old-cache-rule'):
            with store.request_unit_of_work(correlation_id='cache-old',command_id='cache-old'):
                cached=OwnerTruthPersonMemoryProfileService(store,biography_topics=True,composition_config=config).read(context=context)
        with store.request_unit_of_work(correlation_id='cache-new',command_id='cache-new'):
            refreshed=OwnerTruthPersonMemoryProfileService(store,biography_topics=True,composition_config=config).read(context=context)
        assert cached.source_fingerprint!=refreshed.source_fingerprint and refreshed.rebuild_mode!='unchanged'
        count=len(calls)
        for _ in range(10):assert target(read())==merged
        assert len(calls)==count
        mode['fail']=True
        fourth=addition('补充玄关木柜上的那个深蓝色手摇音乐盒：我用软布擦拭它。');formal_before=ledger()
        assert once('uncertain-first')['status']=='pending'
        with psycopg.connect(DSN) as c:c.execute("UPDATE owner_truth.biography_topic_jobs SET available_at=NOW()-INTERVAL '1 second' WHERE state='pending'")
        assert once('uncertain-second')['status']=='fallback'
        retained=target(read());assert retained['qualityState']=='retainedWithAdditions' and retained['text'].startswith(merged['text'])
        assert len(retained['evidence'])==4 and ledger()==formal_before
        assert next(b for b in read() if '咖啡' in b['text'])==unrelated
        mode['fail']=False
        addition('补充玄关木柜上的那个深蓝色手摇音乐盒：我不把它借给别人。')
        mode['hook']=lambda:addition('补充玄关木柜上的那个深蓝色手摇音乐盒：我在它下面放了白色垫子。')
        assert once('concurrent-confirm-stale-worker')['status']=='obsolete'
        formal_before=ledger();assert once('latest-revision')['status']=='ready'
        final=target(read());assert len(final['evidence'])==6 and final['qualityState']=='polished'
        assert ledger()==formal_before and read(other)==other_before
        assert next(b for b in read() if '咖啡' in b['text'])==unrelated
        # Revoking the location anchor changes grouping; old ready text is not reused.
        with psycopg.connect(DSN) as c:c.execute("UPDATE owner_truth.sources SET state='deleted' WHERE id=%s",(str(row[2]),))
        after=read();assert all(b['qualityState'] not in ('polished','retainedWithAdditions') for b in after if '音乐盒' in b['text'])
        assert all(e['sourceId']!=str(row[2]) for b in after for e in b['evidence'])
        result=dict(status='ISOLATED_PG_PASS',scope='actual Live API, production confirmation/profile, independent CLI, controlled HTTP; archive candidate fixture at confirmation boundary',
            exactThreeFactRequest=True,formalLedgerUnchanged=True,unrelatedParagraphExact=True,otherVaultExact=True,coldRead=True,ruleVersionInvalidatesCache=True,readOnlyNoModelCalls=True,
            boundedFailureRetainsProse=True,concurrentOldJobObsolete=True,revokedAnchorNotResurrected=True,provider='CONTROLLED_ONLY',device='NOT_RUN',database=DB)
        (OUTPUT/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        (OUTPUT/'requests.json').write_text(json.dumps(calls,ensure_ascii=False,indent=2))
        print(json.dumps(result,ensure_ascii=False,indent=2))
    finally:
        (OUTPUT/'requests.json').write_text(json.dumps(calls,ensure_ascii=False,indent=2))
        prose_server.shutdown();prose_thread.join(timeout=5);prose_server.server_close()
finally:
    client.close();server.should_exit=True;thread.join(timeout=10);model_server.shutdown();model_thread.join(timeout=5);store.close_pool()
