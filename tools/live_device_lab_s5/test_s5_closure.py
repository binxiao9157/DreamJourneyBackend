from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import subprocess
import tempfile
import unittest
import lab
import test_lab


class ClosureTests(unittest.TestCase):
    def test_actual_swift_meaning_changes_and_terminal_classification(self):
        source = (Path(__file__).parent/'ios/LabEvidence.swift').read_text()
        checks = r'''
enum LiveDeviceLabError: Error { case check(String) }
func review(_ expected:String?, _ actual:String, _ terms:[String]) throws -> Bool {
    var ledger=LiveLabEvidenceLedger()
    ledger.observe(.init(canonicalID:"turn",text:actual,ordinal:1,requiredTerms:terms,expectedTranscript:expected))
    let rows=[LiveLabMessage(canonicalID:"turn",messageID:"message",role:"owner",text:actual)]
    let differences=try ledger.contentDifferences(rows)
    if !differences.isEmpty {
        do { _ = try ledger.requiredFacts(rows); fatalError("unreviewed write authorized") }
        catch LiveDeviceLabError.check(let reason) { assert(reason=="asrFactMismatch") }
    } else { _ = try ledger.requiredFacts(rows) }
    // Semantic review cannot stop live input or suppress the next reply.
    assert(LiveLabTurnCompletion.ready(finalText:actual,displayedUser:actual,displayedAssistant:"好的",playbackClosed:true,listeningOrFarewell:true))
    return !differences.isEmpty
}
for (expected,actual,terms) in [
    ("我以后再尝试做夹心饼干。","我正在尝试做夹心饼干。",["尝试","夹心饼干"]),
    ("我不喜欢喝咖啡。","我喜欢喝咖啡。",["咖啡"]),
    ("姐姐在学习陶笛。","我在学习陶笛。",["学习","陶笛"]),
    ("我偶尔喝咖啡。","我每天喝咖啡。",["喝咖啡"])
] { let needed=try review(expected,actual,terms);assert(needed) }
let missing=try review(nil,"蓝色盒子",["蓝色"]);assert(missing)
let exact=try review("我养着3条小鱼。","我养着 3 条小鱼！",["小鱼"]);assert(!exact)
let clean=try review("我用红色水桶换水。","我用红色水桶换水。",["换水"]);assert(!clean)
assert(LiveLabLinkedThemePolicy.allows(linked:nil,allowed:nil,expectedIDs:[],actualIDs:["c"]))
assert(!LiveLabLinkedThemePolicy.allows(linked:"old",allowed:nil,expectedIDs:["c"],actualIDs:["c"]))
assert(!LiveLabLinkedThemePolicy.allows(linked:"old",allowed:"other",expectedIDs:["c"],actualIDs:["c"]))
assert(!LiveLabLinkedThemePolicy.allows(linked:"old",allowed:"old",expectedIDs:["c"],actualIDs:["c","old-member"]))
assert(LiveLabLinkedThemePolicy.allows(linked:"old",allowed:"old",expectedIDs:["c"],actualIDs:["c"]))
assert(!LiveLabTypedPublicationPolicy.ready(state:nil,hasBatch:false))
assert(!LiveLabTypedPublicationPolicy.ready(state:"pending",hasBatch:false))
for state in ["noChange","published","failed"] {
    assert(LiveLabTypedPublicationPolicy.ready(state:state,hasBatch:false))
}
print("S5 semantic review and terminal policy PASS")
'''
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'main.swift';p.write_text(source+'\n'+checks)
            result=subprocess.run(['swift','-module-cache-path',str(Path(tmp)/'cache'),'-DDEBUG','-DLIVE_DEVICE_AUTOMATION',str(p)],text=True,capture_output=True,timeout=90)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_no_change_host_returns_without_confirmation_or_short_gate(self):
        fixture=test_lab.SafetyTests();fixture.setUp()
        try:
            stage,_=fixture.build_fixture()
            fixture.m['organizationTimeoutSeconds']=180
            lab.save(fixture.root/'manifest.json',fixture.m)
            args=SimpleNamespace(run=str(fixture.root),stage=str(stage),device='phone',current_test_account=True,short_receipt=None,allow_audible=True)
            observation={'status':'NO_CHANGE_OBSERVED','memoryConfirmationStatus':'NOT_APPLICABLE','noChangeFormalAudit':'NOT_RUN'}
            with patch.object(lab,'call') as call,patch.object(lab,'wait_result',return_value=observation) as wait:
                lab.run(args)
            self.assertEqual(wait.call_count,1)
            self.assertFalse((fixture.root/'short-receipt.json').exists())
            self.assertFalse((fixture.root/'cold-readback.json').exists())
            self.assertTrue((fixture.root/'no-change-observation.json').exists())
            launches=[c for c in call.call_args_list if 'launch' in c.args[0]]
            self.assertEqual(len(launches),1)
        finally: fixture.doCleanups()

    def test_no_change_wait_is_terminal_not_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'result.json'
            result={'status':'NO_CHANGE_OBSERVED','mode':'run','launchID':'current'}
            with patch.object(lab,'copy_from',side_effect=lambda *args:lab.save(path,result)) as read:
                self.assertEqual(lab.wait_result('phone','remote',path,30,'run','current'),result)
                self.assertEqual(read.call_count,1)

    def test_modified_speech_expectation_is_bound_by_stage_and_observation(self):
        # Actual runtime consumes the frozen complete transcript, not a keyword-only path.
        root=Path(__file__).parent
        runtime=(root/'ios/LiveDeviceLabRuntime.swift').read_text()
        three=(root/'ios/ThreeEntryRuntime.swift').read_text()
        self.assertTrue(runtime.endswith(three))
        self.assertIn('expectedTranscript: turn?.expectedTranscript ?? turn?.text',runtime)
        self.assertIn('if threeConfig != nil, contentDifferences.isEmpty',runtime)
        self.assertIn('unexpectedOrIncompleteNoChange',three)
        self.assertIn('typedPublicationFailed',three)


