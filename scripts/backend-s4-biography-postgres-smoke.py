"""S4 isolated PostgreSQL business/fault proof; local host only, synthetic data."""
import json, os, sys, subprocess
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlparse
from hashlib import sha256
from dataclasses import replace
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.db.migrator import PostgresMigrator,default_migrations_dir
from app.services.postgres_store import PostgresStore
from app.services.owner_truth_biography import BiographyWorker
from app.services.owner_truth_person_memory_profile import OwnerTruthPersonMemoryProfileService
from app.services.owner_truth_candidate_review import OwnerTruthCandidateReviewService
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.domain.owner_truth.ontology import enrich_memory_payload_v5
from app.domain.owner_truth.contracts import MemoryKind
from app.domain.owner_truth.candidate_decisions import OwnerTruthCandidateReviewCommand,CandidateReviewAction

url=urlparse(os.environ['LOCAL_LIVE_TEST_ADMIN_DSN'])
assert url.hostname in {'127.0.0.1','localhost'}
base=url._replace(path='/').geturl();name='s4_'+uuid4().hex[:10];dsn=base+name
with psycopg.connect(base+'postgres',autocommit=True) as c:
 c.execute(psycopg.sql.SQL('CREATE DATABASE {} TEMPLATE template0 ENCODING \'UTF8\'').format(psycopg.sql.Identifier(name)))
