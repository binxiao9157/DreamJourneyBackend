"""Business regressions: optional association cannot erase verified new themes."""
import unittest
from copy import deepcopy
from types import SimpleNamespace as NS
from tests.test_live_theme_capacity import Probe, atom, theme, target
from app.async_effects.owner_truth_live_recovery_worker import RecoveryThemeAssembler
from app.domain.owner_truth.live_topics import digest
from app.services.owner_truth_live_memory_contract_errors import contract_failure


class PublicationFallbackTests(unittest.TestCase):
    def assert_new_pending(self, probe, atoms):
        before=deepcopy(probe.targets)
        out=probe.run(atoms)
        self.assertEqual(len(out[0]),1)
        self.assertEqual(set(out[0][0].atom_ids),{a['atomId'] for a in atoms})
        self.assertEqual(out[0][0].summary,theme(atoms).summary)
        self.assertEqual(out[1],{})  # No merge into an unchecked historical target.
        self.assertEqual(out[2],())  # Nothing omitted for mere relation uncertainty.
        self.assertEqual(out[4],{})  # No formal-memory correction write.
        self.assertEqual(probe.targets,before)
        self.assertTrue(probe.deferred_relations)
        return out

    def test_short_uncertain_keeps_supported_current_theme(self):
        self.assert_new_pending(Probe([target()],lambda m:('uncertain',[],{})),[atom(1),atom(2)])

    def test_long_uncertain_keeps_members_without_per_sentence_cards(self):
        p=Probe([target()],lambda m:('uncertain',[],{}))
        self.assert_new_pending(p,[atom(i) for i in range(97)])
        self.assertLessEqual(len(p.calls),2)  # Stop optional work once merge is undecidable.

    def test_two_historical_targets_do_not_block_new_theme(self):
        p=Probe([target(key='one'),target(key='two')],lambda m:('supplement',[],{}))
        self.assert_new_pending(p,[atom(i) for i in range(97)])

    def test_correction_kind_mismatch_keeps_new_fact_and_old_memory(self):
        old=atom(0,'old');old['content']['memoryKind']='knowledge';old['contentHash']=digest(old['content'])
        p=Probe([target([old],state='accepted')],lambda m:('correction',[],{m['atoms'][0]['atomId']:'old-0'}))
        self.assert_new_pending(p,[atom(1)])

    def test_bad_identity_still_fails_closed(self):
        p=Probe([target()])
        p._call=lambda **kw:(_ for _ in ()).throw(contract_failure('themeValidate','themeRelationTargetMismatch',eligible=True))
        with self.assertRaisesRegex(Exception,'themeRelationTargetMismatch'):p.run([atom(1)])

    def test_exhausted_target_binding_is_not_downgraded(self):
        from app.async_effects.owner_truth_live_recovery_worker import RecoveryThemePageFailed
        for n in (2,97):
            p=Probe([target()])
            p._call=lambda **kw:(_ for _ in ()).throw(RecoveryThemePageFailed('candidateExtraction.live.themeValidate.themeRelationTargetMismatch'))
            with self.assertRaisesRegex(Exception,'themeRelationTargetMismatch'):p.run([atom(i) for i in range(n)])

    def test_optional_relation_budget_does_not_erase_verified_facts(self):
        from app.services.owner_truth_live_long_memory import LiveLongMemoryBudgetExhausted
        for n in (2,97):
            p=Probe([target()]);p._call=lambda **kw:(_ for _ in ()).throw(LiveLongMemoryBudgetExhausted('controlled exhausted'))
            self.assert_new_pending(p,[atom(i) for i in range(n)])

    def test_corrupt_current_evidence_is_never_published_as_fallback(self):
        for n in (2,97):
            p=Probe([target()],lambda m:('uncertain',[],{}));atoms=[atom(i) for i in range(n)]
            atoms[0]['contentHash']=digest('wrong')
            with self.assertRaisesRegex(Exception,'deferredRelationEvidenceMismatch'):p.run(atoms)

    def test_invalid_correction_unit_is_failed_not_cached_or_replayed(self):
        old=atom(0,'old');old['content']['memoryKind']='knowledge';old['contentHash']=digest(old['content'])
        p=Probe([target([old],state='accepted')],lambda m:('correction',[],{m['atoms'][0]['atomId']:'old-0'}))
        unit={'state':'planned'};events=[]
        def fail(**kw):
            self.assertTrue(kw['terminal']);unit.update(state='failed',failureCode=kw['failure_code']);events.append('failed')
        repo=NS(record_unit=lambda plan:unit,snapshot=lambda run:{'attempts':[]},
            reserve_provider_attempt=lambda **kw:events.append('reserved') or 'reservation',
            complete_provider_attempt=lambda *args,**kw:events.append(kw['exposure_state']),record_unit_failure=fail,
            record_unit_result=lambda **kw:self.fail('invalid correction must not be completed'))
        p.host._store.owner_truth_live_long_memory_repository=lambda:repo
        p._call=RecoveryThemeAssembler._call.__get__(p)
        from types import SimpleNamespace as NSLocal
        def run():
            return p.relate_themes(lease=NSLocal(job_id='local-job'),intent=NSLocal(target=NSLocal(vault_id='v',owner_subject_id='o')),
                source=NSLocal(source_id='s',source_metadata={'snapshotRevision':1}),run_id='r',themes=[theme([atom(1)])],catalog=[atom(1)])
        first=run();second=run()
        self.assertEqual(len(first[0]),1);self.assertEqual(len(second[0]),1)
        self.assertEqual(events,['reserved','rejected','failed'])
        self.assertFalse(first[4]);self.assertFalse(second[4])

    def test_new_content_not_only_one_fixed_script(self):
        for text in ('我今年开始学陶艺。','我昨天和姐姐去青岛看海。','我每周三在社区教书法。'):
            with self.subTest(text=text):
                self.assert_new_pending(Probe([target()],lambda m:('uncertain',[],{})),[atom(1,text=text)])


