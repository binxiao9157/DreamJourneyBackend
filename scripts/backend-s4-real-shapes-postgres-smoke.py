"""Full real payloads -> confirmation -> isolated PG -> real model prose."""
import json,os,sys,subprocess
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlparse
from hashlib import sha256
from copy import deepcopy
import psycopg
from psycopg.types.json import Jsonb
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
from app.db.migrator import PostgresMigrator,default_migrations_dir
from app.services.postgres_store import PostgresStore
from app.services.owner_truth_biography import BiographyWorker
from app.services.owner_truth_person_memory_profile import OwnerTruthPersonMemoryProfileService
from app.services.owner_truth_candidate_review import OwnerTruthCandidateReviewService
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.domain.owner_truth.candidate_decisions import OwnerTruthCandidateReviewCommand,CandidateReviewAction
r=Path(os.environ['S4_EVIDENCE_DIR']);raw=json.loads((r/'real-proposals.json').read_text());recorded=json.loads((root/'tests/fixtures/s4_device_run02.json').read_text())
url=urlparse(os.environ['LOCAL_LIVE_TEST_ADMIN_DSN']);assert url.hostname in {'127.0.0.1','localhost'}
base=url._replace(path='/').geturl();name='s4_full_'+uuid4().hex[:10];dsn=base+name
with psycopg.connect(base+'postgres',autocommit=True) as c:c.execute(psycopg.sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(psycopg.sql.Identifier(name)))
store=None;calls=[]
def compose(block):
 assert store._current_uow.get() is None
 key=sha256(json.dumps(block,sort_keys=True).encode()).hexdigest();path=r/('prose-'+key+'.json');assert not path.exists()
 expected=json.loads((r/'baseline-fingerprints.json').read_text())['app/async_effects/owner_truth_biography_worker.py']
 source=(root/'app/async_effects/owner_truth_biography_worker.py').read_text()
 script='block='+repr(block)+'\nexpected='+repr(expected)+'\nsource='+repr(source)+'\n'+'''import json,hashlib
import app.async_effects.owner_truth_biography_worker as m
from app.core.config import Settings
assert hashlib.sha256(open(m.__file__,'rb').read()).hexdigest()==expected
exec(compile(source,'s4-local-composer-proof','exec'),m.__dict__)
m.PROMPT_VERSION='evidence-polish-v7'
composer=m.DeepSeekBiographyComposer(Settings.from_env());result=composer(block)
print(json.dumps(dict(result=result,metadata=composer.last_metadata),ensure_ascii=False))
'''
 (r/('prose-'+key+'-input.json')).write_text(json.dumps(block,ensure_ascii=False,indent=2))
 p=subprocess.run(['ssh','-o','BatchMode=yes','dreamjourney-cloud','sudo','-n','docker','exec','-i','dreamjourneybackend-api-1','python','-'],input=script,text=True,capture_output=True,timeout=180);assert p.returncode==0,p.stderr
 value=json.loads(p.stdout);path.write_text(json.dumps(value,ensure_ascii=False,indent=2));compose.last_metadata=value['metadata'];calls.append(key);return value['result']
def reopen():
 s=PostgresStore(dsn=dsn,pool_min_size=1,pool_max_size=3);s.open_pool(wait=True);return s
