"""Read-only remote identity gate for the newly created device scene only."""
import hashlib,json,re,subprocess,uuid
from pathlib import Path


def approve_scene_source(device, remote, root, value):
    from lab import call,save,BUNDLE
    run=value['runID'];capture=value['capture'];session=str(uuid.UUID(capture['recoverySessionID']));product=capture['productSessionID']
    if not re.fullmatch(r'lab-[0-9a-f]{32}',run) or not re.fullmatch(r'echo_live_[0-9a-f]+',product):raise ValueError('invalid scene identity')
    path=root/'scene-binding.json'
    if path.exists():return
    query={'runID':run,'sessionID':session,'productSessionID':product,'accountHash':value['accountHash'],'messageCount':value['completedTurns']*2}
    script='q='+repr(query)+'\n'+REMOTE_READ
    result=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10','dreamjourney-cloud','sudo docker exec -i dreamjourneybackend-api-1 python -'],input=script,text=True,capture_output=True,timeout=25,check=True)
    binding=json.loads(result.stdout);assert binding['runID']==run and binding['messageCount']==query['messageCount']
    save(path,binding)
    call(['xcrun','devicectl','device','copy','to','--device',device,'--domain-type','appDataContainer','--domain-identifier',BUNDLE,'--source',path,'--destination',remote+'/scene-binding.json'],timeout=25)

REMOTE_READ=r'''
import json,os,hashlib,psycopg
from psycopg.rows import dict_row
with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row) as c:
 c.execute('SET TRANSACTION READ ONLY');c.execute("SET LOCAL statement_timeout='5s'")
 row=c.execute("SELECT s.id,s.vault_id,s.owner_subject_id,s.metadata FROM owner_truth.interview_sessions s WHERE s.id=%s AND s.metadata->>'productSessionId'=%s",(q['sessionID'],q['productSessionID'])).fetchone()
 assert row and hashlib.sha256((str(row['owner_subject_id'])+'|'+str(row['vault_id'])).encode()).hexdigest()==q['accountHash']
 snap=c.execute("SELECT source_id,state,snapshot FROM owner_truth.live_recovery_snapshots WHERE session_id=%s ORDER BY revision DESC LIMIT 1",(q['sessionID'],)).fetchone()
 assert snap and snap['state']=='published';s=snap['snapshot']
 assert s['endPositionKnown'] and not s['missingRanges'] and s['finalSequence']==q['messageCount']
 source=c.execute('SELECT metadata FROM owner_truth.sources WHERE id=%s AND vault_id=%s',(snap['source_id'],row['vault_id'])).fetchone()['metadata']
 assert source['sessionId']==q['sessionID'] and source['productSessionId']==q['productSessionID']
 messages=c.execute('SELECT id,client_sequence_number,author,content_payload FROM owner_truth.conversation_messages WHERE session_id=%s ORDER BY client_sequence_number',(q['sessionID'],)).fetchall()
 assert len(messages)==q['messageCount'] and len(source['conversationTurns'])==q['messageCount']
 turns={t['messageId']:t for t in source['conversationTurns']};evidence=[]
 for m in messages:
  t=turns[str(m['id'])];h=hashlib.sha256(m['content_payload']['text'].encode()).hexdigest()
  assert t['contentHash']==h and t['index']==m['client_sequence_number'] and t['role']==('user' if m['author']=='owner' else 'assistant')
  evidence.append(dict(messageID=str(m['id']),sequence=m['client_sequence_number'],role=m['author'],sha256=h))
 print(json.dumps(dict(q,sourceID=str(snap['source_id']),messageBindings=evidence,readOnly=True)))
'''
