#!/usr/bin/env python3
"""Replay captured synthetic repeated facts through real isolated PG review.

Only loopback DATABASE_URL is accepted. Creates/drops a unique database;
never connects to providers or changes an existing application database.
"""
import importlib.util
import json
from types import SimpleNamespace
import os
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb
from app.domain.owner_truth.candidate_decisions import CandidateReviewAction, OwnerTruthCandidateReviewCommand
from app.domain.owner_truth.memory_changeset_group import OwnerTruthMemoryChangeSetGroupCommand, OwnerTruthMemoryChangeSetGroupSelection, OwnerTruthMemoryChangeSetGroupDependency
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_candidate_review import OwnerTruthCandidateReviewService
from app.services.owner_truth_formal_memory import OwnerTruthFormalMemoryService
from app.services.owner_truth_memory_changeset_group_review import OwnerTruthMemoryChangeSetGroupReviewService
from app.services.postgres_store import PostgresStore
from app.db.migrator import PostgresMigrator, default_migrations_dir

spec=importlib.util.spec_from_file_location('group_smoke',ROOT/'scripts/backend-owner-truth-memory-changeset-group-postgres-smoke.py')
h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
ROWS=json.loads((ROOT/'tests/fixtures/same_source_repetition.json').read_text())

def seed(dsn, pair):
    token=uuid4().hex
    context=OwnerTruthCommandContext(vault_id='repetition-'+token,owner_subject_id='owner-'+token,actor_subject_id='owner-'+token)
    source=str(uuid4()); ids=[];payloads=[]
    with psycopg.connect(dsn) as conn:
        conn.execute('INSERT INTO owner_truth.vaults(vault_id,owner_subject_id) VALUES(%s,%s)',(context.vault_id,context.owner_subject_id))
        conn.execute("INSERT INTO owner_truth.sources(id,vault_id,owner_subject_id,source_kind,content_hash,policy_version,authority_epoch) VALUES(%s,%s,%s,'text',%s,%s,0)",(source,context.vault_id,context.owner_subject_id,h.canonical_hash({'fixture':'synthetic-repetition'}),context.policy_version))
        for index in pair:
            payload=deepcopy(ROWS[index]['payload']);old=payload['evidenceRefs'][0]['sourceId']
            payload=json.loads(json.dumps(payload).replace(old,source));cid=str(uuid4());ids.append(cid);payloads.append(payload)
            conn.execute("""INSERT INTO owner_truth.memory_candidates(id,vault_id,owner_subject_id,source_id,candidate_kind,perspective_type,epistemic_status,sensitivity,policy_version,authority_epoch,content_hash,payload_schema_version,payload)
                VALUES(%s,%s,%s,%s,'experience','firstPerson','recalled','standard',%s,0,%s,'owner-truth-v5',%s)""",
                (cid,context.vault_id,context.owner_subject_id,source,context.policy_version,h.canonical_hash(payload['content']),Jsonb(payload)))
    return context,ids,payloads

