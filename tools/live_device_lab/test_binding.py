import copy, hashlib, importlib.util, json, tempfile, unittest, uuid
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from binding_contract import BindingError, validate_request, verify_rows
from recovery_binding import approve_scene_source


def fixture(n=2):
    owner, vault, sid, source = [str(uuid.uuid4()) for _ in range(4)]
    product='echo_live_'+uuid.uuid4().hex
    session=dict(id=sid,owner_subject_id=owner,vault_id=vault,metadata={'productSessionId':product})
    q=dict(schema=2,requestID=str(uuid.uuid4()),sessionID=sid,runID='lab-'+'a'*32,launchID=str(uuid.uuid4()),
           deviceID='local-simulator',buildIdentity='a'*64,accountHash=hashlib.sha256((owner+'|'+vault).encode()).hexdigest(),
           productSessionID=product,snapshotRevision=1,messages=[])
    messages=[];turns=[];members={}
    for i in range(n):
        mid=str(uuid.uuid4());text=f'合成事实{i}';role='owner' if i%2==0 else 'assistant';digest=hashlib.sha256(text.encode()).hexdigest()
        q['messages'].append(dict(canonicalID=f'canonical-{i}',messageID=mid,role=role,sha256=digest))
        messages.append(dict(id=mid,author=role,content_payload={'text':text},client_sequence_number=i+1))
        turns.append(dict(messageId=mid,index=i+1,role='user' if role=='owner' else role,text=text,contentHash=digest))
        members[str(i+1)]=dict(messageId=mid,role=role,contentHash=digest)
    snap=dict(source_id=source,state='published',revision=1,snapshot_hash='b'*64,
        snapshot=dict(endPositionKnown=True,missingRanges=[],finalSequence=n,revision=1,hash='b'*64,messages=members))
    source=dict(sessionId=sid,productSessionId=product,conversationTurns=turns,snapshotRevision=1,snapshotHash='b'*64)
    refresh_hash(snap, source)
    return q,session,snap,source,messages


def refresh_hash(snap, source):
    raw={k:v for k,v in snap['snapshot'].items() if k not in ('hash','snapshotId')}
    value=hashlib.sha256(json.dumps(raw,sort_keys=True,separators=(',', ':'),ensure_ascii=False).encode()).hexdigest()
    snap['snapshot']['hash']=snap['snapshot_hash']=source['snapshotHash']=value

