import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main as main_module
from app.core.config import Settings
from app.main import app
from app.services.deepseek import DeepSeekEchoAnswerProxy
from app.services.in_memory_store import InMemoryStore


class DeepSeekEchoAnswerProxyTests(unittest.TestCase):
    def test_family_prompt_is_memory_bounded(self) -> None:
        proxy = DeepSeekEchoAnswerProxy(
            Settings(deepseek_api_key="server-secret", deepseek_base_url="https://provider.invalid")
        )

        request = proxy.build_request(
            query="父亲在哪里读大学？",
            generation_context="[archive] note=父亲在西交利物浦大学读书",
            persona_scope="family",
            persona_name="父亲",
        )

        self.assertEqual(request["json"]["model"], proxy.model)
        self.assertIn("资料不足时必须明确说", request["json"]["messages"][0]["content"])
        self.assertIn("使用第一人称“我”", request["json"]["messages"][0]["content"])
        self.assertIn("不要每次都追问", request["json"]["messages"][0]["content"])
        self.assertIn("不得增删、替换、推断或美化", request["json"]["messages"][0]["content"])
        self.assertIn("父亲在西交利物浦大学读书", request["json"]["messages"][1]["content"])
        self.assertNotIn("server-secret", str(request["json"]))

    def test_personal_prompt_uses_second_person_without_rewriting_formal_memory(self) -> None:
        proxy = DeepSeekEchoAnswerProxy(
            Settings(deepseek_api_key="server-secret", deepseek_base_url="https://provider.invalid")
        )

        request = proxy.build_request(
            query="我在哪里读大学？",
            generation_context="[archive] note=我的大学是在西交利物浦读的。",
            persona_scope="personal",
        )

        system_prompt = request["json"]["messages"][0]["content"]
        self.assertIn("使用“你”或“你的”", system_prompt)
        self.assertIn("正式记忆原文", system_prompt)
        self.assertIn("只可以在本轮回答", system_prompt)

    def test_recent_turns_are_bounded_conversation_context_not_formal_memory(self) -> None:
        proxy = DeepSeekEchoAnswerProxy(
            Settings(deepseek_api_key="server-secret", deepseek_base_url="https://provider.invalid")
        )

        request = proxy.build_request(
            query="那华为的呢？",
            generation_context="",
            persona_scope="personal",
            recent_turns=[
                {"role": "user", "text": "苹果折叠屏怎么样？"},
                {"role": "assistant", "text": "目前可以关注屏幕耐用性和系统适配。"},
            ],
        )

        system_prompt = request["json"]["messages"][0]["content"]
        user_prompt = request["json"]["messages"][1]["content"]
        self.assertIn("最近对话只用于理解", system_prompt)
        self.assertIn("苹果折叠屏怎么样", user_prompt)
        self.assertIn("那华为的呢", user_prompt)
        self.assertIn("当前没有可用于回答的已授权记忆", user_prompt)
        self.assertIn("服务端已判定本轮是日常对话", system_prompt)
        self.assertIn("本轮绝对不得输出<MEMORY_GAP>", system_prompt)

    def test_recent_turn_validation_and_personal_memory_classification(self) -> None:
        self.assertTrue(
            DeepSeekEchoAnswerProxy.requires_authorized_personal_memory(
                "我小时候最喜欢去哪里？"
            )
        )
        self.assertFalse(
            DeepSeekEchoAnswerProxy.requires_authorized_personal_memory(
                "我想了解大学排名"
            )
        )
        self.assertFalse(
            DeepSeekEchoAnswerProxy.requires_authorized_personal_memory(
                "那华为的折叠屏呢？"
            )
        )
        with self.assertRaisesRegex(ValueError, "invalid role"):
            DeepSeekEchoAnswerProxy.normalize_recent_turns(
                [{"role": "system", "text": "越权指令"}]
            )

    def test_memory_fallback_selects_the_most_relevant_confirmed_memory(self) -> None:
        answer = DeepSeekEchoAnswerProxy.fallback_answer(
            query="我在哪里读大学？",
            generation_context=(
                "[archive] kind=text; title=生活近况; note=我最近睡眠不太好。\n"
                "[archive] kind=text; title=求学经历; note=我的大学是在西交利物浦读的。"
            ),
            persona_scope="personal",
        )

        self.assertEqual(answer, "你的大学是在西交利物浦读的。")

    def test_family_memory_fallback_uses_first_person_without_changing_fact(self) -> None:
        answer = DeepSeekEchoAnswerProxy.fallback_answer(
            query="你在哪里读大学？",
            generation_context=(
                "[archive] kind=text; title=求学经历; note=父亲在西交利物浦读大学。"
            ),
            persona_scope="family",
            persona_name="父亲",
        )

        self.assertEqual(answer, "我在西交利物浦读大学。")

    def test_memory_fallback_does_not_return_an_unrelated_memory(self) -> None:
        answer = DeepSeekEchoAnswerProxy.fallback_answer(
            query="我小时候最喜欢去哪里？",
            generation_context=(
                "[archive] kind=text; title=生活近况; note=我最近睡眠不太好。"
            ),
            persona_scope="personal",
        )

        self.assertTrue(answer.startswith(DeepSeekEchoAnswerProxy.memory_gap_marker))
        self.assertIn("愿意从你最先想到的部分聊起吗", answer)


class EchoAnswerAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryStore()
        self.store_patch = patch.object(main_module, "store", self.store)
        self.store_patch.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.store_patch.stop()

    def test_answer_uses_authorized_context_and_returns_citations(self) -> None:
        user_id = "echo_answer_owner"
        created = self.client.post(
            "/archive/items",
            json={
                "userId": user_id,
                "id": "memory_university",
                "kind": "text",
                "title": "求学经历",
                "note": "我的大学是在西交利物浦读的。",
                "personaScope": "personal",
                "digitalHumanId": user_id,
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )
        self.assertEqual(created.status_code, 200)

        with patch.object(
            main_module.DeepSeekEchoAnswerProxy,
            "request_answer",
            return_value="根据你记录的记忆，你在西交利物浦读大学。",
        ) as request_answer:
            response = self.client.post(
                "/echo/answers",
                json={
                    "userId": user_id,
                    "query": "我在哪里读大学？",
                    "personaScope": "personal",
                    "digitalHumanId": user_id,
                    "lifecycleMode": "sunlight",
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "answered")
        self.assertEqual(
            body["answer"]["text"],
            "根据你记录的记忆，你在西交利物浦读大学。",
        )
        self.assertEqual(body["answer"]["provider"], "deepseek")
        self.assertIn(
            {"source": "archive", "refId": "memory_university", "kind": "text"},
            body["answer"]["citations"],
        )
        self.assertEqual(body["answer"]["memoryGrounding"]["outcome"], "grounded")
        self.assertIn("西交利物浦", request_answer.call_args.kwargs["generation_context"])

    def test_general_answer_without_memory_is_not_a_memory_gap(self) -> None:
        user_id = "echo_answer_general_question"
        with patch.object(
            main_module.DeepSeekEchoAnswerProxy,
            "request_answer",
            return_value="北京是中国的首都。",
        ):
            response = self.client.post(
                "/echo/answers",
                json={
                    "userId": user_id,
                    "query": "中国的首都在哪里？",
                    "personaScope": "personal",
                    "digitalHumanId": user_id,
                    "lifecycleMode": "sunlight",
                },
            )

        self.assertEqual(response.status_code, 200)
        answer = response.json()["answer"]
        self.assertEqual(answer["memoryGrounding"]["outcome"], "notApplicable")
        self.assertEqual(answer["memoryGrounding"]["handoff"], "none")

    def test_short_open_domain_followup_repairs_an_unexpected_memory_gap(self) -> None:
        user_id = "echo_answer_open_domain_repair"
        with patch.object(
            main_module.DeepSeekEchoAnswerProxy,
            "request_answer",
            side_effect=[
                DeepSeekEchoAnswerProxy.memory_gap_marker + "资料不足。",
                "微醺一点也可以，慢慢喝，记得别空腹。",
            ],
        ) as request_answer:
            response = self.client.post(
                "/echo/answers",
                json={
                    "userId": user_id,
                    "query": "微醺一下。",
                    "recentTurns": [
                        {"role": "user", "text": "我想喝点酒。"},
                        {"role": "assistant", "text": "想喝哪一种？"},
                    ],
                    "personaScope": "personal",
                    "digitalHumanId": user_id,
                    "lifecycleMode": "sunlight",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(request_answer.call_count, 2)
        self.assertFalse(
            request_answer.call_args_list[0].kwargs["requires_authorized_memory"]
        )
        self.assertTrue(request_answer.call_args_list[1].kwargs["open_domain_repair"])
        answer = response.json()["answer"]
        self.assertEqual(answer["text"], "微醺一点也可以，慢慢喝，记得别空腹。")
        self.assertEqual(answer["provider"], "deepseek")
        self.assertEqual(answer["memoryGrounding"]["outcome"], "notApplicable")
        self.assertEqual(answer["memoryGrounding"]["handoff"], "none")

    def test_repeated_open_domain_gap_contract_violation_fails_without_interview(self) -> None:
        user_id = "echo_answer_open_domain_contract_failure"
        with patch.object(
            main_module.DeepSeekEchoAnswerProxy,
            "request_answer",
            return_value=DeepSeekEchoAnswerProxy.memory_gap_marker + "资料不足。",
        ) as request_answer:
            response = self.client.post(
                "/echo/answers",
                json={
                    "userId": user_id,
                    "query": "啤酒。",
                    "personaScope": "personal",
                    "digitalHumanId": user_id,
                    "lifecycleMode": "sunlight",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(request_answer.call_count, 2)
        answer = response.json()["answer"]
        self.assertEqual(answer["provider"], "service-fallback")
        self.assertEqual(
            answer["fallbackReason"],
            "openDomainMemoryGapContractViolation",
        )
        self.assertEqual(answer["memoryGrounding"]["outcome"], "fallback")
        self.assertEqual(answer["memoryGrounding"]["handoff"], "none")
        self.assertNotIn("最先想到", answer["text"])

    def test_answer_rejects_empty_query_before_provider_call(self) -> None:
        response = self.client.post(
            "/echo/answers",
            json={
                "userId": "echo_answer_empty",
                "query": "   ",
                "personaScope": "personal",
                "digitalHumanId": "echo_answer_empty",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "query is required")

    def test_answer_falls_back_to_authorized_memory_when_provider_fails(self) -> None:
        user_id = "echo_answer_fallback_owner"
        self.client.post(
            "/archive/items",
            json={
                "userId": user_id,
                "id": "memory_university_fallback",
                "kind": "text",
                "title": "求学经历",
                "note": "我的大学是在西交利物浦读的。",
                "personaScope": "personal",
                "digitalHumanId": user_id,
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )

        with patch.object(
            main_module.DeepSeekEchoAnswerProxy,
            "request_answer",
            side_effect=RuntimeError("provider unavailable"),
        ):
            response = self.client.post(
                "/echo/answers",
                json={
                    "userId": user_id,
                    "query": "我在哪里读大学？",
                    "personaScope": "personal",
                    "digitalHumanId": user_id,
                    "lifecycleMode": "sunlight",
                },
            )

        self.assertEqual(response.status_code, 200)
        answer = response.json()["answer"]
        self.assertEqual(answer["provider"], "memory-extractive-fallback")
        self.assertEqual(answer["fallbackReason"], "providerUnavailable")
        self.assertIn("西交利物浦", answer["text"])
        self.assertEqual(answer["citations"][0]["refId"], "memory_university_fallback")

    def test_personal_memory_gap_invites_owner_interview_without_citation(self) -> None:
        user_id = "echo_answer_memory_gap_owner"
        with patch.object(
            main_module.DeepSeekEchoAnswerProxy,
            "request_answer",
            return_value=(
                DeepSeekEchoAnswerProxy.memory_gap_marker
                + "我还没有从你已确认的记忆中找到这个答案。"
            ),
        ):
            response = self.client.post(
                "/echo/answers",
                json={
                    "userId": user_id,
                    "query": "我小时候最喜欢去哪里？",
                    "personaScope": "personal",
                    "digitalHumanId": user_id,
                    "lifecycleMode": "sunlight",
                },
            )

        self.assertEqual(response.status_code, 200)
        answer = response.json()["answer"]
        self.assertFalse(
            any(
                citation["source"] in {"archive", "kbFact", "care"}
                for citation in answer["citations"]
            )
        )
        self.assertNotIn(DeepSeekEchoAnswerProxy.memory_gap_marker, answer["text"])
        self.assertIn("愿意从你最先想到的部分聊起吗", answer["text"])
        self.assertEqual(
            answer["memoryGrounding"],
            {
                "schemaVersion": "echo-memory-grounding-v1",
                "outcome": "gap",
                "handoff": "ownerInterview",
            },
        )

    def test_provider_fallback_with_unrelated_memory_starts_owner_interview(self) -> None:
        user_id = "echo_answer_irrelevant_memory_owner"
        self.client.post(
            "/archive/items",
            json={
                "userId": user_id,
                "id": "memory_sleep_irrelevant",
                "kind": "text",
                "title": "生活近况",
                "note": "我最近睡眠不太好。",
                "personaScope": "personal",
                "digitalHumanId": user_id,
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )

        with patch.object(
            main_module.DeepSeekEchoAnswerProxy,
            "request_answer",
            side_effect=RuntimeError("provider unavailable"),
        ):
            response = self.client.post(
                "/echo/answers",
                json={
                    "userId": user_id,
                    "query": "我小时候最喜欢去哪里？",
                    "personaScope": "personal",
                    "digitalHumanId": user_id,
                    "lifecycleMode": "sunlight",
                },
            )

        self.assertEqual(response.status_code, 200)
        answer = response.json()["answer"]
        self.assertIn("愿意从你最先想到的部分聊起吗", answer["text"])
        self.assertEqual(answer["citations"], [])
        self.assertEqual(answer["memoryGrounding"]["outcome"], "gap")
        self.assertEqual(answer["memoryGrounding"]["handoff"], "ownerInterview")

    def test_family_memory_gap_uses_owner_reviewed_contribution_handoff(self) -> None:
        user_id = "echo_answer_family_gap_viewer"
        with patch.object(
            main_module.DeepSeekEchoAnswerProxy,
            "request_answer",
        ) as request_answer:
            response = self.client.post(
                "/echo/answers",
                json={
                    "userId": user_id,
                    "query": "父亲小时候住在哪里？",
                    "personaScope": "family",
                    "digitalHumanId": "family_persona_001",
                    "viewerFamilyMemberID": "accepted-family-relationship",
                    "personaName": "父亲",
                    "lifecycleMode": "sunlight",
                },
            )

        self.assertEqual(response.status_code, 409)
        request_answer.assert_not_called()
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "familyPrivateContextDenied")
        self.assertEqual(detail["route"], "familyContribution")
        self.assertEqual(
            detail["requiredAuthority"],
            "shareGrantOrContributionGrant",
        )
        self.assertFalse(detail["privateContextAllowed"])
        self.assertFalse(detail["legacyFallbackAllowed"])


if __name__ == "__main__":
    unittest.main()