store=None;checks=[]
try:
 PostgresMigrator(dsn=dsn,migrations_dir=default_migrations_dir(),build_id='s4-biography').apply()
 context=OwnerTruthCommandContext(vault_id='vault-s4-'+uuid4().hex,owner_subject_id='owner-s4',actor_subject_id='owner-s4')
 with psycopg.connect(dsn) as c:c.execute('INSERT INTO owner_truth.vaults(vault_id,owner_subject_id) VALUES (%s,%s)',(context.vault_id,context.owner_subject_id))
 def open_store():
  s=PostgresStore(dsn=dsn,pool_min_size=1,pool_max_size=3);s.open_pool(wait=True);return s
 store=open_store()
 def uow():return store.request_unit_of_work(correlation_id='s4-test',command_id='s4-test')
 def activate(text):
  source,candidate=str(uuid4()),str(uuid4());content={'summary':text,'facets':{**{k:[] for k in ('people','time','places','relationships','emotions','values','personality')},'confidence':1.0}};content=enrich_memory_payload_v5(kind=MemoryKind.EXPERIENCE,payload=content);h=sha256(json.dumps(content,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
  payload={'schemaVersion':'owner-truth-candidate-v1','candidateKind':'experience','content':content,'contentSchemaVersion':'owner-truth-v5','evidenceRefs':[{'sourceId':source,'sourceVersion':1}]}
  with psycopg.connect(dsn) as c:
   c.execute("INSERT INTO owner_truth.sources(id,vault_id,owner_subject_id,source_kind,content_hash,policy_version,authority_epoch) VALUES (%s,%s,%s,'text',%s,%s,0)",(source,context.vault_id,context.owner_subject_id,h,context.policy_version))
   c.execute("""INSERT INTO owner_truth.memory_candidates(id,vault_id,owner_subject_id,source_id,candidate_kind,perspective_type,epistemic_status,sensitivity,policy_version,authority_epoch,content_hash,payload_schema_version,payload)
    VALUES (%s,%s,%s,%s,'experience','firstPerson','recalled','standard',%s,0,%s,'owner-truth-v5',%s)""",(candidate,context.vault_id,context.owner_subject_id,source,context.policy_version,h,Jsonb(payload)))
  service=OwnerTruthCandidateReviewService(store)
  proposal=service.preview_changeset(candidate_id=candidate,context=context)
  command=OwnerTruthCandidateReviewCommand(command_id='s4-'+candidate,candidate_id=candidate,expected_candidate_version=1,expected_memory_revision=proposal.change_set.base_memory_revision,expected_change_set_id=proposal.change_set.change_set_id,expected_proposal_hash=proposal.proposal_hash,action=CandidateReviewAction.ACCEPT,corrected_value=None,corrected_value_schema_version='owner-truth-v5',reason_code='ownerReviewed')
  result=service.decide_and_activate(command=command,context=context)
  assert service.decide_and_activate(command=command,context=context).memory_activation.memory_version_id==result.memory_activation.memory_version_id
  return source,result.memory_activation.memory_version_id
 def profile():
  with uow():return OwnerTruthPersonMemoryProfileService(store,biography_topics=True).read(context=context).public_contract()
 calls=[]
 def compose(block):
  assert store._current_uow.get() is None, 'model must run outside transaction'
  calls.append(block['sourceRevision'])
  compose.last_metadata={'requests':[{'stage':'write','requestedModel':'synthetic','reportedModel':'synthetic','httpStatus':200,'usage':{'total_tokens':0},'authorization':'must-not-persist'}]}
  ids=[s['sentenceId'] for s in block['sentences']]
  text={'咖啡习惯':'我偶尔喜欢喝咖啡，大学时也偶尔喝它提神。','照片与摄影':'我打算整理照片，也喜欢用相册来整理它们。'}.get(block['title'],''.join(s['text'] for s in block['sentences']))
  return {'sentences':[{'text':text, 'sourceSentenceIds':ids}],
          'audit':{'verdict':'supported','facts':[{'sentenceId':x,'covered':True,'qualifiersPreserved':True} for x in ids],
                   'sentences':[{'index':0,'supported':True}],'issues':[]}}
 coffee=activate('大学时我偶尔喝咖啡提神。');photo=activate('我打算整理照片。');activate('我喜欢用相册整理照片。');activate('我偶尔喜欢喝咖啡。')
 # Confirmation committed, process/store disappears before any biography call.
 store.close_pool();store=open_store()
 worker=BiographyWorker(store,compose)
 assert worker.run_once(context=context)['status']=='ready'
 assert worker.run_once(context=context)['status']=='ready'
 assert worker.run_once(context=context)['status']=='idle'
 checks.append('confirmation crash/restart durable discovery and idempotent replay')
 first=profile();assert first['memoryCount']==4
 with uow():
  composed=OwnerTruthPersonMemoryProfileService(store,biography_topics=True).read(context=context)
  assert all(b['qualityState']=='polished' for sec in composed.memory_model['biographyProjection']['sections'] for b in sec['blocks'])
 store.close_pool();store=open_store()
 cold=profile()
 for field in ('profileVersion','sourceFingerprint','memoryCount','lifeStory','lifeRecord'):
  assert cold[field]==first[field],field
 first=profile()
 with psycopg.connect(dsn) as c:
  metadata=c.execute("SELECT request_metadata FROM owner_truth.biography_topic_jobs WHERE state='ready' LIMIT 1").fetchone()[0]
  assert metadata[0]['requests'][0]['reportedModel']=='synthetic'
  assert 'authorization' not in json.dumps(metadata)
 checks.append('reviewed prose persists and cold-read evidence validates against current formal versions')
 for _ in range(20):assert profile()==first
 assert len(calls)==2
 checks.append('20 GETs generate zero additional model calls')
 def timeout(block):raise TimeoutError('synthetic')
 activate('我工作时偶尔喝咖啡。')
 w=BiographyWorker(store,timeout);assert w.run_once(context=context)['status']=='pending'
 with psycopg.connect(dsn) as c:c.execute("UPDATE owner_truth.biography_topic_jobs SET available_at=NOW()-INTERVAL '1 second' WHERE state='pending'")
 store.close_pool();store=open_store()
 assert BiographyWorker(store,timeout).run_once(context=context)=={'status':'fallback','attempt':2}
 assert BiographyWorker(store,timeout).run_once(context=context)['status']=='idle'
 assert profile()['memoryCount']==5
 checks.append('timeout across restart consumes cumulative budget; formal facts readable')
 activate('我喜欢观察薄荷。');activate('我把薄荷放在窗边。')
 def late(block):
  activate('我打算给薄荷做观察记录。')
  return compose(block)
 assert BiographyWorker(store,late).run_once(context=context)['status']=='obsolete'
 checks.append('concurrent confirmation during model call rejects stale completion')
 def invalid(block):return {'sentenceIds':['invented-version']}
 assert BiographyWorker(store,invalid).run_once(context=context)['status']=='pending'
 with psycopg.connect(dsn) as c:c.execute("UPDATE owner_truth.biography_topic_jobs SET available_at=NOW()-INTERVAL '1 second' WHERE state='pending'")
 assert BiographyWorker(store,invalid).run_once(context=context)['status']=='fallback'
 checks.append('invalid provider reference falls back without undoing formal confirmation')
 # Claimed job survives a lost worker and its attempt is not reset.
 activate('我出生于浙江台州。')
 worker=BiographyWorker(store,compose)
 with uow():
  blocks=worker._snapshot(context);repo=store.owner_truth_biography_repository()
  for b in blocks:repo.ensure(context=context,block=b)
  old=repo.claim(context=context);assert old
 with psycopg.connect(dsn) as c:c.execute("UPDATE owner_truth.biography_topic_jobs SET lease_until=NOW()-INTERVAL '1 second' WHERE lease_token=%s",(old['lease_token'],))
 store.close_pool();store=open_store()
 with uow():
  repo=store.owner_truth_biography_repository();new=repo.claim(context=context)
  assert new['attempts']==2 and new['lease_token']!=old['lease_token']
  assert not repo.settle(old,state='ready',result=compose(old['payload']) if False else {'sentenceIds':[]})
  assert repo.settle(new,state='fallback',error='syntheticCrashBudget')
 checks.append('expired lease cannot publish after re-claim, attempt budget survives')
 # Revoke source AFTER a previously successful artifact exists.
 with psycopg.connect(dsn) as c:c.execute("UPDATE owner_truth.sources SET state='deleted' WHERE id=%s",(photo[0],))
 after=profile();assert photo[1] not in json.dumps(after['lifeStory'])
 with uow():
  from unittest.mock import patch
  from app.services.owner_truth_person_memory_profile import OwnerTruthPersonMemoryProfileError
  activate_unused=False
  with patch('app.services.owner_truth_person_memory_profile.build_person_memory_model_incremental',side_effect=OwnerTruthPersonMemoryProfileError('injected')):
   # Force next changed source set through failed incremental and safe full fallback.
   with psycopg.connect(dsn) as c:c.execute("UPDATE owner_truth.sources SET state='deleted' WHERE id=%s",(coffee[0],))
   safe=OwnerTruthPersonMemoryProfileService(store,biography_topics=True).read(context=context).public_contract()
   assert coffee[1] not in json.dumps(safe['lifeStory'])
 checks.append('revocation and failed incremental rebuild cannot return revoked artifact')
 other=replace(context,actor_subject_id='other',owner_subject_id='other')
 try:
  with uow():OwnerTruthPersonMemoryProfileService(store,biography_topics=True).read(context=other)
 except Exception:pass
 else:raise AssertionError('cross owner profile accepted')
 checks.append('cross-account profile denied')
 # Two real connections compete for one durable job; only one lease wins.
 for _ in range(20):
  if BiographyWorker(store,compose).run_once(context=context)['status']=='idle':break
 activate('我喜欢阅读科幻小说。')
 with uow():
  all_blocks=BiographyWorker(store,compose)._snapshot(context)
  repo=store.owner_truth_biography_repository()
  for b in all_blocks:repo.ensure(context=context,block=b)
  repo.retire_other_revisions(context=context,blocks=all_blocks)
 from concurrent.futures import ThreadPoolExecutor
 def race_claim(_):
  with uow():return store.owner_truth_biography_repository().claim(context=context)
 with ThreadPoolExecutor(max_workers=2) as executor:claims=list(executor.map(race_claim,range(2)))
 assert sum(c is not None for c in claims)==1
 winner=next(c for c in claims if c)
 with uow():assert store.owner_truth_biography_repository().settle(winner,state='fallback',error='raceProof')
 checks.append('two concurrent connections claim a single job exactly once')
 # Durable admission transaction rolls back entirely, including budget.
 from copy import deepcopy
 arbitrary=deepcopy(all_blocks[0]);arbitrary['sourceRevision']='f'*64
 try:
  with uow():
   store.owner_truth_biography_repository().ensure(context=context,block=arbitrary)
   raise RuntimeError('synthetic rollback')
 except RuntimeError:pass
 with psycopg.connect(dsn) as c:assert c.execute("SELECT count(*) FROM owner_truth.biography_topic_jobs WHERE source_revision=%s",('f'*64,)).fetchone()[0]==0
 checks.append('task admission rollback leaves no task or attempt')
 # A separate interpreter commits its claim, then exits without any cleanup.
 activate('我喜欢在公园散步。')
 with uow():
  for b in BiographyWorker(store,compose)._snapshot(context):store.owner_truth_biography_repository().ensure(context=context,block=b)
 child = """import os,sys
from app.services.postgres_store import PostgresStore
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
s=PostgresStore(dsn=sys.argv[1],pool_min_size=1,pool_max_size=1);s.open_pool(wait=True)
c=OwnerTruthCommandContext(vault_id=sys.argv[2],owner_subject_id='owner-s4',actor_subject_id='owner-s4')
with s.request_unit_of_work(correlation_id='crash-proof',command_id='crash-proof'):
 assert s.owner_truth_biography_repository().claim(context=c)
os._exit(77)
"""
 exited=subprocess.run([sys.executable,'-c',child,dsn,context.vault_id],capture_output=True,timeout=15)
 assert exited.returncode==77,exited.stderr.decode()
 with psycopg.connect(dsn) as c:
  assert c.execute("SELECT count(*) FROM owner_truth.biography_topic_jobs WHERE state='running' AND attempts=1").fetchone()[0]==1
  c.execute("UPDATE owner_truth.biography_topic_jobs SET lease_until=NOW()-INTERVAL '1 second' WHERE state='running'")
 resumed=BiographyWorker(store,compose).run_once(context=context)
 assert resumed=={'status':'ready','attempt':2},resumed
 checks.append('separate process exits after claim commit; restart completes within remaining budget')
 # Rollback flag reads current facts as well, never the pre-revocation cache.
 with uow():
  legacy=OwnerTruthPersonMemoryProfileService(store,biography_topics=False).read(context=context)
  assert photo[1] not in json.dumps(legacy.public_contract()['lifeStory'])
 checks.append('strategy-off rollback does not expose revoked facts')

 # A fact-complete but verbatim writer result is a quality fallback, not ready.
 activate('我每周六上午在家练习折纸。');activate('我把折纸放在蓝色收纳盒里。')
 def verbatim(block):
  ids=[s['sentenceId'] for s in block['sentences']]
  return {'sentences':[{'text':''.join(s['text'] for s in block['sentences']),'sourceSentenceIds':ids}],
          'audit':{'verdict':'supported','facts':[{'sentenceId':x,'covered':True,'qualifiersPreserved':True} for x in ids],
                   'sentences':[{'index':0,'supported':True}],'issues':[]}}
 assert BiographyWorker(store,verbatim).run_once(context=context)['status']=='pending'
 with psycopg.connect(dsn) as c:c.execute("UPDATE owner_truth.biography_topic_jobs SET available_at=NOW()-INTERVAL '1 second' WHERE state='pending'")
 assert BiographyWorker(store,verbatim).run_once(context=context)['status']=='fallback'
 with psycopg.connect(dsn) as c:
  assert c.execute("SELECT count(*) FROM owner_truth.biography_topic_jobs WHERE error_code='compositionNotIntegrated'").fetchone()[0]==1
 with uow():
  fallback=OwnerTruthPersonMemoryProfileService(store,biography_topics=True).read(context=context)
  folded=[b for s in fallback.memory_model['biographyProjection']['sections'] for b in s['blocks'] if b['title']=='折纸']
 assert len(folded)==1 and folded[0]['qualityState']=='faithful' and len(folded[0]['evidence'])==2
 assert BiographyWorker(store,verbatim).run_once(context=context)['status']=='idle'
 checks.append('untyped same-topic facts group; verbatim quality fallback retains both facts and does not loop')

 with psycopg.connect(dsn,row_factory=dict_row) as c:
  states=c.execute('SELECT state,count(*) AS count,max(attempts) AS max_attempts FROM owner_truth.biography_topic_jobs GROUP BY state ORDER BY state').fetchall()
 print(json.dumps({'status':'PASS','checks':checks,'jobStates':states,'provider':'controlled; real provider NOT_RUN','device':'NOT_RUN'},ensure_ascii=False,indent=2))
finally:
 if store:store.close_pool()
 with psycopg.connect(base+'postgres',autocommit=True) as c:c.execute(psycopg.sql.SQL('DROP DATABASE {}').format(psycopg.sql.Identifier(name)))