class GroupingFallbackTests(unittest.TestCase):
    def assembler(self, fail_upper=False):
        from app.services.owner_truth_live_theme_provider import DeepSeekLiveThemeProvider
        from app.core.config import Settings
        a=object.__new__(RecoveryThemeAssembler)
        a.provider=DeepSeekLiveThemeProvider(Settings(deepseek_api_key='controlled'))
        a.observed=[]
        def call(**kw):
            atoms=kw['atoms'];proposal=kw.get('proposal');a.observed.append(kw)
            if proposal is None:
                groups=([[x] for x in atoms] if any(x.get('groupingRepair') for x in atoms) else [atoms])
                return dict(schemaVersion='owner-truth-live-theme-v1',inputHash=digest(atoms),omittedAtomIds=[],
                    themes=[dict(key=str(i),title='受控主题',summary='；'.join(x['content'].get('summary','事实') for x in group),
                        atomIds=[x['atomId'] for x in group],evidenceIds=sorted({e for x in group for e in x['evidenceIds']}),dimensions=['dailyLife']) for i,group in enumerate(groups)])
            upper=any(x['atomId'].startswith('verified-theme:') for x in atoms)
            deny=(upper if fail_upper else not any(x.get('groupingRepair') for x in atoms))
            return dict(schemaVersion='owner-truth-live-theme-support-v1',inputHash=digest(atoms),proposalHash=digest(proposal),
                themes=[dict(key=g['key'],atomIds=g['atomIds'],evidenceIds=g['evidenceIds'],
                    verdict='uncertain' if deny else 'supported',sameSubjectEvent=not deny,compatibleTime=True,correctionsResolved=True) for g in proposal['themes']])
        a._call=call
        return a

    def test_distinct_events_regroup_instead_of_disappear(self):
        a=self.assembler();atoms=[atom(1),atom(2,text='我上周在社区教书法。')]
        themes,omitted,blocked=a.organize_pages(lease=None,run_id='r',revision=1,source_id='s',catalog=atoms)
        self.assertEqual(len(themes),2);self.assertEqual(omitted,());self.assertEqual(blocked,())
        self.assertEqual({i for t in themes for i in t.atom_ids},{x['atomId'] for x in atoms})

    def test_upper_reduction_cannot_erase_verified_children(self):
        a=self.assembler(fail_upper=True);atoms=[atom(i) for i in range(70)]
        themes,omitted,blocked=a.organize_pages(lease=None,run_id='r',revision=1,source_id='s',catalog=atoms)
        self.assertEqual({i for t in themes for i in t.atom_ids},{x['atomId'] for x in atoms})
        self.assertLessEqual(len(themes),3);self.assertEqual(omitted,());self.assertEqual(blocked,())

if __name__=='__main__':unittest.main()
