"""Local PG + actual CLI process + loopback model HTTP; no real provider calls."""
import json
import os
import subprocess
import sys
import time
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlparse
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.services.postgres_store import PostgresStore
from app.services.owner_truth_candidate_review import OwnerTruthCandidateReviewService
from app.services.owner_truth_person_memory_profile import OwnerTruthPersonMemoryProfileService
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.domain.owner_truth.ontology import enrich_memory_payload_v5
from app.domain.owner_truth.contracts import MemoryKind
from app.domain.owner_truth.candidate_decisions import OwnerTruthCandidateReviewCommand, CandidateReviewAction

parsed = urlparse(os.environ['LOCAL_LIVE_TEST_ADMIN_DSN'])
assert parsed.hostname in {'127.0.0.1', 'localhost'}, 'local PG only'
admin = parsed._replace(path='/postgres').geturl()
name = 's5_watch_' + uuid4().hex[:10]
dsn = parsed._replace(path='/'+name).geturl()
out = Path(os.environ['LOCAL_LIVE_TEST_OUTPUT']); out.mkdir(parents=True, exist_ok=False)
calls = []
mode = {"fail": False}

class Model(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        material = json.loads(request['messages'][1]['content'])
        audit = 'original' in material
        original = material['original'] if audit else material
        ids = [s['sentenceId'] for s in original['sentences']]
        if audit:
            value = dict(verdict='uncertain' if mode['fail'] else 'supported', issues=['synthetic uncertain'] if mode['fail'] else [],
                facts=[dict(sentenceId=i, covered=True, qualifiersPreserved=True) for i in ids],
                sentences=[dict(index=i, supported=True) for i in range(len(material['proposed']['sentences']))])
        else:
            text = ('我偶尔喜欢喝咖啡，大学时也偶尔喝它提神。' if '咖啡' in original['title']
                    else '我打算整理照片，也喜欢用相册来整理它们。')
            if '蓝色杯子' in str(original):
                text += '喝咖啡时我用的是蓝色杯子。'
            if '桌边' in str(original):
                text += '我把咖啡杯搁在桌边。'
            value = dict(paragraph=text, sentences=[dict(text=text, sourceSentenceIds=ids)])
        calls.append(dict(stage='audit' if audit else 'write', title=original['title']))
        data = json.dumps(dict(model='local-controlled', choices=[dict(message=dict(content=json.dumps(value, ensure_ascii=False)))]), ensure_ascii=False).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)

server = ThreadingHTTPServer(('127.0.0.1', 0), Model)
Thread(target=server.serve_forever, daemon=True).start()
with psycopg.connect(admin, autocommit=True) as c:
    c.execute(psycopg.sql.SQL('CREATE DATABASE {} TEMPLATE template0').format(psycopg.sql.Identifier(name)))
