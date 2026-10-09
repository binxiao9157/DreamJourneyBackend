"""S1: opt-in same-session hints, real route/request boundaries and fallback."""
from dataclasses import replace
import unittest
import os
from unittest.mock import patch
from app.core.config import Settings
from app.services.owner_truth_memory_dialogue import asks_for_saved_memory, dialogue_turn_policy
from app.services.echo_public_search import PublicSearchResult, PublicSearchService
from app.services.deepseek import DeepSeekEchoAnswerProxy
from app.services.owner_truth_echo_conversation_context import resolve_owner_truth_retrieval_query
from tests import test_owner_truth_context_authority_api as fixture


class MemoryDialogueCueTests(unittest.TestCase):
    turns = [{'role':'user','text':'我大学备考时喝过咖啡。'},
             {'role':'assistant','text':'你后来停喝了两年。'}]

    def test_followup_uses_user_context_not_model_invention(self):
        for query in ['后来呢？','那是哪一年？','是啊','嗯','为什么呢？']:
            with self.subTest(query=query):
                result=resolve_owner_truth_retrieval_query(query=query,recent_turns=self.turns,memory_dialogue_enabled=True)
                self.assertEqual(result.retrieval_query,self.turns[0]['text'])
                self.assertTrue(result.used_history)

    def test_explicit_new_topic_correction_and_public_query_are_not_overwritten(self):
        for query in ['那我们聊聊植物观察吧','这次我想说照片','然后我开始学游泳了','不是我，是室友','今天是几号？']:
            with self.subTest(query=query):
                result=resolve_owner_truth_retrieval_query(query=query,recent_turns=self.turns,memory_dialogue_enabled=True)
                self.assertEqual(result.retrieval_query,query)
                self.assertFalse(result.used_history)

    def test_missing_user_or_oversized_latest_topic_does_not_revive_old_topic(self):
        for turns in [[],self.turns[1:],self.turns+[{'role':'user','text':'新的照片故事'*100}]]:
            result=resolve_owner_truth_retrieval_query(query='后来呢？',recent_turns=turns,memory_dialogue_enabled=True)
            self.assertEqual(result.retrieval_query,'后来呢？')
            self.assertFalse(result.used_history)

    def test_feature_defaults_off_and_legacy_cue_remains_unchanged(self):
        self.assertFalse(Settings().owner_truth_memory_dialogue_enabled)
        with patch.dict(os.environ, {"OWNER_TRUTH_MEMORY_DIALOGUE_ENABLED":"true"}):
            self.assertTrue(Settings.from_env().owner_truth_memory_dialogue_enabled)
        result=resolve_owner_truth_retrieval_query(query='后来呢？',recent_turns=self.turns)
        self.assertEqual(result.retrieval_query,'后来呢？')

    def test_historical_question_classifier_does_not_block_new_self_reports(self):
        for q in ['我哪年大学毕业？', '我是哪一年大学毕业？', '你还记得我大学读哪里吗？', '我之前说喜欢什么？']:
            self.assertTrue(asks_for_saved_memory(q), q)
        for q in ['我说过大学喝咖啡，最近重新喝了', '我之前说错了，那是室友，不是我', '我现在开始喜欢喝咖啡了']:
            self.assertFalse(asks_for_saved_memory(q), q)

    def test_ambiguous_ack_decline_and_explicit_switch_have_separate_boundaries(self):
        self.assertIn('禁止复述', dialogue_turn_policy('嗯。'))
        self.assertIn('停止展开', dialogue_turn_policy('这件事不想再聊了。'))
        self.assertIn('明确切换话题', dialogue_turn_policy('换个话题，我最近整理照片。'))
        self.assertNotIn('明确切换话题', dialogue_turn_policy('我换了个咖啡杯。'))

    def test_prompt_keeps_formal_facts_and_recent_context_separate(self):
        proxy=DeepSeekEchoAnswerProxy(Settings(owner_truth_memory_dialogue_enabled=True))
        request=proxy.build_request(query='我现在开始喜欢喝咖啡了',generation_context='大学备考时喝过咖啡。',persona_scope='personal',recent_turns=self.turns,memory_retrieval_status='grounded')
        system,user=[m['content'] for m in request['json']['messages']]
        self.assertIn('最多问一个主要问题',system)
        self.assertNotIn('不是在检索用户本人或家人的历史事实',system)
        self.assertIn('【已授权记忆】\n大学备考时喝过咖啡。',user)
        self.assertIn('本次会话最近对话',user)
        off=DeepSeekEchoAnswerProxy(Settings()).build_request(query='我喜欢咖啡',generation_context='',persona_scope='personal')
        self.assertNotIn('memory-dialogue-v3',off['json']['messages'][0]['content'])
        family=proxy.build_request(query='他以前喜欢什么',generation_context='家人已授权事实',persona_scope='family')
        self.assertNotIn('memory-dialogue-v3',family['json']['messages'][0]['content'])


class MemoryDialogueRouteTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixture.OwnerTruthContextAuthorityAPITests()
        self.fixture.setUp(); self.addCleanup(self.fixture.tearDown)
        old=fixture.main_module.settings
        self.addCleanup(setattr,fixture.main_module,'settings',old)
        fixture.main_module.settings=replace(old,owner_truth_memory_dialogue_enabled=True)
        self.owner,self.headers=self.fixture._login('13800139892')
        self.fixture._seed_confirmed_memory(self.owner)
        self.fixture._enable_authenticated_owner_v4(self.owner)

    def call(self,query,unavailable=False):
        payload=self.fixture._payload(self.owner);payload['query']=query
        reader=fixture.main_module.store.owner_truth_memory_search_document_projection_repository()
        original=reader.read
        with patch.object(reader,'read',side_effect=(lambda **kw: None) if unavailable else original), patch.object(DeepSeekEchoAnswerProxy,'request_answer',return_value='是什么时候开始喜欢的？') as provider:
            response=fixture.client.post('/echo/answers',headers=self.headers,json=payload)
        self.assertEqual(response.status_code,200,response.text)
        return response.json(),provider

    def test_new_self_report_reaches_provider_when_no_related_memory(self):
        _,provider=self.call('我现在开始喜欢喝咖啡了')
        provider.assert_called_once()
        self.assertEqual(provider.call_args.kwargs['memory_retrieval_status'],'gap')
        self.assertEqual(provider.call_args.kwargs['generation_context'],'')

    def test_unavailable_search_does_not_stop_new_self_report(self):
        _,provider=self.call('我现在开始喜欢喝咖啡了',True)
        provider.assert_called_once()
        self.assertEqual(provider.call_args.kwargs['memory_retrieval_status'],'fallback')
        self.assertEqual(provider.call_args.kwargs['generation_context'],'')

    def test_unavailable_search_does_not_invent_historical_answer(self):
        result,provider=self.call('我哪年大学毕业？',True)
        provider.assert_not_called()
        self.assertEqual(result['answer']['memoryGrounding']['outcome'],'fallback')

    def test_flag_off_preserves_existing_unavailable_behavior(self):
        fixture.main_module.settings=replace(fixture.main_module.settings,owner_truth_memory_dialogue_enabled=False)
        _,provider=self.call('我现在开始喜欢喝咖啡了',True)
        provider.assert_not_called()

    def test_public_lookup_survives_private_index_unavailable(self):
        fixture.main_module.settings=replace(fixture.main_module.settings,echo_public_search_enabled=True)
        evidence=PublicSearchResult('available','2026-10-08T00:00:00Z')
        with patch.object(PublicSearchService,'lookup',return_value=evidence) as lookup:
            _,provider=self.call('杭州今天天气怎么样？',True)
        lookup.assert_called_once_with('杭州今天天气怎么样？')
        provider.assert_called_once()
        self.assertEqual(provider.call_args.kwargs['generation_context'],'')
        self.assertEqual(provider.call_args.kwargs['public_information'],evidence)
