import unittest
from tests.test_live_theme_capacity import Probe, atom, theme, target
from app.async_effects.owner_truth_live_relation_paging import relation_pages
from app.domain.owner_truth.live_topics import SupportedLiveTheme

class PartialProbe(Probe):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.guarded=[];self.deny=False
    def guard_partial_themes(self,**kw):
        self.guarded.append(kw)
        return ((),({'atomIds':list(kw['themes'][0].atom_ids),'reason':'correction'},)) if self.deny else (kw['themes'],())
    def run(self,atoms,extra=()):
        from types import SimpleNamespace as NS
        return self.relate_themes(lease=None,intent=NS(target=NS(vault_id='v',owner_subject_id='o')),
          source=NS(source_id='s',source_metadata={'snapshotRevision':1,'conversationTurns':[{'text':'更正与撤回原文保留'}]}),run_id='r',
          themes=[theme(atoms)]+[theme(a,k) for k,a in extra],catalog=atoms+[a for _,items in extra for a in items])

class RelationContextTests(unittest.TestCase):
    def test_identity_present_in_every_target_page(self):
        atoms=[atom(i) for i in range(113)];base=theme(atoms)
        t=SupportedLiveTheme(base.key,base.title,'另一个独立读书角，不是同一处，也不是旧场所改名。',base.atom_ids,base.evidence_ids,base.dimensions,base.support_hash)
        p=Probe([target(key=str(i)) for i in range(5)])
        pages=relation_pages(p.provider,t,atoms,p.targets)
        self.assertGreater(len(pages),5)
        for m in pages:
            self.assertIn('不是旧场所改名',m['relationContext']['current']['summary'])
            self.assertEqual(m['relationContext']['current']['supportHash'],t.support_hash)
            self.assertLessEqual(len(p.provider.prepare_relation(material=m)[1].body),24000)
    def test_uncertain_subset_keeps_complete_verified_current_card(self):
        p=PartialProbe([target()],lambda m:('uncertain' if any(a['atomId']=='new-96' for a in m['atoms']) else 'none',[],{}))
        out=p.run([atom(i) for i in range(97)])
        self.assertEqual(len(out[0]),1);self.assertEqual(len(out[0][0].atom_ids),97)
        self.assertFalse(out[1]);self.assertFalse(out[2]);self.assertFalse(out[4]);self.assertTrue(p.deferred_relations)
        self.assertFalse(p.guarded)  # No new partial theme or unreviewed summary was created.

    def test_relation_fallback_does_not_rerun_current_fact_safety(self):
        # Current-scene safety runs before relate_themes in assemble. Optional
        # history comparison must not synthesize another full-context gate.
        p=PartialProbe([target()],lambda m:('uncertain',[],{}));p.deny=True
        out=p.run([atom(i) for i in range(97)])
        self.assertEqual(len(out[0]),1);self.assertFalse(p.guarded);self.assertFalse(out[4])

    def test_all_uncertain_is_explicitly_deferred_not_verified_none(self):
        p=PartialProbe([target()],lambda m:('uncertain',[],{}));out=p.run([atom(i) for i in range(97)])
        self.assertEqual(len(out[0]),1);self.assertTrue(p.deferred_relations)
        self.assertFalse(out[1]);self.assertFalse(out[3]);self.assertFalse(out[4])

    def test_positive_and_uncertain_do_not_silently_modify_old_target(self):
        p=PartialProbe([target()],lambda m:('uncertain' if any(a['atomId']=='new-96' for a in m['atoms']) else 'supplement',[],{}))
        out=p.run([atom(i) for i in range(97)])
        self.assertEqual(len(out[0][0].atom_ids),97);self.assertFalse(out[1]);self.assertFalse(out[4])
        self.assertTrue(p.deferred_relations)
if __name__=='__main__':unittest.main()
