"""Resolve only the current typed scene using read-only server session identity."""
import json, subprocess, uuid
from pathlib import Path
from binding_contract import require

def approve_typed_source(device, remote, root, value):
    from lab import call,save,BUNDLE
    capture=value['capture']
    q={k:value[k] for k in ('runID','launchID','accountHash')}
    q.update(sessionID=capture['recoverySessionID'],productSessionID=capture['productSessionID'])
    uuid.UUID(q['sessionID']);require(q['productSessionID'].startswith('echo_live_'),'bindingRuntimeIdentity')
    script='q='+repr(q)+'\n'+REMOTE
    r=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10','dreamjourney-cloud','sudo docker exec -i dreamjourneybackend-api-1 python -'],input=script,text=True,capture_output=True,check=True,timeout=25)
    result=json.loads(r.stdout)
    require(all(result.get(k)==v for k,v in q.items()),'bindingRuntimeIdentity')
    require(result['status']=='VERIFIED','bindingNotPublished')
    path=root/'typed-source.json';save(path,result)
    call(['xcrun','devicectl','device','copy','to','--device',device,'--domain-type','appDataContainer','--domain-identifier',BUNDLE,'--source',path,'--destination',remote+'/typed-source.json'],timeout=25)

REMOTE=r'''
import os,json,hashlib,psycopg
from psycopg.rows import dict_row
with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row) as c:
 c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
 c.execute("SET LOCAL statement_timeout='5s'")
 s=c.execute('SELECT id,vault_id,owner_subject_id,metadata FROM owner_truth.interview_sessions WHERE id=%s',(q['sessionID'],)).fetchone()
 assert s and s['metadata']['productSessionId']==q['productSessionID'],'session identity'
 assert hashlib.sha256((str(s['owner_subject_id'])+'|'+str(s['vault_id'])).encode()).hexdigest()==q['accountHash'],'account identity'
 snap=c.execute("SELECT source_id,state,revision,snapshot_hash FROM owner_truth.live_recovery_snapshots WHERE session_id=%s ORDER BY revision DESC LIMIT 1",(q['sessionID'],)).fetchone()
 assert snap and snap['state'] in ('published','noChange'),'not a successful publication terminal'
 src=c.execute('SELECT metadata FROM owner_truth.sources WHERE id=%s AND vault_id=%s',(snap['source_id'],s['vault_id'])).fetchone()
 assert src and src['metadata']['sessionId']==q['sessionID'] and src['metadata']['productSessionId']==q['productSessionID'],'source identity'
 print(json.dumps(dict(q,status='VERIFIED',sourceID=str(snap['source_id']),snapshotRevision=snap['revision'],publicationState=snap['state'],readOnly=True)))
'''