try:
 PostgresMigrator(dsn=dsn,migrations_dir=default_migrations_dir(),build_id='s4-full-shapes').apply();store=reopen()
 def context():
  ctx=OwnerTruthCommandContext(vault_id='vault-'+uuid4().hex,owner_subject_id='s4-shape-owner',actor_subject_id='s4-shape-owner')
  with psycopg.connect(dsn) as c:c.execute('INSERT INTO owner_truth.vaults(vault_id,owner_subject_id) VALUES (%s,%s)',(ctx.vault_id,ctx.owner_subject_id))
  return ctx
 def fact(v):v=deepcopy(v);v.pop('provenance',None);return v
 def activate(ctx,item):
  source,candidate=str(uuid4()),str(uuid4());content=deepcopy(item['content']);kind=item.get('memoryKind',item.get('candidateKind'));h=sha256(json.dumps(content,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
  payload=dict(schemaVersion='owner-truth-candidate-v1',candidateKind=kind,content=content,contentSchemaVersion='owner-truth-v5',evidenceRefs=[dict(sourceId=source,sourceVersion=1)])
  with psycopg.connect(dsn) as c:
   c.execute("INSERT INTO owner_truth.sources(id,vault_id,owner_subject_id,source_kind,content_hash,policy_version,authority_epoch) VALUES (%s,%s,%s,'text',%s,%s,0)",(source,ctx.vault_id,ctx.owner_subject_id,h,ctx.policy_version))
   c.execute("INSERT INTO owner_truth.memory_candidates(id,vault_id,owner_subject_id,source_id,candidate_kind,perspective_type,epistemic_status,sensitivity,policy_version,authority_epoch,content_hash,payload_schema_version,payload) VALUES (%s,%s,%s,%s,%s,'firstPerson','recalled','standard',%s,0,%s,'owner-truth-v5',%s)",(candidate,ctx.vault_id,ctx.owner_subject_id,source,kind,ctx.policy_version,h,Jsonb(payload)))
  service=OwnerTruthCandidateReviewService(store);p=service.preview_changeset(candidate_id=candidate,context=ctx)
  cmd=OwnerTruthCandidateReviewCommand(command_id='s4-'+candidate,candidate_id=candidate,expected_candidate_version=1,expected_memory_revision=p.change_set.base_memory_revision,expected_change_set_id=p.change_set.change_set_id,expected_proposal_hash=p.proposal_hash,action=CandidateReviewAction.ACCEPT,corrected_value=None,corrected_value_schema_version='owner-truth-v5',reason_code='ownerReviewed')
  result=service.decide_and_activate(command=cmd,context=ctx);vid=result.memory_activation.memory_version_id
  assert service.decide_and_activate(command=cmd,context=ctx).memory_activation.memory_version_id==vid
  with psycopg.connect(dsn) as c:formal=c.execute('SELECT payload FROM owner_truth.memory_versions WHERE id=%s',(vid,)).fetchone()[0]['content']
  assert fact(formal)==fact(content);assert item['content']==content
  return p.change_set.operation.kind.value,vid
 def read(ctx):
  with store.request_unit_of_work(correlation_id='s4',command_id='s4'):return OwnerTruthPersonMemoryProfileService(store,biography_topics=True).read(context=ctx)
 def blocks(ctx):return [b for s in read(ctx).memory_model['biographyProjection']['sections'] for b in s['blocks']]
 ctx=context();original=[activate(ctx,recorded[n]) for n in ['origami-practice','origami-storage']];bs=blocks(ctx)
 assert len(bs)==1 and {e['memoryVersionId'] for e in bs[0]['evidence']}=={v for _,v in original}
 ctx=context();results=[activate(ctx,raw[n][0]) for n in ['archive-practice','archive-storage','archive-coffee','live-coffee']]
 assert [op for op,_ in results]==['add','add','add','temporalChange'],results
 bs=blocks(ctx);assert len(bs)==2 and sorted(len(b['evidence']) for b in bs)==[2,2],bs
 worker=BiographyWorker(store,compose);states=[worker.run_once(context=ctx) for _ in range(3)];assert [s['status'] for s in states]==['ready','ready','idle'],states
 bs=blocks(ctx);assert all(b['qualityState']=='polished' for b in bs),bs
 assert {e['memoryVersionId'] for b in bs for e in b['evidence']}=={v for _,v in results}
 snapshot=read(ctx).public_contract();store.close_pool();store=reopen();cold=read(ctx).public_contract()
 for key in ['profileVersion','sourceFingerprint','memoryCount','lifeStory','lifeRecord']:assert cold[key]==snapshot[key],key
 read_differences=[]
 for _ in range(10):
  current=read(ctx).public_contract();diff={k:dict(before=cold.get(k),after=current.get(k)) for k in set(cold)|set(current) if cold.get(k)!=current.get(k)}
  read_differences.append(diff)
  (r/'cold-read-differences.json').write_text(json.dumps(read_differences,ensure_ascii=False,indent=2))
  assert set(diff)<={'rebuildMode','affectedDimensions'},diff
  assert current['rebuildMode'] in {'full','incremental','unchanged'}

 assert len(calls)==2
 report=dict(status='PASS',exactDeviceTopicEvidence=True,operations=[op for op,_ in results],formalContentExact=True,coldReadExact=True,readDiagnostics=read_differences,states=states,blocks=bs,compositionCalls=len(calls),scope='local PG real captured extraction and fresh real prose; device NOT_RUN')
 (r/'pg-real-shapes-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False,indent=2))
finally:
 if store:store.close_pool()
 with psycopg.connect(base+'postgres',autocommit=True) as c:c.execute(psycopg.sql.SQL('DROP DATABASE {}').format(psycopg.sql.Identifier(name)))