store = child = None
logs = []
try:
    PostgresMigrator(dsn=dsn, migrations_dir=default_migrations_dir(), build_id='s5-watch').apply()
    ctx = OwnerTruthCommandContext(vault_id='s5-watch-'+uuid4().hex, owner_subject_id='s5-owner', actor_subject_id='s5-owner')
    other = OwnerTruthCommandContext(vault_id='s5-other-'+uuid4().hex, owner_subject_id='s5-owner', actor_subject_id='s5-owner')
    with psycopg.connect(dsn) as c:
        for scope in [ctx, other]:
            c.execute('INSERT INTO owner_truth.vaults(vault_id,owner_subject_id) VALUES (%s,%s)', (scope.vault_id, scope.owner_subject_id))
    store = PostgresStore(dsn=dsn, pool_min_size=1, pool_max_size=2); store.open_pool(wait=True)

    def activate(text, scope=ctx):
        source, candidate = str(uuid4()), str(uuid4())
        content = enrich_memory_payload_v5(kind=MemoryKind.EXPERIENCE, payload={'summary':text, 'facets':{**{k:[] for k in ('people','time','places','relationships','emotions','values','personality')}, 'confidence':1.0}})
        h = sha256(json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        payload = dict(schemaVersion='owner-truth-candidate-v1', candidateKind='experience', content=content, contentSchemaVersion='owner-truth-v5', evidenceRefs=[dict(sourceId=source, sourceVersion=1)])
        with psycopg.connect(dsn) as c:
            c.execute("INSERT INTO owner_truth.sources(id,vault_id,owner_subject_id,source_kind,content_hash,policy_version,authority_epoch) VALUES (%s,%s,%s,'text',%s,%s,0)", (source, scope.vault_id, scope.owner_subject_id, h, scope.policy_version))
            c.execute("""INSERT INTO owner_truth.memory_candidates(id,vault_id,owner_subject_id,source_id,candidate_kind,perspective_type,epistemic_status,sensitivity,policy_version,authority_epoch,content_hash,payload_schema_version,payload)
                VALUES (%s,%s,%s,%s,'experience','firstPerson','recalled','standard',%s,0,%s,'owner-truth-v5',%s)""", (candidate,scope.vault_id,scope.owner_subject_id,source,scope.policy_version,h,Jsonb(payload)))
        service = OwnerTruthCandidateReviewService(store)
        preview = service.preview_changeset(candidate_id=candidate, context=scope)
        command = OwnerTruthCandidateReviewCommand(command_id='s5-'+candidate, candidate_id=candidate, expected_candidate_version=1,
            expected_memory_revision=preview.change_set.base_memory_revision, expected_change_set_id=preview.change_set.change_set_id,
            expected_proposal_hash=preview.proposal_hash, action=CandidateReviewAction.ACCEPT, corrected_value=None,
            corrected_value_schema_version='owner-truth-v5', reason_code='ownerReviewed')
        return service.decide_and_activate(command=command, context=scope).memory_activation.memory_version_id

    versions = [activate('大学时我偶尔喝咖啡提神。'), activate('我偶尔喜欢喝咖啡。')]
    activate('我打算整理照片。', other); activate('我喜欢用相册整理照片。', other)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', STORE_BACKEND='postgres', DATABASE_URL=dsn,
        OWNER_TRUTH_BIOGRAPHY_TOPICS_ENABLED='true', DB_POOL_MIN_SIZE='1', DB_POOL_MAX_SIZE='2',
        DEEPSEEK_API_KEY='local-controlled-only', DEEPSEEK_BASE_URL=f'http://127.0.0.1:{server.server_port}/chat/completions')
    cmd = [sys.executable, '-m', 'app.async_effects.owner_truth_biography_worker', '--vault-id', ctx.vault_id,
        '--owner-subject-id', ctx.owner_subject_id, '--watch', '--poll-seconds', '15', '--max-jobs', '1']

    def start(label):
        log = (out/(label+'.log')).open('w'); logs.append(log)
        return subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)

    def wait_ready(count):
        until = time.monotonic()+55
        while time.monotonic()<until:
            assert child.poll() is None, 'worker process exited'
            with psycopg.connect(dsn) as c:
                rows = c.execute('SELECT state FROM owner_truth.biography_topic_jobs WHERE vault_id=%s', (ctx.vault_id,)).fetchall()
            if sum(s[0]=='ready' for s in rows)==count: return
            assert not any(s[0]=='fallback' for s in rows), rows
            time.sleep(.25)
        raise AssertionError('automatic watch did not complete within bound')

    def formal_snapshot():
        with psycopg.connect(dsn) as c:
            return c.execute('SELECT id,content_hash,payload FROM owner_truth.memory_versions WHERE id=ANY(%s::uuid[]) ORDER BY id', (versions,)).fetchall()

    before = formal_snapshot()
    child = start('first-process'); wait_ready(1); assert len(calls)==2
    assert formal_snapshot()==before
    versions += [activate('我打算整理照片。'), activate('我喜欢用相册整理照片。')]
    before = formal_snapshot(); wait_ready(2); assert len(calls)==4
    assert formal_snapshot()==before
    child.terminate(); child.wait(timeout=10); child = start('restarted-process')
    time.sleep(17); assert child.poll() is None and len(calls)==4
    with psycopg.connect(dsn) as c:
        assert c.execute('SELECT count(*) FROM owner_truth.biography_topic_jobs WHERE vault_id=%s', (other.vault_id,)).fetchone()[0]==0
    store.close_pool(); store=PostgresStore(dsn=dsn, pool_min_size=1, pool_max_size=2); store.open_pool(wait=True)
    with store.request_unit_of_work(correlation_id='s5-cold',command_id='s5-cold'):
        from app.services.owner_truth_biography import biography_model_config
        from app.core.config import Settings
        from dataclasses import replace
        profile=OwnerTruthPersonMemoryProfileService(store, biography_topics=True,
            composition_config=biography_model_config(replace(Settings.from_env(),deepseek_base_url=env['DEEPSEEK_BASE_URL']))).read(context=ctx)
        blocks=[b for s in profile.memory_model['biographyProjection']['sections'] for b in s['blocks']]
        assert len(blocks)==2 and all(b['qualityState']=='polished' for b in blocks), blocks

    # Stop the watcher to control the fault boundary, keeping actual CLI code.
    child.terminate(); child.wait(timeout=10); child=None
    old_blocks={b['topicId']:b for b in blocks};old_coffee=next(b for b in blocks if '咖啡' in b['text'])
    old_photo=next(b for b in blocks if '照片' in b['text'])
    def read_blocks():
        with store.request_unit_of_work(correlation_id='rewrite-cold',command_id='rewrite-cold'):
            profile=OwnerTruthPersonMemoryProfileService(store,biography_topics=True,
                composition_config=biography_model_config(replace(Settings.from_env(),deepseek_base_url=env['DEEPSEEK_BASE_URL']))).read(context=ctx)
            return [b for sec in profile.memory_model['biographyProjection']['sections'] for b in sec['blocks']]
    def once(label):
        command=cmd[:cmd.index('--watch')]+['--max-jobs','1']
        result=subprocess.run(command,cwd=ROOT,env=env,capture_output=True,text=True,timeout=75)
        (out/(label+'.log')).write_text(result.stdout+result.stderr)
        assert result.returncode==0,result.stderr
        return json.loads(result.stdout)
    mode['fail']=True
    versions.append(activate('我用蓝色杯子喝咖啡。'))
    before=formal_snapshot();assert once('uncertain-after-add')['status']=='pending'
    with psycopg.connect(dsn) as c:c.execute("UPDATE owner_truth.biography_topic_jobs SET available_at=NOW()-INTERVAL '1 second' WHERE state='pending'")
    assert once('uncertain-after-add-budget-exhausted')['status']=='fallback'
    assert formal_snapshot()==before
    store.close_pool();store=PostgresStore(dsn=dsn,pool_min_size=1,pool_max_size=2);store.open_pool(wait=True)
    retained=read_blocks();coffee=next(b for b in retained if b['topicId']==old_coffee['topicId'])
    assert coffee['qualityState']=='retainedWithAdditions'
    assert coffee['text'].startswith(old_coffee['text']) and '蓝色杯子' in coffee['text']
    assert next(b for b in retained if b['topicId']==old_photo['topicId'])==old_photo
    with psycopg.connect(dsn) as c:
        assert c.execute("SELECT error_code FROM owner_truth.biography_topic_jobs WHERE vault_id=%s AND source_revision=%s",(ctx.vault_id,coffee['sourceRevision'])).fetchone()[0]=='auditUncertain'
    call_count=len(calls)
    for _ in range(10):assert read_blocks()==retained
    assert len(calls)==call_count
    mode['fail']=False;versions.append(activate('我把咖啡杯放在桌边。'))
    before=formal_snapshot();assert once('new-revision-polished')['status']=='ready';assert formal_snapshot()==before
    fresh=read_blocks();coffee=next(b for b in fresh if b['topicId']==old_coffee['topicId'])
    assert coffee['qualityState']=='polished' and '桌边' in coffee['text']
    assert next(b for b in fresh if b['topicId']==old_photo['topicId'])==old_photo
    # Revoke a supporting source: neither old nor new prose may reintroduce it.
    with psycopg.connect(dsn) as c:
        source=c.execute('SELECT source_id FROM owner_truth.memory_versions WHERE id=%s',(versions[0],)).fetchone()[0]
        c.execute("UPDATE owner_truth.sources SET state='deleted' WHERE id=%s",(source,))
    safe=read_blocks();coffee=next(b for b in safe if b['topicId']==old_coffee['topicId'])
    assert coffee['qualityState']=='faithful' and versions[0] not in json.dumps(coffee)
    assert '大学时' not in coffee['text']
    assert next(b for b in safe if b['topicId']==old_photo['topicId'])==old_photo
    disabled = subprocess.run(cmd, cwd=ROOT, env={**env,'OWNER_TRUTH_BIOGRAPHY_TOPICS_ENABLED':'false','DATABASE_URL':'postgresql://invalid.invalid/must-not-connect'},capture_output=True,text=True,timeout=5)
    assert disabled.returncode==0 and json.loads(disabled.stdout)['status']=='disabled'
    result=dict(status='PASS', sameOwnerOtherVaultUntouched=True, additionsRetainSafeProse=True, uncertainClassified=True, newRevisionLocallyReplaces=True, revocationRejectsAllOldProse=True, tenReadsNoRequests=True, automaticInitial=True, automaticLaterConfirmation=True, restartNoDuplicateCalls=True,
        otherVaultUntouched=True, formalVersionsUnchanged=True, coldPolishedBlocks=2, disabledNoDB=True,
        modelRequests=calls, provider='controlled loopback HTTP; real Provider NOT_RUN')
    (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)); print(json.dumps(result,ensure_ascii=False))
finally:
    if child and child.poll() is None: child.terminate(); child.wait(timeout=10)
    for log in logs: log.close()
    if store: store.close_pool()
    server.shutdown(); server.server_close()
    with psycopg.connect(admin,autocommit=True) as c:
        c.execute(psycopg.sql.SQL('DROP DATABASE {} WITH (FORCE)').format(psycopg.sql.Identifier(name)))
