"""Synthetic regressions for a large memory archive and a one-sentence Live start."""
import copy
import hashlib
import json
import unittest

from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.formal_memory_conversation_snapshot import (
    FormalMemoryConversationSnapshotError, FormalMemoryConversationSnapshotService,
    bind_provider_role_text,
)
from test_formal_memory_conversation_snapshot import _ProjectionStore, _ready_projection


class LiveProviderPromptBudgetTests(unittest.TestCase):
    def snapshot(self, statements):
        projection = _ready_projection()
        projection['entries'] = [
            {'memoryVersionId': f'version-{i}', 'memoryKind': 'knowledge',
             'content': {'statement': text, 'dimensions': [f'dimension-{i % 5}']}}
            for i, text in enumerate(statements)
        ]
        original = copy.deepcopy(projection)
        snapshot = FormalMemoryConversationSnapshotService(_ProjectionStore(projection)).build(
            context=OwnerTruthCommandContext(vault_id='test', owner_subject_id='test', actor_subject_id='test'))
        self.assertEqual(projection, original)
        return snapshot

    def bind(self, snapshot, **kwargs):
        return bind_provider_role_text(snapshot, system_role='只依据正式事实回答，无依据时明确不知道。',
                                       speaking_style='自然温和。', **kwargs)

    def test_large_archive_one_sentence_start_is_bounded_without_losing_stored_facts(self):
        snapshot = self.snapshot([f'合成事实{i}：我以前喜欢跑步，现在不喜欢。' for i in range(150)])
        before = copy.deepcopy(snapshot)
        bound = self.bind(snapshot)
        self.assertLessEqual(len(bound['providerRoleText'].encode('utf-8')), 8192)
        self.assertGreater(len(bound['coreFacts']), 0)
        self.assertLess(len(bound['coreFacts']), 150)
        self.assertEqual(snapshot, before)
        self.assertEqual(bound['coverage']['omittedFactCount'], 150-len(bound['coreFacts']))
        self.assertTrue(bound['coverage']['truncated'])

    def test_large_individual_fact_does_not_prevent_smaller_later_facts(self):
        bound = self.bind(self.snapshot(['超长完整事实' * 4000, '我不吃花生。']))
        self.assertEqual([f['statement'] for f in bound['coreFacts']], ['我不吃花生。'])
        self.assertNotIn('超长完整事实', bound['providerRoleText'])

    def test_no_fact_fits_but_rules_remain_and_coverage_is_honest(self):
        bound = self.bind(self.snapshot(['超长完整事实' * 4000]))
        self.assertEqual(bound['coreFacts'], [])
        self.assertEqual(bound['coverage']['omittedFactCount'], 1)
        self.assertIn('未提供不表示', bound['providerRoleText'])

    def test_unicode_exact_byte_boundary_and_one_byte_less(self):
        snapshot = self.snapshot(['我不吃花生🙂；说明"A\\B"。'])
        full = self.bind(snapshot)
        exact = len(full['providerRoleText'].encode('utf-8'))
        fits = self.bind(snapshot, max_bytes=exact)
        smaller = self.bind(snapshot, max_bytes=exact-1)
        self.assertEqual(len(fits['coreFacts']), 1)
        self.assertEqual(len(smaller['coreFacts']), 0)
        self.assertLessEqual(smaller['providerRoleByteCount'], exact-1)

    def test_character_limit_is_also_enforced(self):
        snapshot = self.snapshot(['合成事实' * 200] * 10)
        bound = self.bind(snapshot, max_chars=1024)
        self.assertLessEqual(len(bound['providerRoleText']), 1024)
        self.assertLess(len(bound['coreFacts']), 10)

    def test_config_cannot_raise_hard_byte_ceiling(self):
        bound = self.bind(self.snapshot(['事实' * 40] * 150), max_bytes=32768)
        self.assertLessEqual(bound['providerRoleByteCount'], 8192)
        self.assertEqual(bound['providerRoleBudget']['maxBytes'], 8192)

    def test_fixed_rules_cannot_be_silently_truncated(self):
        with self.assertRaises(FormalMemoryConversationSnapshotError) as result:
            bind_provider_role_text(self.snapshot([]), system_role='固定角色' * 4000, speaking_style='自然')
        self.assertEqual(result.exception.code, 'formalMemorySnapshotTooLarge')

    def test_invalid_budget_fails_closed(self):
        for budget in (0, -1):
            with self.subTest(budget=budget), self.assertRaises(FormalMemoryConversationSnapshotError):
                self.bind(self.snapshot([]), max_bytes=budget)

    def test_selected_fact_identity_rows_and_hashes_match_exactly(self):
        snapshot = self.snapshot([f'事实{i}，不是其他人的经历。🙂' for i in range(100)])
        bound = self.bind(snapshot)
        original_by_id = {tuple(f['sourceMemoryVersionIds']): f for f in snapshot['coreFacts']}
        role = bound['providerRoleText']
        lines = role.splitlines()
        start = lines.index('【正式事实数据开始】')
        end = lines.index('【正式事实数据结束】')
        fields = json.loads(lines[start+1].split('：', 1)[1])
        rows = [dict(zip(fields, json.loads(row))) for row in lines[start+2:end]]
        self.assertEqual(len(rows), len(bound['coreFacts']))
        for row, fact in zip(rows, bound['coreFacts']):
            self.assertEqual(row['ref'], fact['ref'])
            self.assertEqual(row['statement'], original_by_id[tuple(fact['sourceMemoryVersionIds'])]['statement'])
        refs = {f['ref'] for f in bound['coreFacts']}
        self.assertEqual(refs, {r for d in bound['dimensionSummaries'] for r in d['supportRefs']})
        self.assertEqual(bound['providerContextHash'], 'sha256:'+hashlib.sha256(role.encode()).hexdigest())
        material = {k:v for k,v in bound.items() if k not in ('generatedAt','contextHash') and not k.startswith('provider')}
        expected = hashlib.sha256(json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        self.assertEqual(bound['contextHash'], 'sha256:'+expected)
        self.assertNotEqual(bound['contextHash'], snapshot['contextHash'])

    def test_repeat_binding_same_budget_is_stable(self):
        snapshot = self.snapshot([f'合成事实{i}' for i in range(150)])
        bound = self.bind(snapshot)
        self.assertEqual(bound, self.bind(bound))
        self.assertEqual(bound, self.bind(snapshot))

    def test_small_archive_keeps_all_complete_facts(self):
        snapshot = self.snapshot(['我很喜欢鱼，但不是最喜欢。', '我过去住在北京，现在已经搬走。'])
        bound = self.bind(snapshot)
        self.assertEqual(bound['coreFacts'], snapshot['coreFacts'])
        self.assertEqual(bound['coverage']['omittedFactCount'], 0)
        self.assertFalse(bound['coverage']['truncated'])


if __name__ == '__main__':
    unittest.main()
