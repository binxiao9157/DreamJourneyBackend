import json
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS
from app.core.config import Settings
from app.domain.owner_truth.live_topics import digest, validate_theme_review, SupportedLiveTheme
from app.domain.owner_truth.live_theme_fallback import reuse_scope, private_draft_coverage, eligible_private_themes
from app.async_effects.owner_truth_live_recovery_worker import RecoveryThemeAssembler, RecoveryThemePageFailed
from app.services.owner_truth_live_long_memory import LiveLongMemoryBudgetExhausted
from app.services.owner_truth_live_memory_contract_errors import LiveMemoryContractFailure
from tests.test_live_relation_completion import AssembleProbe

DATA=json.loads((Path(__file__).parent/'fixtures/live_theme_regroup_device.json').read_text())
SCOPE=reuse_scope(run_id='run',vault_id='v',owner_subject_id='o',authority_epoch=1,capture_generation=1)


def recorded_drafts():
    # Real provider proposals and reviews are revalidated; no invented PASS.
    drafts=[]
    for pair in DATA['privatePairs']:
        result=validate_theme_review(atoms=pair['atoms'],proposal=pair['proposal'],review=pair['review'])
        coverage=private_draft_coverage(parent_unit_id=pair['sourceId'],scope=SCOPE,catalog=pair['atoms'],
            themes=result.themes,omitted=result.omitted_atom_ids,blocked=result.blocked)
        drafts.append(dict(id=pair['originalUnitId'],output_hash=digest(coverage),output_coverage=coverage))
    return drafts


class DeviceReplay(RecoveryThemeAssembler):
    def __init__(self):
        super().__init__(NS(_settings=Settings(deepseek_api_key='synthetic')))
        self.private_theme_fallbacks=[];self._verified_private_themes=[];self.repairs=[]
        for draft in recorded_drafts():
            self._verified_private_themes.extend((t,dict(unitId=draft['id'],outputHash=draft['output_hash'],scope=SCOPE))
                for t in eligible_private_themes(coverage=draft['output_coverage'],scope=SCOPE,catalog=DATA['catalog']))
    def _organize_page(self,**kw):
        for pair in DATA['finalPairs']:
            if pair['key']==kw['key'] and pair['proposal']['inputHash']==digest(kw['page']):
                return validate_theme_review(atoms=kw['page'],proposal=pair['proposal'],review=pair['review'])
        self.repairs.append(kw['page'])
        raise RecoveryThemePageFailed('controlledFurtherModelUnavailable')


class ReuseTests(unittest.TestCase):
    def test_device_four_missing_facts_survive_without_weakening_review(self):
        p=DeviceReplay()
        try:
            themes,omitted,_=p.organize_pages(lease=None,run_id='run',revision=1,source_id='source',catalog=DATA['finalCatalog'])
        except LiveMemoryContractFailure as error:
            if not error.code.endswith('noReliableThemes'):raise
            themes,omitted=(),tuple(a['atomId'] for a in DATA['finalCatalog'])
        ids=[a for t in themes for a in t.atom_ids]
        self.assertTrue(set(DATA['missingAtomIds'])<=set(ids))
        initially_valid=set()
        for pair in DATA['finalPairs']:
            if not pair['key'].startswith('0:') or 'regroup' in pair['key']:continue
            members={aid for t in pair['proposal']['themes'] for aid in t['atomIds']}|set(pair['proposal']['omittedAtomIds'])
            page=[a for a in DATA['finalCatalog'] if a['atomId'] in members]
            result=validate_theme_review(atoms=page,proposal=pair['proposal'],review=pair['review'])
            initially_valid.update(aid for t in result.themes for aid in t.atom_ids)
        self.assertTrue(initially_valid<=set(ids))
        self.assertFalse(set(DATA['missingAtomIds']) & set(omitted))
        self.assertEqual(len(ids),len(set(ids)))
        self.assertTrue(p.private_theme_fallbacks)
        for record in p.private_theme_fallbacks:
            self.assertFalse(set(record['atomIds']) & {a['atomId'] for page in p.repairs for a in page})
    def test_whole_private_theme_can_span_final_input_pages(self):
        p=DeviceReplay()
        whole=next((t,ref) for t,ref in p._verified_private_themes if len(t.atom_ids)>1)
        p._verified_private_themes=[whole]
        page=[a for a in DATA['catalog'] if a['atomId'] in whole[0].atom_ids]
        p.provider.pages=lambda atoms:[[a] for a in atoms]
        def reject(**kw):raise RecoveryThemePageFailed('controlledPageFailure')
        p._organize_page=reject
        themes,omitted,blocked=p.organize_pages(lease=None,run_id='run',revision=1,source_id='s',catalog=page)
        self.assertEqual({a for t in themes for a in t.atom_ids},set(whole[0].atom_ids))
        self.assertEqual((len(themes),omitted,blocked),(1,(),()))

    def test_receipt_rejects_stale_scope_content_evidence_and_members(self):
        d=recorded_drafts()[0]['output_coverage'];catalog=deepcopy(DATA['catalog'])
        self.assertTrue(eligible_private_themes(coverage=d,scope=SCOPE,catalog=catalog))
        for key in SCOPE:
            scope=dict(SCOPE);scope[key]=str(scope[key])+'changed'
            self.assertFalse(eligible_private_themes(coverage=d,scope=scope,catalog=catalog),key)
        for field in ('content','evidence','evidenceIds','dimensions','supportState','contentHash'):
            changed=deepcopy(catalog);ids=set(d['themes'][0]['atomIds'])
            for a in changed:
                if a['atomId'] in ids:
                    if field=='content':a[field]['summary']='changed';a['contentHash']=digest(a[field])
                    elif field=='evidence':a[field][0]['text']='changed'
                    elif field in ('evidenceIds','dimensions'):a[field]=['changed']
                    else:a[field]='changed'
            self.assertFalse(eligible_private_themes(coverage=d,scope=SCOPE,catalog=changed),field)
        d=deepcopy(d);d.pop('verifiedReuse')
        self.assertFalse(eligible_private_themes(coverage=d,scope=SCOPE,catalog=catalog))
    def test_whole_group_only_and_hint_does_not_change_binding(self):
        d=next(d['output_coverage'] for d in recorded_drafts() if any(len(t['atomIds'])>1 for t in d['output_coverage']['themes']))
        theme=next(t for t in d['themes'] if len(t['atomIds'])>1);d=deepcopy(d);d['themes']=[theme]
        catalog=deepcopy(DATA['catalog'])
        for a in catalog:a['privateDraftGroups']=[dict(key='hint',title='hint')]
        self.assertEqual(len(eligible_private_themes(coverage=d,scope=SCOPE,catalog=catalog)),1)
        self.assertFalse(eligible_private_themes(coverage=d,scope=SCOPE,catalog=[a for a in catalog if a['atomId']!=theme['atomIds'][0]]))
        p=DeviceReplay();p.private_theme_fallbacks=[]
        p._verified_private_themes=[(eligible_private_themes(coverage=d,scope=SCOPE,catalog=catalog)[0],dict(unitId='d'))]
        self.assertEqual(p._reuse_private_themes(omitted={theme['atomIds'][0]}),([],set()))
    def test_malformed_theme_and_evidence_union_are_ineligible(self):
        original=recorded_drafts()[0]['output_coverage']
        for field,value in [('atomIds',[]),('evidenceIds',[]),('dimensions',['injected']),('supportHash','bad'),('summary','')]:
            d=deepcopy(original)
            for t in d['themes']:t[field]=value
            self.assertFalse(eligible_private_themes(coverage=d,scope=SCOPE,catalog=DATA['catalog']))


