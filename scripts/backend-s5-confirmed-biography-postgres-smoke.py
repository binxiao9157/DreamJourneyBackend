"""Read bindings through real theme confirmations seeded by the Live PG smoke.

All adversarial SQL is rolled back. Only localhost disposable databases allowed.
"""
import os, sys, json
from pathlib import Path
from dataclasses import replace
from copy import deepcopy
from urllib.parse import urlparse
import psycopg
import subprocess, time
from threading import Thread
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.services.postgres_store import PostgresStore
from app.services.owner_truth_person_memory_profile import OwnerTruthPersonMemoryProfileService, _person_model_entry
from app.services.owner_truth_biography import PostgresBiographyRepository
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.domain.owner_truth.person_biography import digest

dsn = os.environ['TEST_LIVE_RECOVERY_DSN']
p = urlparse(dsn)
assert p.hostname in {'localhost', '127.0.0.1'} and p.path.startswith('/dj_live_recovery_')
out = Path(os.environ['LOCAL_LIVE_TEST_OUTPUT']); out.mkdir(parents=True, exist_ok=True)
store = PostgresStore(dsn=dsn, pool_min_size=1, pool_max_size=2); store.open_pool(wait=True)
checks = []
try:
    with psycopg.connect(dsn) as c:
        vault, owner = c.execute("SELECT vault_id,owner_subject_id FROM owner_truth.live_memory_runs WHERE product_session_id='short-A'").fetchone()
    ctx = OwnerTruthCommandContext(vault_id=vault, owner_subject_id=owner, actor_subject_id=owner)
    service = OwnerTruthPersonMemoryProfileService(store, biography_topics=True)
    with store.request_unit_of_work(correlation_id='bindings', command_id='bindings'):
        entries = tuple(_person_model_entry(m) for m in service._all_current_memories(context=ctx))
        profile = service.read(context=ctx)
    original = profile.memory_model
    blocks = [b for s in original['biographyProjection']['sections'] for b in s['blocks']]
    with psycopg.connect(dsn) as c:
        repo = PostgresBiographyRepository(c)
        bindings = repo.confirmed_topic_bindings(context=ctx, entries=entries)
        # The seed also exercises resolved relationships: eight activated facts
        # differ from their original candidates and must NOT inherit that theme.
        assert len(bindings) == 26 and len(entries) == 34, (len(bindings), len(entries))
        excluded = c.execute("""SELECT m.id FROM owner_truth.memories m
            JOIN owner_truth.memory_versions v ON v.memory_id=m.id AND v.is_current
            JOIN owner_truth.decision_receipts d ON d.id=v.decision_receipt_id
            JOIN owner_truth.memory_candidates c ON c.id=d.candidate_id
            WHERE m.vault_id=%s AND m.status='active' AND v.payload->'content'<>c.payload->'content'""",(vault,)).fetchall()
        assert len(excluded)==8 and all(str(row[0]) not in bindings for row in excluded)
        checks.append('eight changed formal contents excluded from stale theme bindings')
        assert sorted(e['memoryVersionId'] for e in entries) == sorted(e['memoryVersionId'] for b in blocks for e in b['evidence'])
        for scene, count in [('short-A',2), ('logical10',20), ('short-B',2)]:
            source = str(c.execute('SELECT source_id FROM owner_truth.live_recovery_snapshots s JOIN owner_truth.interview_sessions i ON i.id=s.session_id WHERE i.product_session_id=%s', (scene,)).fetchone()[0])
            group = [b for b in blocks if any(e['sourceId']==source for e in b['evidence'])]
            assert sum(len(b['evidence']) for b in group)==count
            assert len(group)<count, (scene, group)
            checks.append(dict(scene=scene, formalFacts=count, groupedBlocks=len(group)))
        assert repo.confirmed_topic_bindings(context=replace(ctx, owner_subject_id='wrong-owner'), entries=entries)=={}
        assert repo.confirmed_topic_bindings(context=replace(ctx, vault_id='wrong-vault'), entries=entries)=={}
        for field in ('contentHash','memoryId','sourceId'):
            changed=deepcopy(entries)
            for e in changed:e[field]='wrong'
            assert repo.confirmed_topic_bindings(context=ctx, entries=changed)=={}, field
        checks.append('owner/vault/version-content/source guards')
        c.rollback()
        target = next(iter(bindings.values()))
        # Mutable lifecycle/ownership guards; transactions are always rolled back.
        for query, args in [
            ("UPDATE owner_truth.live_memory_topics SET state='pending' WHERE id=%s", (target['topicId'],)),
            ("UPDATE owner_truth.live_memory_topics SET owner_subject_id='wrong-owner' WHERE id=%s", (target['topicId'],)),
            ("UPDATE owner_truth.memory_candidates SET payload=jsonb_set(payload,'{proposalHash}','\"wrong\"') WHERE id=%s", (target['candidateId'],)),
            ("UPDATE owner_truth.memory_candidates SET payload=jsonb_set(payload,'{content,claim}','\"changed after confirmation\"') WHERE id=%s", (target['candidateId'],)),
        ]:
            try:
                c.execute(query,args)
                assert target['memoryId'] not in repo.confirmed_topic_bindings(context=ctx, entries=entries)
            except psycopg.errors.RaiseException as error:
                assert 'memory_candidates' in query and 'terminal candidate decision is immutable' in str(error)
                checks.append('database prevented terminal candidate corruption before query')
            finally:c.rollback()
        checks.append('pending/foreign-owner excluded; changed content exercised by actual resolved confirmations')
    # Same formal revisions, changed theme eligibility: cached profile must rebuild.
    topic = target['topicId']
    with psycopg.connect(dsn) as c:c.execute("UPDATE owner_truth.live_memory_topics SET state='pending' WHERE id=%s",(topic,))
    try:
        with store.request_unit_of_work(correlation_id='pending',command_id='pending'):
            pending=service.read(context=ctx)
        assert pending.source_fingerprint != profile.source_fingerprint
    finally:
        with psycopg.connect(dsn) as c:c.execute("UPDATE owner_truth.live_memory_topics SET state='accepted' WHERE id=%s",(topic,))
    with store.request_unit_of_work(correlation_id='restored',command_id='restored'):
        restored=service.read(context=ctx)
    (out/'cache-before.json').write_text(json.dumps(original,ensure_ascii=False,indent=2))
    (out/'cache-after.json').write_text(json.dumps(restored.memory_model,ensure_ascii=False,indent=2))
    # Incremental derivation has existing diagnostic/model-version metadata.
    # Every business projection, including exact biography bytes, must match.
    assert {k:v for k,v in restored.memory_model.items() if k not in {'modelVersion','incrementalDerivation'}} == {k:v for k,v in original.items() if k not in {'modelVersion','incrementalDerivation'}}
    checks.append('binding-only cache invalidation and exact restoration')
    with psycopg.connect(dsn) as c:
        assert c.execute('SELECT count(*) FROM owner_truth.biography_topic_jobs').fetchone()[0]==0
    checks.append('profile reads did not admit model work')
    calls=[]
    class Model(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            material=json.loads(body['messages'][1]['content'])
            audit='original' in material
            raw=material['original'] if audit else material
            ids=[s['sentenceId'] for s in raw['sentences']]
            target=any('每周三在社区教书法' in s['text'] for s in raw['sentences'])
            calls.append(dict(stage='audit' if audit else 'write',sentenceCount=len(ids),target=target))
            if audit:
                value=dict(verdict='supported' if target else 'uncertain',issues=[] if target else ['controlled non-target fallback'],
                    facts=[dict(sentenceId=i,covered=True,qualifiersPreserved=True) for i in ids],
                    sentences=[dict(index=i,supported=True) for i in range(len(material['proposed']['sentences']))])
            elif target:
                value=dict(sentences=[dict(text='我每周三在社区教书法，也用绿色本子记录这门书法课的教学进度。',sourceSentenceIds=ids)])
            else:value=dict(sentences=[dict(text=s['text'],sourceSentenceIds=[s['sentenceId']]) for s in raw['sentences']])
            if 'original' not in material:value['paragraph']=''.join(s['text'] for s in value['sentences'])
            data=json.dumps(dict(model='local-controlled',choices=[dict(message=dict(content=json.dumps(value,ensure_ascii=False)))]),ensure_ascii=False).encode()
            self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    server=ThreadingHTTPServer(('127.0.0.1',0),Model);Thread(target=server.serve_forever,daemon=True).start()
    env=dict(os.environ,STORE_BACKEND='postgres',DATABASE_URL=dsn,OWNER_TRUTH_BIOGRAPHY_TOPICS_ENABLED='true',
        DEEPSEEK_API_KEY='local-controlled',DEEPSEEK_BASE_URL=f'http://127.0.0.1:{server.server_port}/chat/completions',DB_POOL_MIN_SIZE='1',DB_POOL_MAX_SIZE='2')
    cmd=[sys.executable,'-m','app.async_effects.owner_truth_biography_worker','--vault-id',vault,'--owner-subject-id',owner,'--watch','--max-jobs','10','--poll-seconds','15']
    def ledger():
        with psycopg.connect(dsn) as c:
            return c.execute('SELECT id,content_hash,payload,is_current FROM owner_truth.memory_versions ORDER BY id').fetchall()
    ledger_before=ledger()
    try:
        with (out/'worker.log').open('w') as log:
            child=subprocess.Popen(cmd,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
            try:
                deadline=time.monotonic()+55
                while time.monotonic()<deadline:
                    assert child.poll() is None
                    with psycopg.connect(dsn) as c:
                        rows=c.execute("SELECT state,result,payload FROM owner_truth.biography_topic_jobs WHERE vault_id=%s AND payload::text LIKE %s",(vault,'%每周三在社区教书法%')).fetchall()
                    if rows and rows[0][0]=='ready':break
                    time.sleep(.1)
                assert rows and rows[0][0]=='ready',rows
                assert len(rows[0][2]['sentences'])==2
                from app.services.owner_truth_biography import biography_model_config
                from app.core.config import Settings
                config=biography_model_config(replace(Settings.from_env(),deepseek_base_url=env['DEEPSEEK_BASE_URL']))
                store.close_pool();store=PostgresStore(dsn=dsn,pool_min_size=1,pool_max_size=2);store.open_pool(wait=True)
                with store.request_unit_of_work(correlation_id='cold-worker',command_id='cold-worker'):
                    cold=OwnerTruthPersonMemoryProfileService(store,biography_topics=True,composition_config=config).read(context=ctx)
                selected=[b for s in cold.memory_model['biographyProjection']['sections'] for b in s['blocks'] if '每周三在社区教书法' in b['text']]
                assert len(selected)==1 and selected[0]['qualityState']=='polished',selected
                assert ledger()==ledger_before
                checks.append('actual watch CLI consumed two confirmed members and cold-read one polished block; formal ledger unchanged')
            finally:child.terminate();child.wait(timeout=10)
    finally:server.shutdown();server.server_close()
    result=dict(status='PASS', entries=len(entries), bindings=len(bindings), blocks=len(blocks), checks=checks,
                controlledModelCalls=calls,scope='local PostgreSQL, actual API-confirmed candidates, actual CLI, controlled loopback HTTP; no real provider calls')
    (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False,indent=2))
finally:store.close_pool()
