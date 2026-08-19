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
        self.assertIn("父亲在西交利物浦大学读书", request["json"]["messages"][1]["content"])
        self.assertNotIn("server-secret", str(request["json"]))

    def test_memory_fallback_selects_the_most_relevant_confirmed_memory(self) -> None:
        answer = DeepSeekEchoAnswerProxy.fallback_answer(
            query="我在哪里读大学？",
            generation_context=(
                "[archive] kind=text; title=生活近况; note=我最近睡眠不太好。\n"
                "[archive] kind=text; title=求学经历; note=我的大学是在西交利物浦读的。"
            ),
            persona_scope="personal",
        )

        self.assertEqual(answer, "根据已确认的记忆：我的大学是在西交利物浦读的。")

    def test_memory_fallback_does_not_return_an_unrelated_memory(self) -> None:
        answer = DeepSeekEchoAnswerProxy.fallback_answer(
            query="我小时候最喜欢去哪里？",
            generation_context=(
                "[archive] kind=text; title=生活近况; note=我最近睡眠不太好。"
            ),
            persona_scope="personal",
        )

        self.assertTrue(answer.startswith(DeepSeekEchoAnswerProxy.memory_gap_marker))
        self.assertIn("那我们来聊一聊吧", answer)


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
        self.assertIn("那我们来聊一聊吧", answer["text"])
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
        self.assertIn("那我们来聊一聊吧", answer["text"])
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