if __name__=='__main__': unittest.main()

class AuditTests(unittest.TestCase):
    def test_no_change_requires_exact_sets_and_identity(self):
        from no_change_audit import validate
        from copy import deepcopy
        before=dict(accountHash='a',formal=[dict(id='f',version='v1',hash='h')],candidates=[dict(id='p',hash='c')])
        after=dict(before,sessionID='s',productSessionID='ps',sourceID='src',state='noChange',frozen=True,finalSequence=4,continuousSequence=4)
        obs=dict(status='NO_CHANGE_OBSERVED',accountHash='a',sourceID='src',capture=dict(recoverySessionID='s',productSessionID='ps'))
        self.assertEqual(validate(before,after,obs)['status'],'PASS')
        for key,value in [('formal',[]),('candidates',[]),('accountHash','b'),('sourceID','bad'),('state','pending'),('frozen',False),('continuousSequence',3)]:
            bad=deepcopy(after);bad[key]=value
            with self.subTest(key=key),self.assertRaises(AssertionError):validate(before,bad,obs)
        changed=deepcopy(after);changed['formal'][0]['version']='v2'
        with self.assertRaises(AssertionError):validate(before,changed,obs)

    def test_actual_server_binding_accepts_only_successful_terminals(self):
        import typed_binding,sys,types,contextlib,io,hashlib
        from unittest.mock import patch
        class Cursor:
            def __init__(self,row):self.row=row
            def fetchone(self):return self.row
        class Conn:
            def __init__(self,state,owner='owner'):self.state=state;self.owner=owner
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def execute(self,sql,args=None):
                if 'interview_sessions' in sql:return Cursor(dict(id='s',vault_id='v',owner_subject_id=self.owner,metadata=dict(productSessionId='ps')))
                if 'live_recovery_snapshots' in sql:return Cursor(dict(source_id='src',state=self.state,revision=1,snapshot_hash='h'))
                if 'sources' in sql:return Cursor(dict(metadata=dict(sessionId='s',productSessionId='ps')))
                return Cursor(None)
        q=dict(sessionID='s',productSessionID='ps',accountHash=hashlib.sha256(b'owner|v').hexdigest())
        for state in ['published','noChange','pending','failed']:
            modules={'psycopg':types.SimpleNamespace(connect=lambda *a,**kw:Conn(state)),'psycopg.rows':types.SimpleNamespace(dict_row=None)}
            with self.subTest(state=state),patch.dict(sys.modules,modules),patch.dict('os.environ',{'DATABASE_URL':'test'}),contextlib.redirect_stdout(io.StringIO()):
                if state in ['published','noChange']:exec(typed_binding.REMOTE,dict(q=q))
                else:
                    with self.assertRaises(AssertionError):exec(typed_binding.REMOTE,dict(q=q))
