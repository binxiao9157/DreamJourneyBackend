"""ASR differences must not control liveness or authorize confirmation."""
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
import unittest
import lab
import test_lab


class ASRReviewTests(unittest.TestCase):
    def test_actual_swift_turn_and_final_durable_policy(self):
        source = (Path(__file__).parent/'ios/LabEvidence.swift').read_text()
        checks = r'''
enum LiveDeviceLabError: Error { case check(String) }
func ready(_ text: String, _ display: String? = nil, _ audio: Bool = true, _ listening: Bool = true, _ assistant: String = "好的") -> Bool {
    LiveLabTurnCompletion.ready(finalText: text, displayedUser: display ?? text,
        displayedAssistant: assistant, playbackClosed: audio, listeningOrFarewell: listening)
}
// Both homophone and a meaning-changing negation continue; neither is silently accepted.
for text in ["慢慢完成，不敢进度", "慢慢完成，要赶进度"] {
    assert(ready(text))
    assert(LiveLabTurnCompletion.missingTerms(text: text, terms: ["不赶进度"]) == ["不赶进度"])
    var ledger = LiveLabEvidenceLedger()
    ledger.observe(.init(canonicalID: "turn28", text: text, ordinal: 28, requiredTerms: ["不赶进度"]))
    let rows = [LiveLabMessage(canonicalID: "turn28", messageID: "m28", role: "owner", text: text)]
    let differences = try ledger.contentDifferences(rows)
    assert(differences.count == 1 && differences[0]["actualTranscript"] as? String == text)
    assert(differences[0]["canonicalID"] as? String == "turn28")
    do { _ = try ledger.requiredFacts(rows); fatalError("unreviewed content authorized") }
    catch LiveDeviceLabError.check(let code) { assert(code == "asrFactMismatch") }
    // Next turn remains eligible, unlike the old throw/keyword-gated wait.
    assert(ready("下次再聊"))
    // A later legitimate final revision supersedes the earlier observation only by exact durable identity.
    ledger.observe(.init(canonicalID: "turn28", text: "不赶进度", ordinal: 28, requiredTerms: ["不赶进度"]))
    let revised = [LiveLabMessage(canonicalID: "turn28", messageID: "m28", role: "owner", text: "不赶进度")]
    assert(try ledger.contentDifferences(revised).isEmpty)
    assert(try ledger.requiredFacts(revised) == ["不赶进度"])
    do { _ = try ledger.contentDifferences([.init(canonicalID: "other", messageID: "m28", role: "owner", text: text)]); fatalError("wrong identity accepted") }
    catch LiveDeviceLabError.check(let code) { assert(code == "durableObservationUnresolved") }
}
assert(!ready(""))
assert(!ready("正常", "另一轮"))
assert(!ready("正常", nil, false))
assert(!ready("正常", nil, true, false))
assert(!ready("正常", nil, true, true, "  "))
assert(ready("正常。", "正常"))
print("ASR completion, review, revision and identity assertions PASS")
'''
        # Throwing expressions cannot appear inside Swift assert's non-throwing autoclosure.
        checks = checks.replace('assert(try ledger.contentDifferences(revised).isEmpty)',
                                'let clean = try ledger.contentDifferences(revised); assert(clean.isEmpty)')
        checks = checks.replace('assert(try ledger.requiredFacts(revised) == ["不赶进度"])',
                                'let terms = try ledger.requiredFacts(revised); assert(terms == ["不赶进度"])')
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'main.swift';p.write_text(source+'\n'+checks)
            result = subprocess.run(['swift', '-DDEBUG', '-DLIVE_DEVICE_AUTOMATION', str(p)],
                                    capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_host_returns_review_terminal_without_waiting_for_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest=Path(tmp)/'result.json'
            result=dict(status='CONTENT_REVIEW_REQUIRED', mode='run', launchID='current')
            with patch.object(lab, 'copy_from', side_effect=lambda *a: lab.save(dest, result)) as copy:
                self.assertEqual(lab.wait_result('phone', 'remote', dest, 30, 'run', 'current'), result)
                self.assertEqual(copy.call_count, 1)

    def test_review_pending_never_confirms_via_readback_or_issues_short_receipt(self):
        fixture=test_lab.SafetyTests();fixture.setUp()
        try:
            stage, app=fixture.build_fixture()
            fixture.m['organizationTimeoutSeconds']=180
            lab.save(fixture.root/'manifest.json', fixture.m)
            args=SimpleNamespace(run=str(fixture.root),stage=str(stage),device='phone',
                current_test_account=True,short_receipt=None,allow_audible=True)
            pending={'status':'CONTENT_REVIEW_REQUIRED','memoryConfirmationStatus':'REVIEW_REQUIRED'}
            with patch.object(lab,'call') as call, patch.object(lab,'wait_result',return_value=pending) as wait:
                with self.assertRaises(lab.ContentReviewRequired): lab.run(args)
            self.assertEqual(wait.call_count,1)
            self.assertFalse((fixture.root/'short-receipt.json').exists())
            self.assertFalse((fixture.root/'cold-readback.json').exists())
            launches=[c for c in call.call_args_list if 'launch' in c.args[0]]
            self.assertEqual(len(launches),1)
            self.assertIn('"DJ_LIVE_DEVICE_LAB_MODE": "run"', ' '.join(map(str,launches[0].args[0])))
            with self.assertRaises(ValueError):
                lab.validate_receipt(dict(fixture.receipt(),status='CONTENT_REVIEW_REQUIRED'),
                                     fixture.receipt()['identity'],'iphone','account')
        finally:
            fixture.doCleanups()

if __name__ == '__main__': unittest.main()