class BindingTests(unittest.TestCase):
    def test_zero_completed_two_saved_and_odd_count(self):
        for n,completed in [(2,0),(1,0),(4,1)]:
            args=fixture(n);r=verify_rows(*args)
            self.assertEqual(r['messageCount'],n)
            self.assertEqual(len(r['messageBindings']),n)
            self.assertNotEqual(n,completed*2)
    def test_exact_identity_not_only_count(self):
        cases=[('accountHash','c'*64,'bindingAccount'),('sessionID',str(uuid.uuid4()),'bindingSession'),
               ('productSessionID','echo_live_wrong','bindingSession'),('snapshotRevision',2,'bindingRevision')]
        for key,value,code in cases:
            args=fixture();args[0][key]=value
            with self.subTest(key=key),self.assertRaisesRegex(BindingError,code):verify_rows(*args)
        for key,value,code in [('role','assistant','bindingRole'),('sha256','0'*64,'bindingHash'),
                               ('messageID',str(uuid.uuid4()),'bindingMessageIdentity')]:
            args=fixture();args[0]['messages'][0][key]=value
            with self.subTest(key=key),self.assertRaisesRegex(BindingError,code):verify_rows(*args)
    def test_duplicate_source_and_local_id(self):
        args=fixture();args[0]['messages'][1]=copy.deepcopy(args[0]['messages'][0])
        with self.assertRaisesRegex(BindingError,'bindingDuplicateMessage'):verify_rows(*args)
        args=fixture();args[3]['conversationTurns'][1]=copy.deepcopy(args[3]['conversationTurns'][0])
        with self.assertRaisesRegex(BindingError,'bindingDuplicateSource'):verify_rows(*args)
    def test_snapshot_membership_and_hash(self):
        for change in ('sourceHash','member','snapshotHash'):
            args=fixture()
            if change=='sourceHash':args[3]['snapshotHash']='c'*64
            elif change=='member':args[2]['snapshot']['messages']['1']['messageId']=str(uuid.uuid4())
            else:args[2]['snapshot']['hash']='c'*64
            with self.subTest(change=change),self.assertRaisesRegex(BindingError,'bindingSnapshotHash'):verify_rows(*args)
    def test_partial_refused_not_promoted(self):
        args=fixture();args[2]['snapshot']['missingRanges']=[[2,3]]
        with self.assertRaisesRegex(BindingError,'bindingPartialUnsupported'):verify_rows(*args)
    def test_request_types_fail_closed(self):
        for key,value in [('messages',[None]),('snapshotRevision',True),('runID',1),('buildIdentity',[]),('messages',[])]:
            q=fixture()[0];q[key]=value
            with self.subTest(key=key,value=value),self.assertRaises(BindingError):validate_request(q)
    def test_recopy_after_transfer_failure_and_new_revision(self):
        args=fixture();q=args[0];value=dict(q,capture=dict(recoverySessionID=q['sessionID'],productSessionID=q['productSessionID']),sourceBindingRequest=q,completedTurns=0)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);reads=[];copies=[]
            def reader(q):reads.append(q);return verify_rows(*args)
            def copier(p):copies.append(json.loads(p.read_text()));
            with self.assertRaises(OSError):
                approve_scene_source(q['deviceID'],'unused',root,value,reader=reader,copier=lambda p:(_ for _ in ()).throw(OSError()),expected_build='a'*64)
            r=approve_scene_source(q['deviceID'],'unused',root,value,reader=reader,copier=copier,expected_build='a'*64)
            self.assertEqual(len(reads),2);self.assertEqual(len(copies),1);self.assertEqual(r['messageCount'],2)
            q['requestID']=str(uuid.uuid4());args[2]['revision']=2;args[2]['snapshot']['revision']=2;args[3]['snapshotRevision']=2;q['snapshotRevision']=2;refresh_hash(args[2],args[3])
            approve_scene_source(q['deviceID'],'unused',root,value,reader=reader,copier=copier,expected_build='a'*64)
            self.assertEqual(len(reads),3)
    def test_host_runtime_build_and_safe_error(self):
        args=fixture();q=args[0];value=dict(q,capture=dict(recoverySessionID=q['sessionID'],productSessionID=q['productSessionID']),sourceBindingRequest=q)
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(BindingError,'bindingBuildIdentity'):
                approve_scene_source(q['deviceID'],'x',Path(temp),value,expected_build='c'*64)
            r=approve_scene_source(q['deviceID'],'x',Path(temp),value,expected_build='a'*64,
                 reader=lambda q:dict(q,status='ERROR',code='bindingHash',readOnly=True),copier=lambda p:None)
            self.assertEqual(r['code'],'bindingHash')
    def test_old_host_same_durable_assertion_is_red(self):
        old=Path(__file__).resolve().parents[2]/'outputs/2026-09-30-live-lab-recovery-repair/run-01/baseline/live_device_lab/recovery_binding.py'
        spec=importlib.util.spec_from_file_location('old_binding',old);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
        q=fixture()[0];value=dict(q,capture=dict(recoverySessionID=q['sessionID'],productSessionID=q['productSessionID']),completedTurns=0)
        # The remote fixture applies the old server assertion to the SAME actual count (2).
        def remote(*args,**kwargs):
            sent=eval(kwargs['input'].splitlines()[0][2:])
            self.assertEqual(sent['messageCount'],2,'actual durable Source count must be used even when playback completed=0')
        with tempfile.TemporaryDirectory() as temp,patch.object(mod.subprocess,'run',remote):
            with self.assertRaisesRegex(AssertionError,'actual durable Source count'):
                mod.approve_scene_source(q['deviceID'],'unused',Path(temp),value)

if __name__=='__main__':unittest.main(verbosity=2)