class FallbackAssembleProbe(AssembleProbe):
    def __init__(self,**kw):
        super().__init__(failure=False,**kw)
        from app.domain.owner_truth.live_topics import supported_atom_catalog
        catalog=supported_atom_catalog(atoms=self.atoms,turns=self.turns)
        themes=[SupportedLiveTheme(a['atomId'],'test',a['content']['claim'],(a['atomId'],),tuple(a['evidenceIds']),tuple(a['dimensions']),digest(a)) for a in catalog]
        coverage=private_draft_coverage(parent_unit_id='parent',scope=SCOPE,catalog=catalog,themes=themes,omitted=(),blocked=())
        self.drafts=[dict(id='draft',output_hash=digest(coverage),output_coverage=coverage)]
    def fetch(self):
        if "kind='privateThemeDraft'" in self.questions[-1]:return self.drafts
        return super().fetch()
    def organize_pages(self,**kw):return RecoveryThemeAssembler.organize_pages(self,**kw)
    def _organize_page(self,**kw):raise RecoveryThemePageFailed('controlledGroupingFailure')


class AssembleReuseTests(unittest.TestCase):
    def test_even_complete_fallback_runs_full_context_guard(self):
        p=FallbackAssembleProbe();out=p.run_scene()
        self.assertEqual(len(p.created),2);self.assertEqual(len(p.guarded),1)
        self.assertEqual(p.guarded[0]['turns'],p.turns)
        self.assertEqual(out.manifest['omittedAtomIds'],[])
        self.assertEqual(len(out.manifest['privateThemeFallbacks']),2)
        # Reusing an assembler for a later job cannot retain prior receipts.
        p.drafts=[]
        with self.assertRaisesRegex(Exception,'noReliableThemes'):p.run_scene()
        self.assertEqual(p.private_theme_fallbacks,[])
    def test_unsafe_fallback_stays_omitted(self):
        p=FallbackAssembleProbe(deny=True);out=p.run_scene()
        self.assertEqual(len(p.created),1);self.assertEqual(out.manifest['completeness'],'partial')
        self.assertEqual(len(out.manifest['omittedAtomIds']),1)
    def test_guard_budget_authority_database_propagate(self):
        for error in (LiveLongMemoryBudgetExhausted('budget'),RuntimeError('authorityLost'),RuntimeError('databaseFault')):
            p=FallbackAssembleProbe()
            def fail(**kw):raise error
            p.guard_partial_themes=fail
            with self.assertRaises(type(error)):p.run_scene()
            self.assertFalse(p.created)
    def test_durable_draft_hash_mismatch_is_hard_failure(self):
        p=FallbackAssembleProbe();p.drafts[0]['output_hash']='bad'
        with self.assertRaisesRegex(Exception,'privateThemeDraftHashMismatch'):p.run_scene()

if __name__=='__main__':unittest.main()