def review(store,context,ids,prefix,expected):
    if len(ids)==1:
        service=OwnerTruthCandidateReviewService(store)
        proposal=service.preview_changeset(candidate_id=ids[0],context=context)
        assert [proposal.change_set.operation.kind.value]==expected
        command=OwnerTruthCandidateReviewCommand(command_id=prefix+'-confirm',candidate_id=ids[0],expected_candidate_version=1,
            expected_memory_revision=proposal.change_set.base_memory_revision,expected_change_set_id=proposal.change_set.change_set_id,
            expected_proposal_hash=proposal.proposal_hash,action=CandidateReviewAction.ACCEPT,corrected_value=None,
            corrected_value_schema_version='owner-truth-v5',reason_code='ownerReviewed')
        result=service.decide_and_activate(command=command,context=context)
        before=h.vault_counts(store._test_dsn,context=context)
        replay=service.decide_and_activate(command=command,context=context)
        assert replay.memory_activation.memory_id==result.memory_activation.memory_id
        assert h.vault_counts(store._test_dsn,context=context)==before
        return SimpleNamespace(members=[result.memory_activation])
    selections=tuple(OwnerTruthMemoryChangeSetGroupSelection(candidate_id=cid,expected_candidate_version=1,action=CandidateReviewAction.ACCEPT,corrected_value=None,corrected_value_schema_version=None,reason_code='ownerReviewed') for cid in ids)
    dependencies=(OwnerTruthMemoryChangeSetGroupDependency(before_candidate_id=ids[0],after_candidate_id=ids[1]),) if len(ids)>1 else ()
    cmd=OwnerTruthMemoryChangeSetGroupCommand(command_id=prefix+'-preview',selections=selections,dependencies=dependencies)
    service=OwnerTruthMemoryChangeSetGroupReviewService(store);proposal=service.preview(command=cmd,context=context)
    kinds=[m.proposal.change_set.operation.kind.value for m in proposal.members]
    assert kinds==expected,(kinds,expected)
    cmd=replace(cmd,command_id=prefix+'-confirm',expected_memory_revision=proposal.base_memory_revision,expected_group_proposal_id=proposal.proposal_id,expected_group_proposal_hash=proposal.proposal_hash)
    result=service.confirm(command=cmd,context=context)
    before=h.vault_counts(store._test_dsn,context=context)
    assert service.confirm(command=cmd,context=context).outcome=='deduplicated'
    assert h.vault_counts(store._test_dsn,context=context)==before
    return result

def main():
    admin=os.environ['DATABASE_URL'];assert conninfo_to_dict(admin).get('host') in {'127.0.0.1','localhost'}
    db='dj_repetition_'+uuid4().hex[:12];dsn=h.dsn_for_database(admin,db);h.create_database(admin,db)
    results=[]
    try:
        PostgresMigrator(dsn=dsn,migrations_dir=default_migrations_dir(),build_id='same-source-repetition').apply()
        for pair in ((0,1),(2,3),(4,5)):
            for mode in ('virtual-group','already-formal'):
                context,ids,payloads=seed(dsn,pair)
                store=PostgresStore(dsn=dsn,pool_min_size=1,pool_max_size=2);store.open_pool(wait=True);store._test_dsn=dsn
                try:
                    if mode=='virtual-group':result=review(store,context,ids,'group',['add','addEvidence'])
                    else:
                        review(store,context,ids[:1],'first',['add'])
                        result=review(store,context,ids[1:],'second',['addEvidence'])
                    memory_id=result.members[-1].memory_id
                finally:store.close_pool()
                # A new Store / connection reads persisted formal data.
                fresh=PostgresStore(dsn=dsn,pool_min_size=1,pool_max_size=2);fresh.open_pool(wait=True)
                try:
                    detail=OwnerTruthFormalMemoryService(fresh).detail(context=context,memory_id=memory_id)
                    actual=deepcopy(detail.current_version.content);expected=deepcopy(payloads[0]['content'])
                    actual.pop('provenance');expected.pop('provenance');assert actual==expected
                    refs=detail.current_version.evidence_refs
                    assert sorted(json.dumps(r,sort_keys=True) for r in refs)==sorted(json.dumps(r,sort_keys=True) for p in payloads for r in p['evidenceRefs'])
                finally:fresh.close_pool()
                counts=h.vault_counts(dsn,context=context)
                assert counts['currentMemoryVersions']==1,counts
                assert counts['memoryVersions']==2 and counts['candidateStates']=={'accepted':2},counts
                results.append(dict(pair=list(pair),mode=mode,oneActiveMemory=True,twoEvidenceSpans=True,contentPreserved=True,coldRead=True,replayIdempotent=True))
        print(json.dumps(dict(status='LOCAL_PG_PASS',results=results),indent=2))
    finally:h.drop_database(admin,db)

if __name__=='__main__':main()
