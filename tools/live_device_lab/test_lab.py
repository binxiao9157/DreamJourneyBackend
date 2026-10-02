import hashlib
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import lab
from stage import replace_once


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        pcm = b'\x10\x02' * 1600
        self.m = dict(schema=1, runID='lab-' + 'a'*32, profile='short',
                      minimumDurationSeconds=0, maximumDurationSeconds=300,
                      sampleRate=16000, channels=1, sampleBytes=2,
                      requiredMemoryTerms=['蓝色'], maxCandidateWrites=30, turns=[])
        for i in (1, 2):
            p = self.root/f'turn-{i:03d}.pcm'; p.write_bytes(pcm)
            self.m['turns'].append(dict(ordinal=i,file=p.name,sha256=lab.digest(p),requiredASRTerms=['蓝色']))

    def test_valid_manifest(self):
        lab.validate_manifest(self.m, self.root)

    def test_audio_tamper_rejected(self):
        (self.root/'turn-002.pcm').write_bytes(b'\x10\x03' * 1600)
        with self.assertRaisesRegex(ValueError, 'digest'): lab.validate_manifest(self.m,self.root)

    def test_path_traversal_rejected(self):
        self.m['turns'][0]['file']='../turn-001.pcm'
        with self.assertRaisesRegex(ValueError, 'filename'): lab.validate_manifest(self.m,self.root)

    def test_symlink_rejected(self):
        p=self.root/'turn-001.pcm'; p.rename(self.root/'original.pcm'); p.symlink_to(self.root/'original.pcm')
        with self.assertRaisesRegex(ValueError, 'escaped'): lab.validate_manifest(self.m,self.root)

    def test_wrong_rate_rejected(self):
        self.m['sampleRate']=24000
        with self.assertRaisesRegex(ValueError, '16 kHz'): lab.validate_manifest(self.m,self.root)

    def test_long_profile_cannot_be_compressed(self):
        self.m['profile']='20m'
        with self.assertRaisesRegex(ValueError, 'duration'): lab.validate_manifest(self.m,self.root)

    def test_no_silent_speech_pass(self):
        p=self.root/'turn-001.pcm'; p.write_bytes(bytes(3200)); self.m['turns'][0]['sha256']=lab.digest(p)
        with self.assertRaisesRegex(ValueError, 'silent'): lab.validate_manifest(self.m,self.root)

    def receipt(self):
        return dict(status='PASS',profile='short',identity={'source':'a','executable':'b'},device='iphone',
                    accountHash='account',createdAt=time.time(),consumed=False)

    def test_current_device_short_required(self):
        r=self.receipt()
        lab.validate_receipt(r,r['identity'],'iphone','account')
        for changes in ({'status':'FAIL'},{'profile':'local'},{'consumed':'another-run'},
                        {'createdAt':time.time()-4000},{'device':'simulator'},
                        {'createdAt':time.time()+60},
                        {'identity':{'source':'changed'}},{'accountHash':'different'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                lab.validate_receipt(dict(r,**changes),r['identity'],'iphone','account')

    def build_fixture(self):
        repo, stage = self.root/'repo', self.root/'stage'
        for p in (repo, stage):
            (p/'DreamJourney').mkdir(parents=True)
            (p/'DreamJourney/Main.swift').write_text('original app source')
        app=stage/'DerivedData/Build/Products/Debug-iphoneos/DreamJourney.app'
        app.mkdir(parents=True)
        (app/'DreamJourney').write_bytes(b'unchanging debug launcher')
        (app/'DreamJourney.debug.dylib').write_bytes(b'actual app code v1')
        (app/'Frameworks').mkdir()
        (app/'Frameworks/Provider').write_bytes(b'provider implementation')
        (app/'Info.plist').write_bytes(b'original runtime configuration')
        lab.save(stage/'lab-build.json', dict(schema=2, repository=str(repo),
                 source=lab.fingerprint(repo), stagedSource=lab.fingerprint(stage),
                 tool=lab.tool_fingerprint()))
        lab.save(stage/'binary.json', dict(schema=2, signed=True,
                 executable=lab.digest(app/'DreamJourney'), bundle=lab.bundle_fingerprint(app)))
        return stage, app

    def test_real_code_and_configuration_tamper_rejected_with_same_launcher(self):
        stage, app=self.build_fixture()
        identity, _=lab.validate_build(stage)
        for name in ('DreamJourney.debug.dylib', 'Frameworks/Provider', 'Info.plist'):
            target=app/name; original=target.read_bytes()
            target.write_bytes(original+b' changed')
            self.assertEqual(lab.digest(app/'DreamJourney'), identity['executable'])
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'bundle changed'):
                lab.validate_build(stage)
            target.write_bytes(original)

    def test_modified_tool_or_staged_code_invalidates_build(self):
        stage, _=self.build_fixture()
        with patch.object(lab,'tool_fingerprint',return_value='changed tool'):
            with self.assertRaisesRegex(ValueError, 'tool changed'):
                lab.validate_build(stage)
        (stage/'DreamJourney/Main.swift').write_text('locally bypassed assertion')
        with self.assertRaisesRegex(ValueError, 'staged source changed'):
            lab.validate_build(stage)

    def test_legacy_launcher_only_identity_cannot_be_reused(self):
        stage, app=self.build_fixture()
        lab.save(stage/'binary.json',dict(signed=True,executable=lab.digest(app/'DreamJourney')))
        with self.assertRaisesRegex(ValueError, 'complete app bundle identity'):
            lab.validate_build(stage)

    def test_missing_short_gate_stops_before_any_device_operation(self):
        stage, _=self.build_fixture()
        self.m.update(profile='20m',minimumDurationSeconds=1200,maximumDurationSeconds=3600)
        first=self.m['turns'][0]
        for i in range(3,111):
            name=f'turn-{i:03d}.pcm'
            (self.root/name).write_bytes((self.root/first['file']).read_bytes())
            self.m['turns'].append(dict(first,ordinal=i,file=name))
        lab.save(self.root/'manifest.json',self.m)
        args=SimpleNamespace(run=str(self.root),stage=str(stage),device='iphone',
                             current_test_account=True,short_receipt=None)
        with patch.object(lab,'call') as device_call:
            with self.assertRaisesRegex(ValueError,'missing --short-receipt'):
                lab.run(args)
            device_call.assert_not_called()
        self.assertFalse((self.root/'launched.json').exists())

    def test_bundle_external_code_link_rejected(self):
        stage, app=self.build_fixture()
        outside=self.root/'external-code'; outside.write_bytes(b'unbound code')
        (app/'Frameworks/External').symlink_to(outside)
        with self.assertRaisesRegex(ValueError,'symlink escaped'):
            lab.validate_build(stage)

    def test_ambiguous_source_hook_rejected(self):
        with self.assertRaises(ValueError): replace_once('anchor anchor','anchor','new')
        with self.assertRaises(ValueError): replace_once('changed','anchor','new')

    def test_stale_report_not_accepted(self):
        result=self.root/'result.json'
        rows=[dict(status='PASS',mode='preflight',stage='preflight',launchID='old'),
              dict(status='PASS',mode='run',stage='done',launchID='old'),
              dict(status='FAIL',mode='run',stage='failed',launchID='current')]
        def receive(*args): lab.save(result, rows.pop(0))
        with patch.object(lab,'copy_from',side_effect=receive),patch.object(lab.time,'sleep'):
            actual=lab.wait_result('iphone','remote',result,30,'run','current')
        self.assertEqual(actual['status'],'FAIL')

    def test_recovery_observation_does_not_end_at_first_error_or_erase_it(self):
        result=self.root/'result.json'
        rows=[dict(status='RECOVERING',conversationStatus='FAIL',recoveryStatus='OBSERVING',failure='frameGap',mode='run',launchID='current'),
              dict(status='FAIL',conversationStatus='FAIL',recoveryStatus='PASS',failure='frameGap',mode='run',launchID='current')]
        def receive(*args): lab.save(result, rows.pop(0))
        with patch.object(lab,'copy_from',side_effect=receive),patch.object(lab.time,'sleep'):
            actual=lab.wait_result('iphone','remote',result,30,'run','current')
        self.assertEqual(actual['recoveryStatus'],'PASS')
        self.assertEqual(actual['conversationStatus'],'FAIL')
        self.assertEqual(actual['failure'],'frameGap')
        self.assertFalse(rows)

    def test_actual_swift_observation_policy_has_fixed_timeout_and_partial_states(self):
        import subprocess
        source=(Path(__file__).parent/'ios/LiveDeviceLabRuntime.swift').read_text()
        policy=source.split('// LAB_RECOVERY_POLICY_BEGIN')[1].split('// LAB_RECOVERY_POLICY_END')[0]
        checks='''
assert(LiveDeviceLabRecoveryPolicy.outcome(now: 1, deadline: 10, publication: nil, partial: false) == nil)
assert(LiveDeviceLabRecoveryPolicy.outcome(now: 1, deadline: 10, publication: "noChange", partial: false) == "NO_CHANGE")
assert(LiveDeviceLabRecoveryPolicy.outcome(now: 10, deadline: 10, publication: nil, partial: false) == "OBSERVATION_TIMEOUT")
assert(LiveDeviceLabRecoveryPolicy.outcome(now: 2, deadline: 10, publication: "published", partial: true) == "PARTIAL")
assert(LiveDeviceLabRecoveryPolicy.outcome(now: 2, deadline: 10, publication: "published", partial: false) == "PASS")
assert(LiveDeviceLabRecoveryPolicy.outcome(now: 2, deadline: 10, publication: "failed", partial: false) == "FAIL")
assert(LiveDeviceLabRecoveryPolicy.duration(wallCap: 1200, leaseWait: 20, repairWait: 60, requestDeadline: 90, requests: 2, absoluteRemaining: 100, pollInterval: 2) == 182)
assert(LiveDeviceLabRecoveryPolicy.duration(wallCap: 120, leaseWait: 20, repairWait: 60, requestDeadline: 90, requests: 2, absoluteRemaining: 100, pollInterval: 2) == 120)
assert(LiveDeviceLabRecoveryPolicy.duration(wallCap: 120, leaseWait: 0, repairWait: 0, requestDeadline: 90, requests: 0, absoluteRemaining: 100, pollInterval: 2) == 2)
'''
        path=self.root/'recovery-policy.swift';path.write_text(policy+checks)
        subprocess.run(['swift',str(path)],check=True,capture_output=True,timeout=60)

    def test_no_shell_execution_or_direct_backend(self):
        # Test runner executes only explicit argv and the real app; it has no credential or SQL client.
        with patch.object(lab.subprocess,'run') as run:
            lab.call(['say','a;$(echo unexpected)'])
            self.assertEqual(run.call_args.args[0], ['say','a;$(echo unexpected)'])
            self.assertNotIn('shell',run.call_args.kwargs)


if __name__=='__main__': unittest.main()
