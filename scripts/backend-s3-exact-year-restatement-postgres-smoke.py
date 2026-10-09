"""Exact regression through production preview/activation and isolated PG."""
from pathlib import Path
import sys, importlib.util, os, json
from dataclasses import replace
from uuid import uuid4
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
import psycopg
from psycopg.types.json import Jsonb
from app.db.migrator import PostgresMigrator,default_migrations_dir
from app.services.postgres_store import PostgresStore
from app.services.owner_truth_candidate_review import OwnerTruthCandidateReviewService
from app.domain.owner_truth.candidate_decisions import CandidateReviewAction,OwnerTruthCandidateReviewCommand
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from tests.test_s3_relation_review import S3RelationReviewTests
from tests.test_s3_exact_year_restatement import ExactYearRestatementTests
from app.domain.owner_truth.ontology import OWNER_TRUTH_SCHEMA_VERSION_V5
from urllib.parse import urlparse
parsed=urlparse(os.environ['LOCAL_LIVE_TEST_ADMIN_DSN'])
assert parsed.hostname in {'127.0.0.1','localhost'}, 'isolated local PostgreSQL only'
base=parsed._replace(path='/').geturl()
name='s3_period_'+uuid4().hex[:10];dsn=base+name
with psycopg.connect(base+'postgres',autocommit=True) as c:
 c.execute(psycopg.sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(psycopg.sql.Identifier(name)))
store=None
try:
 PostgresMigrator(dsn=dsn,migrations_dir=default_migrations_dir(),build_id='s3-multiple-periods').apply()
 h=S3RelationReviewTests();h.setUp();context=h.context
 fixtures=ExactYearRestatementTests();fixtures.setUp()
 with psycopg.connect(dsn) as c:
  c.execute('INSERT INTO owner_truth.vaults(vault_id,owner_subject_id) VALUES (%s,%s)',(context.vault_id,context.owner_subject_id))
 store=PostgresStore(dsn=dsn,pool_min_size=1,pool_max_size=5);store.open_pool(wait=True)
 service=OwnerTruthCandidateReviewService(store)
 results=[]
 for idx,fixture_name in enumerate(('period-2016','period-2025','period-repeat')):
  candidate=replace(fixtures.candidate(fixture_name),vault_id=context.vault_id,owner_subject_id=context.owner_subject_id)
  year=2016 if idx==0 else 2025
  with psycopg.connect(dsn) as c:
   c.execute("INSERT INTO owner_truth.sources(id,vault_id,owner_subject_id,source_kind,content_hash,policy_version,authority_epoch) VALUES (%s,%s,%s,'text',%s,%s,0)",(candidate.source_id,context.vault_id,context.owner_subject_id,candidate.content_hash,context.policy_version))
   c.execute("""INSERT INTO owner_truth.memory_candidates(id,vault_id,owner_subject_id,source_id,candidate_kind,perspective_type,epistemic_status,sensitivity,policy_version,authority_epoch,content_hash,payload_schema_version,payload)
   VALUES (%s,%s,%s,%s,%s,'firstPerson','recalled','standard',%s,0,%s,%s,%s)""",(candidate.candidate_id,context.vault_id,context.owner_subject_id,candidate.source_id,candidate.memory_kind.value,context.policy_version,candidate.content_hash,OWNER_TRUTH_SCHEMA_VERSION_V5,Jsonb({**candidate.payload,'candidateKind':candidate.memory_kind.value})))
  proposal=service.preview_changeset(candidate_id=candidate.candidate_id,context=context)
  command=OwnerTruthCandidateReviewCommand(command_id=f's3-pg-confirm-{idx}',candidate_id=candidate.candidate_id,expected_candidate_version=1,expected_memory_revision=proposal.change_set.base_memory_revision,expected_change_set_id=proposal.change_set.change_set_id,expected_proposal_hash=proposal.proposal_hash,action=CandidateReviewAction.ACCEPT,corrected_value=None,corrected_value_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V5,reason_code='ownerReviewed')
  before_content=None
  if idx==2:
   with psycopg.connect(dsn) as c:
    before_content=c.execute('SELECT payload FROM owner_truth.memory_versions WHERE memory_id=%s AND is_current',(results[1]['memoryId'],)).fetchone()[0]
  result=service.decide_and_activate(command=command,context=context)
  # Lost response: fetch saved result, and same command replay must not create an extra version.
  replay=service.decide_and_activate(command=command,context=context)
  assert result.memory_activation.memory_version_id==replay.memory_activation.memory_version_id
  results.append(dict(year=year,operation=proposal.change_set.operation.kind.value,memoryId=result.memory_activation.memory_id,versionId=result.memory_activation.memory_version_id,outcome=result.memory_activation.outcome,replaySameVersion=True))
 with psycopg.connect(dsn) as c:
  row=c.execute('SELECT m.memory_kind,v.payload FROM owner_truth.memories m JOIN owner_truth.memory_versions v ON v.memory_id=m.id AND v.is_current WHERE m.id=%s',(results[1]['memoryId'],)).fetchone()
  assert row[0]=='experience'
  after_content=row[1]
  # Reviewed content stays exact; only provenance/evidence and version envelope can change.
  def fact(payload):
   content=dict(payload['content']);content.pop('provenance',None);return content
  assert fact(before_content)==fact(after_content)
 assert [r['operation'] for r in results]==['add','temporalChange','addEvidence'],results
 assert results[0]['memoryId']!=results[1]['memoryId']==results[2]['memoryId'],results
 old=service.list_memory_version_history(memory_id=results[0]['memoryId'],context=context)
 recent=service.list_memory_version_history(memory_id=results[1]['memoryId'],context=context)
 assert [v.status for v in old.versions]==['current']
 assert [v.status for v in recent.versions]==['current','superseded']
 # New store/connection (no in-memory result reuse), compare immutable identities.
 store.close_pool();store=PostgresStore(dsn=dsn,pool_min_size=1,pool_max_size=5);store.open_pool(wait=True)
 cold=OwnerTruthCandidateReviewService(store).list_memory_version_history(memory_id=results[1]['memoryId'],context=context)
 assert cold == recent
 print(json.dumps(dict(status='PASS',scope='production preview and confirmation service with isolated PG; not three-entry UI acceptance',results=results,oldPeriodUnchanged=True,newConnectionReadExact=True),ensure_ascii=False,indent=2))
finally:
 if store:store.close_pool()
 with psycopg.connect(base+'postgres',autocommit=True) as c:
  c.execute(psycopg.sql.SQL('DROP DATABASE {}').format(psycopg.sql.Identifier(name)))
